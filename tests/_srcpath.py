"""Put this checkout's `src/` on the import path — and NOTHING else.

`_support` does the same and then runs the provisioning gate (`_provisioning`), which REFUSES
(`os._exit(2)`) in an interpreter without PyYAML and mokata's console scripts. That is right for the
unit suite and fatal for the IDE browser e2e, which runs in a slim interpreter by design (ci.yml's
`ide-e2e` job: playwright only). A helper module the e2e flows import must therefore reach `src/`
through THIS file, never through `_support`; `tests/test_ci_fix_e2e_helpers_skip_the_gate.py` derives
that set and holds it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
