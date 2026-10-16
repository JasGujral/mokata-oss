"""Unit prices and line totals."""

PRICES = {"apple": 0.5, "pear": 0.75, "melon": 3.0, "plum": 0.1}


def unit_price(sku):
    return PRICES[sku]


def line_total(sku, qty):
    return unit_price(sku) * qty
