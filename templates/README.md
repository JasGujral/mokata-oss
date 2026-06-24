# templates/

Reference artifacts for the mokata spine.

- `manifest.schema.json` — JSON Schema for the stack manifest (A1). **Generated** from
  `src/mokata/schema.py` (`MANIFEST_JSON_SCHEMA`), which is the source of truth. Used
  by the optional `jsonschema` validation pass; the built-in validator enforces the
  same shape without the dependency.

The default constitution scaffold lives in `src/mokata/init.py`
(`DEFAULT_CONSTITUTION`) and is written to `.mokata/constitution.md` by `mokata init`.
