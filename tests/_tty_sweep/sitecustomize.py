"""The only hook that runs before the first test module is imported.

Kept to two useful lines: the instrument itself is `tests/_tty_prompt_sweep.py`, where it can be
read and reviewed like any other file, rather than hidden in a directory whose name Python happens
to look at."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _tty_prompt_sweep  # noqa: E402,F401  — imported for its side effects, under TTYSWEEP=1
