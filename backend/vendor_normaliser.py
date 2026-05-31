"""Vendor normalisation: collapse messy OCR merchant strings to a canonical vendor.

EasyOCR yields "WALMART SUPERCENTER", "Walmart #4821", "WAL-MART", "walmart" for the
same store. Left raw, top-merchant/vendor analytics splits one merchant into many and
totals are wrong. We fuzzy-match each raw name against known vendor aliases (per
tenant); on a strong match we reuse that vendor, otherwise we create a new vendor and
flag it for human review.

Two resolvers share one pure matcher:
  - ``resolve_vendor_sync``  — PyMongo, called by the Celery worker's line-item writer.
  - ``resolve_vendor_async`` — Motor, called by the API's on-demand ``/itemize``.

Resolution happens once per receipt (all its line items share the merchant), so a
per-receipt collection scan over a bounded alias list is cheap. A Redis cache layer is
intentionally deferred (see docs/CODEBASE_IMPROVEMENTS.md Priority 5) to avoid coupling
the sync worker and async API to a shared cache client; it can be added behind these
functions without changing callers.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# 0–100; at/above this two names are treated as the same vendor.
SIMILARITY_THRESHOLD = 85
# Receipts usually lead with the brand ("WALMART #4821", "WALMART SUPERCENTER"), so a
# strong match on the leading token is a reliable same-vendor signal even when the
# full strings diverge. Guarded by a min length to avoid merging on tiny/generic tokens.
BRAND_RATIO = 90
BRAND_MIN_LEN = 4
BRAND_MATCH_SCORE = 90
# Cap the alias scan so a pathological tenant can't load an unbounded list.
MAX_VENDORS_SCANNED = 5000

Resolved = Tuple[str, str]  # (vendor_id, canonical_name)


def normalise_name(raw_name: Optional[str]) -> Optional[str]:
    """Canonical comparison form: trimmed, collapsed whitespace, upper-cased."""
    if not raw_name or not raw_name.strip():
        return None
    return " ".join(raw_name.split()).upper()


def _alias_pairs(vendors: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    """Flatten vendor docs into (alias_upper, vendor_id, canonical_name) tuples."""
    pairs: List[Tuple[str, str, str]] = []
    for v in vendors:
        vid = str(v["_id"])
        canonical = v.get("canonical_name", "")
        for alias in v.get("aliases", []):
            if alias:
                pairs.append((alias.upper(), vid, canonical))
    return pairs


def _pair_score(a: str, b: str) -> float:
    """Similarity of two normalised names. token_set_ratio handles subset/extra-word
    cases ("WALMART" ⊂ "WALMART SUPERCENTER"); a leading-brand-token check rescues
    variants that share only the brand ("WALMART #4821" vs "WALMART SUPERCENTER")."""
    score = float(fuzz.token_set_ratio(a, b))
    if score < SIMILARITY_THRESHOLD:
        ta, tb = a.split(), b.split()
        if ta and tb and len(ta[0]) >= BRAND_MIN_LEN and len(tb[0]) >= BRAND_MIN_LEN:
            if fuzz.ratio(ta[0], tb[0]) >= BRAND_RATIO:
                score = max(score, float(BRAND_MATCH_SCORE))
    return score


def best_match(
    normalised: str, alias_pairs: List[Tuple[str, str, str]]
) -> Optional[Resolved]:
    """Pure fuzzy match: return (vendor_id, canonical) at/above threshold, else None."""
    if not normalised or not alias_pairs:
        return None
    best: Optional[Resolved] = None
    best_score = 0.0
    for alias, vendor_id, canonical in alias_pairs:
        score = _pair_score(normalised, alias)
        if score > best_score:
            best_score = score
            best = (vendor_id, canonical)
    if best is not None and best_score >= SIMILARITY_THRESHOLD:
        return best
    return None


def _new_vendor_doc(raw_name: str, normalised: str, tenant_id: str) -> Dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "canonical_name": raw_name.strip(),
        "aliases": [normalised],
        "category_default": None,
        "needs_review": True,  # an unseen vendor; a human can confirm/merge it later
        "created_at": datetime.now(timezone.utc),
    }


def resolve_vendor_sync(db, raw_name: Optional[str], tenant_id: str) -> Optional[Resolved]:
    """PyMongo resolver for the Celery worker. Returns (vendor_id, canonical) or None."""
    normalised = normalise_name(raw_name)
    if normalised is None:
        return None

    vendors = list(
        db["vendors"]
        .find({"tenant_id": tenant_id}, {"_id": 1, "canonical_name": 1, "aliases": 1})
        .limit(MAX_VENDORS_SCANNED)
    )
    match = best_match(normalised, _alias_pairs(vendors))
    if match:
        return match

    result = db["vendors"].insert_one(_new_vendor_doc(raw_name, normalised, tenant_id))
    logger.info(
        "new vendor created tenant=%s vendor_id=%s (flagged for review)",
        tenant_id,
        result.inserted_id,
    )
    return str(result.inserted_id), raw_name.strip()


async def resolve_vendor_async(
    vendors_coll, raw_name: Optional[str], tenant_id: str
) -> Optional[Resolved]:
    """Motor resolver for the API. Returns (vendor_id, canonical) or None."""
    normalised = normalise_name(raw_name)
    if normalised is None:
        return None

    vendors = await vendors_coll.find(
        {"tenant_id": tenant_id}, {"_id": 1, "canonical_name": 1, "aliases": 1}
    ).to_list(length=MAX_VENDORS_SCANNED)
    match = best_match(normalised, _alias_pairs(vendors))
    if match:
        return match

    result = await vendors_coll.insert_one(
        _new_vendor_doc(raw_name, normalised, tenant_id)
    )
    logger.info(
        "new vendor created tenant=%s vendor_id=%s (flagged for review)",
        tenant_id,
        result.inserted_id,
    )
    return str(result.inserted_id), raw_name.strip()


async def confirm_vendor_alias(
    vendors_coll, vendor_id: str, tenant_id: str, new_alias: Optional[str] = None
) -> bool:
    """Mark a vendor reviewed and optionally fold in another alias. Tenant-scoped so a
    caller can't confirm another tenant's vendor. Returns True if a vendor was updated."""
    try:
        oid = ObjectId(vendor_id)
    except Exception:
        return False

    update: Dict[str, Any] = {"$set": {"needs_review": False}}
    normalised = normalise_name(new_alias)
    if normalised:
        update["$addToSet"] = {"aliases": normalised}

    result = await vendors_coll.update_one(
        {"_id": oid, "tenant_id": tenant_id}, update
    )
    return result.matched_count > 0
