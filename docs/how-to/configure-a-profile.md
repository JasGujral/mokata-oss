# How-to: configure a profile

## Pick a profile at init

```bash
mokata init --profile minimal    # engine only, zero network egress
mokata init                      # default: standard — knowledge + memory on lean local defaults
mokata init --profile full       # every known graph/memory provider wired
mokata init --profile custom     # everything wired as a starting point to hand-tune
```

`standard` is the default — engine + graph + memory on lean, local, dependency-free
defaults (grep + SQLite). Use `full` to wire every graph/memory provider (each degrades to
its floor when absent), or `minimal` for just the governed TDD engine.

### Or pick an adoption MODE instead

If you'd rather not think about providers, `--mode` is a graduated on-ramp — an **alias for a
profile plus a printed quickstart**. It is **mutually exclusive** with `--profile`:

```bash
mokata init --mode seatbelt   # just the gates
mokata init --mode memory     # gates + persistent memory
mokata init --mode full       # everything
```

`memory` and `full` additionally **offer** the local embeddings model when run interactively (see
[use & heal memory](use-memory.md)); `seatbelt` structurally never does.

### From inside Claude Code

Once mokata is wired (`pip install mokata` → `mokata setup claude`), you don't need the terminal —
type **`/init full`** (or `standard` / `minimal`). It previews exactly what it will write, asks you
to approve, then sets the profile. You can also just say *"set up mokata here"* and Claude will run
the gated `init` MCP tool; on a brand-new project mokata even offers to initialize it for you
(once — never a nag).

## Switch an existing repo's profile (e.g. up to `full`)

Already initialized? **Don't re-init** — run the re-runnable wizard:

```bash
mokata reconfigure --profile full
```

It is gated, idempotent, and reversible: it re-wires providers on the already-initialized repo
(add/remove an integration, switch a backend, change the profile) and leaves your memory and run
state untouched. Inside Claude Code the same flow is the **`/reconfigure`** slash command.

`full` wires the whole graph/memory provider chain, but the graph tools themselves are external —
to get the full structural tier immediately after switching, install and adopt
`code-review-graph` (see [use a codebase graph](use-a-codebase-graph.md#wire-a-graph)):

```bash
pip install "code-review-graph[embeddings]"
mokata graph adopt code-review-graph
mokata graph status
```

## Tune the committed manifest

Everything is a toggle in `.mokata/manifest.json` (see the
[manifest reference](../reference/manifest.md)):

- **Layers** — set `layers.<name>.enabled` to `false` and that layer's capabilities drop
  from the router.
- **Tools** — set `tools.<id>.enabled` to `false`; the router degrades to the next provider
  in the capability's `fallback` chain.
- **Memory types** — `settings.memory.{persistent,decision,episodic}` toggle independently.
- **Trust dial** — `settings.trust` is a flat map to `read-only` / `propose-only` /
  `gated-write` (the default). See [what it actually governs](#the-trust-dial-what-it-governs)
  below — it is narrower than the name suggests.
- **Output density** — `settings.governance.output_density: true` to enable F4 compression.
- **Karpathy gates** — `settings.governance.karpathy.<id>: false` to disable a gate.

- **Backend paths** — point a backend at a custom location (SQLite path, Obsidian vault,
  hosted Postgres) via each tool's `config` block: see
  [configure storage backends & paths](configure-storage-backends.md).
- **Codebase graph** — `full` wires a real graph (code-review-graph / serena) for structural
  queries, with the embedded stdlib-AST floor as the structural default and grep as the universal
  emergency floor: see [use a codebase graph](use-a-codebase-graph.md).

## The trust dial: what it governs

`settings.trust` is a **flat `{key: level}` map**. A key is either a write **surface** (`mcp`,
`cli`) or a **tool** name (`remember`, `config_set`, …), and the tool wins. Resolution is: the
tool's own level, else the surface's, else `gated-write`. So you can govern a whole surface in
one line and still carve out a single tool:

```jsonc
"settings": {
  "trust": {
    "mcp":      "propose-only",   // the default for every write arriving over MCP
    "remember": "read-only"       // …except this one, which may not write at all
  }
}
```

**Where it is actually enforced — and where it is not.** Being precise here matters more than
being impressive:

- **The MCP write surface is the only place the dial has teeth today.** A `read-only` MCP tool
  refuses to write at all, and that refusal is a **configuration bound, not a missing approval**:
  no proposal, and no human approval, can lift it.
- **`settings.trust.cli` is accepted and validated, but enforces nothing today.** It is
  reserved — set it and no CLI behaviour changes. mokata says so rather than implying a rung it
  does not have.
- **Native `Write` / `Edit` are outside the dial entirely.** The dial governs mokata's own write
  tools; it is not a filesystem permission. What polices a native write is the PreToolUse
  **gate-guard** (run-state) and **secret-guard** (security), which are a separate mechanism.

**The honest ladder on `mcp` is two rungs, not three:**

```text
read-only  ▸  write-allowed        (propose-only == gated-write)
```

`propose-only` and `gated-write` **coincide on MCP**, because *every* MCP write already requires
a human-minted approval — the tool call returns a proposal id, you mint the approval out-of-band
with `mokata approve <id>`, and only a re-call carrying that id commits. Setting `propose-only`
is still meaningful (it **pins** that floor, so a future loosening cannot silently un-pin it),
but it buys no extra teeth today.

`mokata doctor` prints this same surface-truth line whenever `settings.trust` is set, so the
config can never quietly promise more than it delivers.

## Verify

```bash
mokata validate       # manifest parses + validates
mokata doctor         # missing providers, conflicts, bad trust, oversized rule tiers
mokata coverage       # which capabilities are covered + any gaps/overlaps
mokata status         # what each capability resolves to right now
```
