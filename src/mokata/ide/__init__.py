"""`mokata ide` — the read-only review window (0.0.22 E2; doc 85 §1b is its ADR).

    snapshots   per-write content snapshots: "diff at any point in time"
    diffs       live diff (incl. uncommitted), snapshot-to-snapshot, commits/branches, PRs
    sessions    who wrote what: sessions, their runs, their timelines
    demo        a clearly-labelled SAMPLE dataset so the UI is reviewable before real data exists
    server      the localhost Starlette app (imports the `[ide]` deps lazily)
    launch      start it, or re-focus a running one on a session (`/mokata:review-window`)

Everything here READS the repo and mokata's record; the only writes are the snapshot store and the
server's own record, both under `.mokata/temp_local/`.
"""
