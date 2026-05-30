import os
import re
from pathlib import Path

UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", "/data/uploads"))


def validate_tenant_id(tenant_id: str) -> str:
    if not tenant_id or not re.match(r"^[a-zA-Z0-9_-]{1,64}$", tenant_id):
        raise ValueError("Invalid tenant id")
    return tenant_id


def validate_job_object_id(job_id: str) -> None:
    if not job_id or not re.match(r"^[a-f0-9]{24}$", job_id):
        raise ValueError("Invalid job id")


def job_raw_dir(tenant_id: str, job_id: str) -> Path:
    validate_tenant_id(tenant_id)
    validate_job_object_id(job_id)
    base = UPLOAD_ROOT.resolve()
    target = (UPLOAD_ROOT / tenant_id / job_id).resolve()
    if not str(target).startswith(str(base)) or target == base:
        raise ValueError("Invalid storage path")
    return target


def save_job_upload(tenant_id: str, job_id: str, content: bytes, suffix: str) -> str:
    d = job_raw_dir(tenant_id, job_id)
    d.mkdir(parents=True, exist_ok=True)
    safe_suffix = suffix if re.match(r"^\.[a-zA-Z0-9]{1,8}$", suffix) else ".bin"
    path = d / f"source{safe_suffix}"
    path.write_bytes(content)
    return str(path)
