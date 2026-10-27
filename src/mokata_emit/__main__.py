"""`python -m mokata_emit <sub> …`, the drainer (`python <this file> --drain <spool>`) and the
recall-graph warmer (`--warm-recall-graph <root>`) and the briefing-structure refresher
(`--warm-briefing-structure <root>`), both 0.0.26 O12.

Run BY PATH for the drainer, `sys.path[0]` is this package's directory; `_drainer_env` puts its
parent on PYTHONPATH so `mokata_emit` and `mokata` import from the same place.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import sys

if __name__ == "__main__":
    if sys.path and sys.path[0] and sys.path[0].rstrip("/\\").endswith("mokata_emit"):
        sys.path.pop(0)          # run by path: never import siblings of this file as top-level
    if len(sys.argv) > 1 and sys.argv[1] == "--warm-recall-graph":
        # 0.0.26 O12 — the per-prompt recall's graph lookups, run detached off the prompt path.
        from mokata.memory.graph_anchors import warm_main
        raise SystemExit(warm_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh-briefing":
        # 0.0.26 O12b — the SessionStart briefing's slow lines, re-derived off the hook.
        from mokata.briefing_cache import refresh_main
        raise SystemExit(refresh_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--warm-briefing-structure":
        # 0.0.26 O12 — the SessionStart briefing's structure line, walked off session start.
        from mokata.knowledge.structure_cache import warm_main as _structure
        raise SystemExit(_structure(sys.argv[2:]))
    from mokata_emit import main
    raise SystemExit(main())
