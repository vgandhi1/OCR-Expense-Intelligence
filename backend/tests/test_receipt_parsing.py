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
