def flatten(items, depth=None):
    out = []
    for x in items:
        if isinstance(x, (list, tuple)) and (depth is None or depth > 0):
            out.extend(flatten(x, None if depth is None else depth - 1))
        else:
            out.append(x)
    return out
