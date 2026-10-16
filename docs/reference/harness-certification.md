# Harness certification (mokata-mcp on other agents)

mokata's MCP server (`mokata-mcp`) is meant to work in any MCP client. This page says what
"works" means for four clients — **Cursor, Codex CLI, Gemini CLI and Copilot CLI** — and how
that gets checked.

**Status: no client is certified yet.** The current results are in
[`harness-cert-matrix.json`](harness-cert-matrix.json). Every cell reads `untested` until an owner
runs the checklist below with the real client and records the result.

## The criteria

| id | name | the server passes when… |
|---|---|---|
| C1 | config | `mokata setup <harness>` writes an MCP entry for server `mokata` into the client's own config file |
| C2 | starts | the server launched from that entry completes MCP `initialize` over stdio |
| C3 | tools | `tools/list` returns the tool set this mokata registers, including `status`, `remember` and `recall` (any mismatch is version skew) |
| C4 | read | the read tool `status` answers without error and reports this mokata's version |
| C5 | gate | the gated write `remember` will not commit without a human-minted approval. A call with no id comes back `proposed`, a model-typed `approve=true` writes nothing, and a forged proposal id is refused |
| C6 | memory | the memory round-trip works: propose, then a human runs `mokata approve <id>` in a terminal, then the redeem commits, then `recall` returns the value. Using the same approval a second time is refused |

A client is **certified** only when all six criteria have a recorded `pass`, and each `pass`
carries the client version, the person who recorded it and a date. `manual` is not a pass.

### Where setup stands per client

| client | `mokata setup` target | MCP config | C1 today |
|---|---|---|---|
| Cursor | `cursor` | `<project>/.cursor/mcp.json`, written by setup | can pass |
| Gemini CLI | `gemini` | `<project>/.gemini/settings.json`, written by setup | can pass |
| Codex CLI | `codex` | `~/.codex/config.toml`, **registered by hand** | `manual` |
| Copilot CLI | `copilot` | `~/.copilot/mcp-config.json`, **registered by hand** | `manual` |

## Two kinds of evidence

1. **The local contract suite.** It proves the server's side and needs no third-party client.
   A minimal stdio MCP client built into mokata (`mokata.harness_cert`) runs C1–C6 against a
   scratch repo wired by `mokata setup`. CI runs it as `tests/test_d12_harness_cert_matrix.py`. You
   can also run it by hand:

   ```bash
   python scripts/harness_cert.py contract all            # this interpreter's server
   python scripts/harness_cert.py contract cursor --use-config   # the command the config names
   ```

   A green contract run **does not certify a client**. It shows what any conforming client
   would see, and it never writes to the matrix. To stand in for the human approval, it runs
   `mokata approve <id> --yes` in a separate process.

2. **The owner's checklist.** This is what the matrix records. Run it with the real client:

   ```bash
   python scripts/harness_cert.py checklist gemini        # the run sheet
   python scripts/harness_cert.py record gemini C3 pass --client-version 0.9.0 --by <you>
   python scripts/harness_cert.py show                    # the matrix
   ```

   `record` will not save a `pass` that is missing the client version, the recorder or the date.
   If the matrix file can't be parsed, `record` stops with an error instead of overwriting it.
