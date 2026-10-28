"""The IDE's versioned HTTP contract (0.0.26 P0 — ID-API-CONTRACT).

`api_contract.json` (beside this file) holds the contract VERSION, the shared schemas, and every
CORE route; each feature module `features/<name>.py` ships its routes' entries in
`features/<name>.contract.json`. `load()` merges them — one file per feature, so parallel stages
never edit a shared line. `tests/test_p0_ide_api_contract.py` boots the app and holds the
contract to the app both ways: a route the app serves that the contract does not list fails, and a
contract entry the app does not serve fails; every entry's probes are sent and every answer is
validated against the entry's schema for that status.

A route entry:
  {"path": "/api/x", "method": "GET", "kind": "json", "feature": "core",
   "params": {"query": {"name": "what it is"}} | {"body": {"name": "what it is"}},
   "responses": {"200": {"media": "json", "schema": {...}}, "400": {"media": "json",
                 "schema": {"$ref": "#/schemas/Error"}}},
   "probes": [{"query": {...}, "status": 200}, {"body": {...}, "status": 400, "demo": true}]}

`media` is one of json, html, text, js, css, svg, sse, redirect, empty. The schema dialect is
the subset `validate` implements (type, properties, required, items, enum, nullable via a type
list, `$ref` into the top-level `schemas`) — no dependency, so the IDE never needs one.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..errors import MokataError

HERE = os.path.dirname(os.path.abspath(__file__))
CORE_FILE = os.path.join(HERE, "api_contract.json")
FEATURES_DIR = os.path.join(HERE, "features")
MEDIA = ("json", "html", "text", "js", "css", "svg", "sse", "redirect", "empty")


class ContractError(MokataError, ValueError):
    """The contract files disagree with themselves (a duplicate entry, a bad shape)."""


def _read(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load() -> Dict[str, Any]:
    """The merged contract: `{version, schemas, routes}` — core first, then each feature file in
    name order. A (path, method) listed twice is a ContractError."""
    core = _read(CORE_FILE)
    routes: List[Dict[str, Any]] = [dict(r, feature=r.get("feature", "core"))
                                    for r in core["routes"]]
    schemas = dict(core.get("schemas", {}))
    for name in sorted(os.listdir(FEATURES_DIR)):
        if not name.endswith(".contract.json"):
            continue
        part = _read(os.path.join(FEATURES_DIR, name))
        feature = name[: -len(".contract.json")]
        for k, v in part.get("schemas", {}).items():
            if k in schemas:
                raise ContractError(f"{name}: schema {k!r} is already defined")
            schemas[k] = v
        routes.extend(dict(r, feature=feature) for r in part["routes"])
    seen = set()
    for r in routes:
        key = (r["path"], r["method"])
        if key in seen:
            raise ContractError(f"{r['method']} {r['path']} is listed twice in the contract")
        seen.add(key)
        for status, resp in r.get("responses", {}).items():
            if resp.get("media") not in MEDIA:
                raise ContractError(f"{r['method']} {r['path']} {status}: media must be one of "
                                    f"{MEDIA}")
    return {"version": core["version"], "schemas": schemas, "routes": routes}


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[t])


def validate(value: Any, schema: Dict[str, Any], schemas: Dict[str, Any],
             where: str = "$") -> List[str]:
    """Every way `value` breaks `schema`, as `where: what` lines ([] = valid)."""
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/schemas/") or ref[len("#/schemas/"):] not in schemas:
            return [f"{where}: unknown $ref {ref}"]
        return validate(value, schemas[ref[len("#/schemas/"):]], schemas, where)
    if "oneOf" in schema:
        # A route whose answer has several honest shapes (e.g. /api/semantic: not-installed /
        # model-not-local / ready) lists each; the value must match EXACTLY one.
        tried = [validate(value, alt, schemas, where) for alt in schema["oneOf"]]
        ok = [e for e in tried if not e]
        if len(ok) == 1:
            return []
        if not ok:
            best = min(tried, key=len)
            return [f"{where}: matches none of {len(tried)} shapes; closest: {best[0]}"]
        return [f"{where}: matches {len(ok)} of the oneOf shapes — they must be disjoint"]
    errs: List[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in types):
            return [f"{where}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{where}: missing required key {k!r}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs.extend(validate(value[k], sub, schemas, f"{where}.{k}"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value[:50]):
            errs.extend(validate(item, schema["items"], schemas, f"{where}[{i}]"))
    return errs


def route_index(contract: Optional[Dict[str, Any]] = None) -> Dict[tuple, Dict[str, Any]]:
    c = contract or load()
    return {(r["path"], r["method"]): r for r in c["routes"]}
