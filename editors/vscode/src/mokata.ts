// Stage 64 — mokata VS Code extension: the thin, READ-ONLY client over the mokata CLI.
//
// This module is the ONLY place that spawns the CLI, and it spawns it ONLY with the
// read-only subcommands in READ_COMMANDS. There is no engine logic here — every view is
// rendered from the CLI's own output. Durable writes are NEVER performed by the extension;
// they are deferred to the human-gated CLI (the terminal passthrough in extension.ts).
//
// Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

import { execFile } from "child_process";

// The read-only mokata subcommands each view renders. NOTHING here mutates state — these are
// all observability commands. A view not in this map cannot be spawned (see runRead's guard),
// so a durable-write subcommand can never be reached from the editor.
export const READ_COMMANDS: Record<string, string[]> = {
  status: ["status"],
  progress: ["progress", "--lanes"],
  governance: ["govern"],
  memory: ["memory"]
};

export type ReadView = keyof typeof READ_COMMANDS;

// Friendly degrade-clean copy — shown instead of an error spew when mokata is absent or the
// folder isn't a mokata project. (Lower-cased substrings "not installed" / "not initialized"
// are also what the extension/tests key off.)
export const NOT_INSTALLED_MESSAGE =
  "mokata is not installed (or not on your PATH). Install it with `pipx install mokata` " +
  "(or `pip install mokata`), then reload — this extension is a read-only view over the CLI.";

export const NOT_INITIALIZED_MESSAGE =
  "mokata is not initialized in this folder. Run `mokata init` in the terminal to set it up " +
  "(the extension never writes — initialization stays human-gated in the CLI).";

export interface ReadResult {
  ok: boolean;
  text: string;
  reason?: "not-installed" | "not-initialized";
}

// The CLI prints "… is not initialized in '…' (no .mokata/manifest.json)…" when there's no
// mokata project here. Recognise it (and the generic phrasing) so we degrade cleanly.
export function isNotInitialized(output: string): boolean {
  return /not initialized/i.test(output);
}

export function isNotInstalledError(err: unknown): boolean {
  return !!err && (err as NodeJS.ErrnoException).code === "ENOENT";
}

// The status-bar badge: a single, compact line. The CLI's status/badge output may carry extra
// lines or whitespace; we keep the first non-empty line and bound its length.
export function formatBadge(cliOutput: string, max = 60): string {
  const first = cliOutput.split(/\r?\n/).map(s => s.trim()).find(Boolean) || "";
  const oneLine = first.length > max ? first.slice(0, max - 1) + "…" : first;
  return oneLine ? `$(shield) ${oneLine}` : "$(shield) mokata";
}

// Build the read-only CLI argv for a view. Throws on anything not whitelisted so a write
// subcommand can never be spawned (defence in depth around the map above).
export function readArgs(view: string): string[] {
  const args = READ_COMMANDS[view as ReadView];
  if (!args) {
    throw new Error(`'${view}' is not a read-only mokata view (allowed: ${Object.keys(READ_COMMANDS).join(", ")})`);
  }
  return args.slice();
}

// Spawn the CLI for a read-only view and return rendered text or a degrade-clean message.
// execFile (not a shell) with a fixed argv — no shell injection, no write path.
export function runRead(cliPath: string, view: string, cwd: string | undefined): Promise<ReadResult> {
  const args = readArgs(view); // guard: throws synchronously for a non-read view
  return new Promise<ReadResult>(resolve => {
    execFile(cliPath, args, { cwd, timeout: 8000, windowsHide: true }, (err, stdout, stderr) => {
      if (isNotInstalledError(err)) {
        resolve({ ok: false, text: NOT_INSTALLED_MESSAGE, reason: "not-installed" });
        return;
      }
      const out = `${stdout || ""}${stderr || ""}`;
      if (isNotInitialized(out)) {
        resolve({ ok: false, text: NOT_INITIALIZED_MESSAGE, reason: "not-initialized" });
        return;
      }
      resolve({ ok: true, text: out.trim() || "(no output)" });
    });
  });
}
