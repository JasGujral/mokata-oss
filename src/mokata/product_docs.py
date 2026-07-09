"""A2 — `/mokata:docs`: a pointer to the published docs site (NOT a bundled-docs browser).

The product docs live ONLY at the repo-root ``docs/`` tree — the single source mkdocs builds the
published site from. Nothing here ships doc CONTENT: `mokata docs` maps a topic to its live-site
URL and prints it, so the wheel stays clean (no docs in the package) and the command is
local-first — it NEVER fetches at runtime, it only resolves and prints URLs.

`mokata docs` (no topic) lists the shipped topics with their URLs through the A4 legibility
helpers (`box`/`table` + the `_color_enabled` colour gate). Degrade-clean: colour + a Unicode box
only on a real TTY (NO_COLOR unset); a piped / redirected / NO_COLOR run renders plain ASCII with
zero escape codes. Read-only — it enumerates a small static map and formats it; it writes nothing.

Topic → URL follows the mkdocs rule: take the doc's path relative to ``docs/``, drop the ``.md``,
drop a trailing ``index`` (``index`` → the site root), and join under BASE_URL with a trailing
slash (``getting-started`` → BASE/getting-started/ ; ``concepts/memory`` → BASE/concepts/memory/).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .legibility import _color_enabled, box, table

# The published documentation site (mkdocs site_url). The ONLY place docs content lives online.
BASE_URL = "https://jasgujral.github.io/mokata-oss/"


@dataclass(frozen=True)
class Topic:
    slug: str            # docs-root-relative path without ".md" (POSIX), e.g. concepts/execution-model
    title: str           # a human-friendly title for the list


# A SMALL, curated map of top-level guides + one landing page per product section. Names + slugs
# ONLY — no doc content. mkdocs builds the live site from repo-root docs/; this points at it. The
# drift guard (test_docs_cmd) fails if a new section is added under docs/ but missing here.
TOPICS: Tuple[Topic, ...] = (
    Topic("index", "mokata — documentation home"),
    Topic("getting-started", "Getting started"),
    Topic("quickstart", "Quickstart"),
    Topic("tutorials/mokata-complete-guide", "Tutorials — The Complete Guide"),
    Topic("how-to/first-run", "How-to — Your first run"),
    Topic("concepts/execution-model", "Concepts — How mokata uses an LLM"),
    Topic("how-it-works/index", "How it works — architecture overview"),
    Topic("reference/cli", "Reference — the CLI"),
    Topic("profiles", "Profiles & toggles"),
    Topic("developer-guide", "Developer guide"),
    Topic("security", "Security"),
    Topic("changelog", "Changelog"),
)


def topic_url(slug: str) -> str:
    """The published-site URL for a docs `slug`, per the mkdocs rule (drop ``.md``/trailing
    ``index``, join under BASE_URL with a trailing slash). ``index`` → BASE_URL itself."""
    key = (slug or "").strip()
    if key.endswith(".md"):
        key = key[:-3]
    parts = [p for p in key.split("/") if p]
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    path = "/".join(parts)
    return BASE_URL + (path + "/" if path else "")


def list_topics() -> List[Topic]:
    """The shipped topic list (a copy — callers can't mutate the map)."""
    return list(TOPICS)


def find_topic(topic: str) -> Optional[Topic]:
    """The Topic whose slug matches `topic` (a trailing ``.md`` or surrounding slashes are
    tolerated), or None when unknown. Pure lookup against the shipped list — no fetch, no I/O."""
    key = (topic or "").strip().strip("/")
    if key.endswith(".md"):
        key = key[:-3]
    for t in TOPICS:
        if t.slug == key:
            return t
    return None


def render_topic_list(*, ascii_only: bool = False) -> str:
    """The one-screen topic list: a header box + a (topic, title, url) table, through the A4
    helpers. Every row's URL points at the live site — nothing is fetched to build it."""
    header = box(
        [f"mokata · docs — {len(TOPICS)} topics on the live site",
         f"the full docs: {BASE_URL}",
         "open one:  mokata docs <topic>  (/mokata:docs <topic>) — prints its site URL"],
        ascii_only=ascii_only,
    )
    rows = [(t.slug, t.title, topic_url(t.slug)) for t in TOPICS]
    return "\n".join([header, "", table(("topic", "title", "url"), rows, ascii_only=ascii_only)])


def render_topic(t: Topic, *, ascii_only: bool = False) -> str:
    """A single topic's site URL + title, in a box (colour-gated, zero escapes when ascii)."""
    return box([t.title, topic_url(t.slug)], ascii_only=ascii_only)


def render(topic: Optional[str] = None, *, ascii_only: Optional[bool] = None,
           color: Optional[bool] = None, stream=None) -> Optional[str]:
    """Render the docs surface. No topic → the topic list. A known topic → its URL + title. An
    unknown topic → None (the caller lists + hints and exits non-zero). `ascii_only`/`color`/
    `stream` are overridable for tests; by default both derive from the single colour gate so a
    piped / NO_COLOR run has zero escapes."""
    if color is None:
        color = _color_enabled(ascii_only=bool(ascii_only), stream=stream)
    if ascii_only is None:
        ascii_only = not color
    if topic:
        t = find_topic(topic)
        return render_topic(t, ascii_only=ascii_only) if t is not None else None
    return render_topic_list(ascii_only=ascii_only)


__all__ = ["BASE_URL", "Topic", "TOPICS", "topic_url", "list_topics", "find_topic",
           "render_topic_list", "render_topic", "render"]
