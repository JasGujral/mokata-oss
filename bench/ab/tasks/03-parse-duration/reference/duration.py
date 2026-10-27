import re

_PART = re.compile(r"(\d+)([hms])")
_UNIT = {"h": 3600, "m": 60, "s": 1}


def parse_duration(s):
    if not isinstance(s, str) or not s or not re.fullmatch(r"(\d+[hms])+", s):
        raise ValueError(f"bad duration: {s!r}")
    return sum(int(n) * _UNIT[u] for n, u in _PART.findall(s))
