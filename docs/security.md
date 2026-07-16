# Security

Report vulnerabilities **privately** via GitHub's
[private vulnerability reporting](https://github.com/JasGujral/mokata-oss/security/advisories/new) —
do not open a public issue. See the repository's
[`SECURITY.md`](https://github.com/JasGujral/mokata-oss/blob/main/SECURITY.md) for supported
versions and response expectations.

Separately, mokata ships a *defensive* feature: 4-layer secret protection and a sync
security hook (`secret_guard.py`, exit code 2) that blocks secrets before they are written,
committed, or sent. mokata is local-first and sends nothing off-machine by default.

## The two sync hooks — and why only one is a *security* block

mokata registers **two** blocking (`PreToolUse`) hooks in Claude Code. Both stop a tool call with
**exit code 2**, but they are different in kind, and the difference is security-relevant:

| Hook | Matches | Class | Overridable? |
|---|---|---|---|
| `secret-guard` | `Write` · `Edit` · `MultiEdit` · **`Bash`** | **security** — a credential is about to be written, committed, or sent | **Never.** No flag, no approval, no override lifts it — an approved write is still hard-blocked |
| `gate-guard` | `Write` · `Edit` · `MultiEdit` · `NotebookEdit` | **methodology** — the [run-state gates](reference/cli.md#mokata-gate-statusoverrideclear) (`approach-approval`, `spec-persisted`, `no-code-without-failing-test`, `spec-scope`) | **Yes**, explicitly: `mokata gate override <gate> --reason "<why>"` — session-scoped, re-confirmed, and **ledgered**. There is deliberately no env-var kill switch |

A methodology gate is something a human may knowingly step around, on the record. A security block is
not — so mokata never lets an approval, a trust level, or a flag turn the secret-guard off.

**One honest limit.** The `gate-guard` does **not** match `Bash`: a shelled `sed -i` (or any other
in-shell edit) is **not** policed by the run-state gates. The `secret-guard` **does** match `Bash`,
so the *security* boundary holds there. Note also that only the **Claude Code** harness declares the
`hooks` capability — see [platform support](reference/platform-support.md).
