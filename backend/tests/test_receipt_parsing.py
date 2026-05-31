"""Unit tests for line-item extraction from OCR text."""

from receipt_parsing import find_line_items

WALMART_TEXT = (
    "WALMART\nSupercenter #1234\n04/15/2026\n"
    "Eggs\n3.49\nMilk\n2.99\nBread\n2.50\n"
    "SUBTOTAL\n8 . 98\nTAX\n0 . 85\nTOTAL\n47.83"
)

STARBUCKS_TEXT = (
    "STARBUCKS\nCOFFEE\nStore\n0456\n04/18/2026\n"
    "Latte\n5 .25\nMuffin\n1.50\nTOTAL\n6.75"
)


def test_walmart_items_extracted_excluding_totals():
    items = find_line_items(WALMART_TEXT)
    pairs = [(i["description"], i["amount"]) for i in items]
    assert pairs == [("Eggs", 3.49), ("Milk", 2.99), ("Bread", 2.50)]
    # totals/tax must NOT appear as products
    descs = [d.lower() for d, _ in pairs]
    assert not any(k in descs for k in ("subtotal", "tax", "total"))


def test_starbucks_items_with_spaced_price():
    items = find_line_items(STARBUCKS_TEXT)
    assert [(i["description"], i["amount"]) for i in items] == [
        ("Latte", 5.25),
        ("Muffin", 1.50),
    ]


def test_each_item_has_qty_default():
    items = find_line_items(WALMART_TEXT)
    assert all(i["qty"] == 1 for i in items)


def test_empty_text_returns_empty_list():
    assert find_line_items("") == []
    assert find_line_items(None) == []


def test_lines_without_prices_yield_no_items():
    assert find_line_items("WALMART\nThank you\nHave a nice day") == []


def test_most_expensive_is_identifiable():
    items = find_line_items(WALMART_TEXT)
    top = max(items, key=lambda i: i["amount"])
    assert top["description"] == "Eggs" and top["amount"] == 3.49


# --- total-leak filtering (the "most expensive item is actually the total" bug) ---

def test_item_above_total_is_dropped():
    # "Total" mangled by OCR so the keyword filter misses it; its amount (840.64)
    # exceeds the real total (40), so it must be filtered out.
    text = "CAFE\nFalafel\n6.40\nTota] ;\n840.64"
    items = find_line_items(text, total=40.0)
    descs = [i["description"] for i in items]
    assert "Tota] ;" not in descs
    assert all(i["amount"] <= 40.0 for i in items)


def test_total_equal_amount_dropped_when_other_items_present():
    # A line equal to the grand total alongside real items is the leaked total.
    text = "STORE\nApple\n3.46\nBanana\n1.92\nTEND\n28.14"
    items = find_line_items(text, total=28.14)
    amounts = [i["amount"] for i in items]
    assert 28.14 not in amounts
    assert amounts == [3.46, 1.92]


def test_single_item_equal_to_total_is_kept():
    # With only one item, an amount equal to the total is a legitimate purchase.
    text = "KIOSK\nCoffee\n4.00"
    items = find_line_items(text, total=4.00)
    assert items == [{"description": "Coffee", "amount": 4.00, "qty": 1}]


def test_single_letter_descriptions_dropped():
    text = "STORE\nF\n3.46\nMilk\n2.99"
    items = find_line_items(text)
    assert [i["description"] for i in items] == ["Milk"]


def test_total_filter_noop_without_total():
    # Backward-compatible: omitting total leaves items untouched.
    items = find_line_items(WALMART_TEXT)
    assert [i["description"] for i in items] == ["Eggs", "Milk", "Bread"]
