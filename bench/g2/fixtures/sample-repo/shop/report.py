"""Plain-text receipts."""

from shop.pricing import line_total


def receipt(cart):
    rows = [f"{sku} x{qty}: {line_total(sku, qty):.2f}" for sku, qty in cart.lines]
    rows.append(f"TOTAL: {cart.total():.2f}")
    return "\n".join(rows)
