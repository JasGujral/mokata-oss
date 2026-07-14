---
name: docs
description: mokata · The docs pointer — list the published documentation topics with their site URLs, or resolve one topic to its URL. Read-only, never fetches.
argument-hint: "[topic]"
allowed-tools: Bash, Read
---

# mokata · docs (point at the published docs site)

Point the user at mokata's **published documentation site**
(<https://mokata.ai/>). With **no argument** it prints the list of top-level
topics, each with its live-site URL; with a **topic** it prints that page's URL. It is
**read-only and local-first** — it resolves a topic to a URL and prints it. It **never fetches**
the page and it ships **no doc content** in the package.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled engine:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`. If missing/empty, search the
  Claude Code plugins directory for a `mokata` plugin containing `src/mokata/__init__.py`. (If a
  `mokata` CLI is on PATH, use it directly.)
- Build the engine command with the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Print the pointer (read-only)

```bash
# no topic → the topic list with URLs; a topic → that page's URL
eval "$ENGINE docs ${ARGUMENTS}"
```

Show the output **verbatim**. With no `$ARGUMENTS` it renders a header box + a `(topic, title,
url)` table; with a topic it prints that page's site URL and title. An unknown topic re-prints the
list, notes the miss, and exits non-zero — offer the closest topic from the list.

The topic list is a small, curated map of the top-level guides and one landing page per docs
section. For anything not listed, point the user at the full site:
<https://mokata.ai/>.

## Notes

- **Local-first:** this command only prints URLs — it does not open a browser or fetch the page.
- **Single source:** the docs themselves live at the repo-root `docs/` tree, which mkdocs builds
  into the site. Nothing here ships doc content in the wheel.
