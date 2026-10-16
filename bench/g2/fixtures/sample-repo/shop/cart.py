"""A shopping cart."""

from shop.pricing import line_total


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, sku, qty=1):
        self.lines.append((sku, qty))

    def total(self):
        return sum(line_total(sku, qty) for sku, qty in self.lines)
