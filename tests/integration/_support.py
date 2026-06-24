"""Integration-suite support shim.

The integration suite is its own independently-discoverable package
(`python -m unittest discover -s tests/integration -t tests/integration`), so it carries
its own support module rather than relying on the unit suite's top-level dir.

It (a) puts `src/` on the import path so `mokata` imports, and (b) reuses the unit suite's
sample helpers from `tests/_support.py` — loaded under a distinct module name so this
same-named shim never imports itself.
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# (a) make the package under test importable.
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

# (b) reuse tests/_support.py helpers without a name collision.
_spec = importlib.util.spec_from_file_location(
    "_mokata_unit_support", os.path.join(_HERE, "..", "_support.py"))
_parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parent)

write_sample_repo = _parent.write_sample_repo
sample_manifest_data = _parent.sample_manifest_data
