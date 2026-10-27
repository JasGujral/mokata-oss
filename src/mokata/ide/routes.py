"""The IDE's route registry and its feature discovery (0.0.26 P0 — ID-API-CONTRACT, ruling R7).

WHY. Every route used to live in one hand-kept list inside `server.build_app`, so every feature
stage edited the same lines. Now a route is a `RouteSpec` in a `Registry`: the core routes are
registered by `server.build_app`, and a FEATURE adds its own by shipping one module under
`mokata/ide/features/` with a `routes(reg, ctx)` function — server.py is not edited. Discovery is
a sorted walk of that package, so two stages that each add a module never touch a shared line.

SAME DOORS. A spec carries only *what* the route is (`kind`) and its `auth`/`hook` flags; the
server wraps every spec in the same `guard(...)` (Host check, Origin check on non-GET, pairing
cookie/token, the hook door only where `hook=True`) and every `json` spec in the same `_json`
read wrapper (400 on a bad request or a refused path, 202 + a job handle for long work, a JSON
error never a 500 page). A feature cannot register around the guard: there is no unguarded mount.

KINDS
  page    an HTML page or a redirect (`/`, `/pair`, `/open`)          GET
  static  a whitelisted file under static/                            GET
  json    a READ: `handler(request) -> dict`, run off the loop by `_json`. With `job=<kind>`
          (0.0.26 P32) the handler validates and returns the WORK, which runs as a job: the
          result, or 202 with the job's handle (`ide/jobs.py`) — never a deadline       GET
  read    `async handler(request) -> Response` answering JSON, for a read that owns its own
          lane or error mapping (`/api/query`'s exact CLI bytes, `/api/ping`)       GET
  action  `async handler(request) -> Response`; reads a JSON body     POST
  stream  a long-lived answer (SSE / long-poll)                       GET
  hook    an observe-only POST from a Claude Code hook (`hook=True`)  POST

CONFLICTS are refused when the app is built (`RouteConflict` names both owners), so two stages
that pick the same path cannot both ship.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..errors import MokataError

KINDS = ("page", "static", "json", "read", "action", "stream", "hook")
_METHODS = {"page": ("GET",), "static": ("GET",), "json": ("GET",), "read": ("GET",), "action": ("POST",),
            "stream": ("GET",), "hook": ("POST",)}
FEATURES_PACKAGE = "mokata.ide.features"


class RouteConflict(MokataError, ValueError):
    """Two registrations claim the same path + method, or a spec is malformed. Raised when the
    app is built — a conflicting feature can never be served."""


@dataclass(frozen=True)
class RouteSpec:
    path: str
    handler: Callable[..., Any]
    methods: Tuple[str, ...]
    kind: str
    feature: str
    auth: bool = True
    hook: bool = False
    # 0.0.26 P32 — a json read that runs as a JOB of this kind (`ide/jobs.KINDS`), labelled for
    # the Activity drawer by `job_label(request)`.
    job: Optional[str] = None
    job_label: Optional[Callable[[Any], str]] = None


class Registry:
    def __init__(self) -> None:
        self._specs: List[RouteSpec] = []
        self._owner: Dict[Tuple[str, str], str] = {}

    def register(self, path: str, handler: Callable[..., Any], *, kind: str,
                 feature: str, methods: Optional[Tuple[str, ...]] = None, auth: bool = True,
                 hook: bool = False,
                 job: Optional[str] = None,
                 job_label: Optional[Callable[[Any], str]] = None) -> RouteSpec:
        if kind not in KINDS:
            raise RouteConflict(f"{feature}: {path}: kind must be one of {KINDS}, not {kind!r}")
        if not path.startswith("/"):
            raise RouteConflict(f"{feature}: {path!r} must start with '/'")
        if not feature:
            raise RouteConflict(f"{path}: every route names the feature that owns it")
        ms = tuple(methods) if methods else _METHODS[kind]
        if any(m not in _METHODS[kind] for m in ms):
            raise RouteConflict(f"{feature}: {path}: a {kind} route answers {_METHODS[kind]}, "
                                f"not {ms}")
        if hook and kind != "hook":
            raise RouteConflict(f"{feature}: {path}: only a `hook` route opens the hook door")
        if job is not None and kind != "json":
            raise RouteConflict(f"{feature}: {path}: `job` is for json reads only")
        if job is not None:
            from .jobs import KINDS as JOB_KINDS
            if job not in JOB_KINDS:
                raise RouteConflict(f"{feature}: {path}: unknown job kind {job!r}")
        if job_label is not None and job is None:
            raise RouteConflict(f"{feature}: {path}: `job_label` needs a job route")
        for m in ms:
            prior = self._owner.get((path, m))
            if prior is not None:
                raise RouteConflict(f"{m} {path} is registered twice: by {prior!r} and by "
                                    f"{feature!r}")
        for m in ms:
            self._owner[(path, m)] = feature
        spec = RouteSpec(path, handler, ms, kind, feature, auth, hook or kind == "hook",
                         job, job_label)
        self._specs.append(spec)
        return spec

    def specs(self) -> List[RouteSpec]:
        return list(self._specs)

    def table(self) -> List[Dict[str, Any]]:
        """`[{path, method, kind, feature}]` — what the contract test compares the contract with."""
        return [{"path": s.path, "method": m, "kind": s.kind, "feature": s.feature}
                for s in self._specs for m in s.methods]


@dataclass
class FeatureContext:
    """What a feature's `routes(reg, ctx)` is handed. Handlers close over it."""
    state: Any                                     # server.AppState (source, root, demo, bus, …)
    q: Callable[[Any, str], str]                   # q(request, "name") -> query param or ""
    body: Callable[[Any], Any]                     # await body(request) -> dict ({} if not JSON)
    bad_request: type                              # raise ctx.bad_request("…") -> a 400 answer
    assets: Dict[str, List[str]] = field(default_factory=dict)   # feature scripts / styles
    registry: Optional[Registry] = None


def feature_modules(package: str = FEATURES_PACKAGE) -> List[Any]:
    """Every feature module under `package`, sorted by name (deterministic route order). A module
    whose name starts with `_` is private to the package and skipped."""
    pkg = importlib.import_module(package)
    names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_"))
    mods = []
    for name in names:
        mod = importlib.import_module(f"{package}.{name}")
        if not callable(getattr(mod, "routes", None)):
            raise RouteConflict(f"{package}.{name} is a feature module with no routes(reg, ctx)")
        mods.append(mod)
    return mods
