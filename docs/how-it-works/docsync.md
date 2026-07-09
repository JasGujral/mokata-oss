# docsync — keeping the docs true to the code

Documentation drifts silently: a command gets renamed, a skill count changes, an install path dies,
a signature moves under a doc's feet. **docsync** is mokata's skill for catching that — it audits
the public docs against the live code, and reconciles the drift through the same human-gated
WriteGate everything else rides. It's how this very doc set stays honest.

docsync is both **user-invocable** (`mokata docsync` / `/mokata:docsync`) and **model-invocable**
(it auto-fires on drift). It adds **no new runtime gate**: the audit is read-only and the reconcile
rides the existing WriteGate.

## Two targeting modes

- **You point at a doc** — `mokata docsync <path>` audits exactly that file.
- **The system finds the docs** — `mokata docsync` with no target sweeps the public doc tree
  (README + `docs/`, never the internal trees) and drift-detects; given the symbols a change
  touched, it narrows to just the docs that reference them. You don't have to know which doc went
  stale.

## Two output modes

### Audit (read-only, the default)

The audit cross-references every claim in a doc against the code and reports each discrepancy with
a severity and the stale section it sits in. It writes nothing. The checks are pulled from the
single sources the rest of mokata reads — never a hand-kept copy:

| Check | Ground truth | Severity |
|---|---|---|
| **skill count** (`N skills`) | the curated registry (16) + shipped domain skills (26 total) | Blocking |
| **command name** (`mokata <cmd>`) | the live CLI parser | Blocking |
| **slash skill** (`/mokata:<name>`) | the shipped skill set | Blocking |
| **install path** (`pip install …`) | the canonical `pip install mokata` | Blocking |
| **version example** (`mokata==x.y.z`) | the shipping version constant | Info |
| **symbol reference** (`module.symbol`) | the code graph, when one is injected | Minor |

```bash
mokata docsync docs/getting-started.md     # audit one doc
mokata docsync                             # sweep the whole public doc tree
```

A clean doc reports `OK — every claim matches the code`. A drifted one lists each finding and
**highlights the stale sections**, e.g.:

```text
docsync audit · docs/example.md: 2 discrepancy(ies)
  ⚠ stale section(s): Install
  [Blocking] skill-count (L12 · §Overview): doc claims an outdated skill count; the code ships 16 pipeline skills (26 incl. domain skills)
  [Blocking] install-path (L20 · §Install): dead install path — a mokata-prefixed package other than the canonical `pip install mokata`
```

### Reconcile (human-gated writes)

`mokata docsync <path> --reconcile` proposes the edits that bring a doc back in line with the code,
**previews the unified diff**, and writes only through the WriteGate (secret-scan → explicit human
approval → audit). A decline writes nothing. Never silent — the same P2 guarantee as every durable
write.

## Auto-fire on drift

docsync is wired to **attach to the flow**, not wait to be run:

- **On drift.** When a change touches a symbol a doc references, docsync engages with the `⛭`
  banner and states its boundary up front — the audit is read-only; any edit is previewed and
  human-gated — so an auto-fire can never quietly become a silent write.
- **In the pre-release doc gate.** mokata's mandatory ground-up documentation review (docs must
  describe only what's shipping; the install path must be real; counts/commands must match) runs
  docsync's sweep, so that gate stops being a manual pass.
- **In brainstorm.** Brainstorm's blast-radius lens calls docsync's doc-freshness check: per
  approach, it lists the docs the change touches or invalidates, marks each **fresh / stale /
  new-doc-needed**, highlights the stale ones, and asks you to update them before the spec. A stale
  doc left unaddressed carries into the spec as an open item.

## Why it exists

Every mokata release must pass a ground-up doc gate before it ships. docsync turns that hand-run
audit into a reusable, auto-firing capability — the same discipline mokata applies to code
(evidence over claims, human-gated writes, an audit trail) applied to the docs about the code. It
pairs with the [docs/ADR domain skill](domain-skills.md) and feeds the release gate.

## See also

- [The skills layer](skills-and-gates.md) — the anatomy docsync shares with every skill.
- [Governance & audit](../concepts/governance.md) — the WriteGate a reconcile rides.
- [CLI reference](../reference/cli.md) — the `docsync` command flags.
