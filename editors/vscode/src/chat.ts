// Stage 64b — mokata Copilot Chat participant: the PURE intent layer (no vscode/process deps).
//
// `@mokata` in Copilot Chat is READ-ONLY. This module maps a chat request (its subcommand and
// prompt) to one of the Stage-64 read-only views (mokata.ts READ_COMMANDS) — and NOTHING else.
// Anything that would WRITE resolves to a "propose" intent: the participant shows the exact
// `/mokata:` command and DEFERS to the human (the terminal passthrough); it never auto-writes.
//
// It carries no engine logic and spawns nothing — the extension renders these intents by calling
// the existing guarded `runRead` in mokata.ts.
//
// Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

export const PARTICIPANT_ID = "mokata.chat";
export const PARTICIPANT_NAME = "mokata";

// Chat intent (request.command) OR a prompt keyword -> a Stage-64 READ-ONLY view id. Every
// value here MUST be a key of mokata.ts READ_COMMANDS (status | progress | governance | memory)
// — that is what keeps `@mokata` read-only by construction.
export const CHAT_READ_VIEWS: Record<string, string> = {
  status: "status",
  progress: "progress",
  lanes: "progress",
  memory: "memory",
  health: "memory",
  why: "governance",
  govern: "governance",
  gate: "governance",
  gates: "governance"
};

// Verbs that would change state. `@mokata` NEVER runs these — it proposes the command and
// defers to the human-gated CLI (mokata's own gates then apply). Kept as data so the read-only
// guarantee is auditable.
export const WRITE_VERBS = [
  "init", "setup", "unsetup", "reset", "spec", "develop", "review", "ship", "onboard",
  "brainstorm", "refine", "test", "remember", "push", "pull", "import", "export",
  "reconfigure", "upgrade", "session", "vault", "skill", "decompose"
];

export type ChatIntentKind = "read" | "propose" | "help";

export interface ChatIntent {
  kind: ChatIntentKind;
  view?: string;       // for "read": a READ_COMMANDS view id
  verb?: string;       // for "propose": the mokata verb to defer to the human
}

// Resolve a chat request to an intent. Pure + deterministic. The subcommand wins; otherwise the
// prompt is scanned for a read keyword, then a write verb (-> propose), else help.
export function resolveChatIntent(command: string | undefined, prompt: string): ChatIntent {
  const cmd = (command || "").trim().toLowerCase();
  if (cmd && CHAT_READ_VIEWS[cmd]) {
    return { kind: "read", view: CHAT_READ_VIEWS[cmd] };
  }
  const words = (prompt || "").toLowerCase().split(/[^a-z]+/).filter(Boolean);
  for (const w of words) {
    if (CHAT_READ_VIEWS[w]) {
      return { kind: "read", view: CHAT_READ_VIEWS[w] };
    }
  }
  for (const w of [cmd, ...words]) {
    if (w && WRITE_VERBS.includes(w)) {
      return { kind: "propose", verb: w };
    }
  }
  return { kind: "help" };
}

// The slash-command + CLI forms to SHOW for a proposed write (the human runs one of them).
export function proposalForVerb(verb: string, cliPath = "mokata"): { slash: string; cli: string } {
  return { slash: `/mokata:${verb}`, cli: `${cliPath} ${verb}` };
}

export function chatHelp(): string {
  return [
    "**@mokata** is read-only in chat. Ask it to *show* you mokata's state:",
    "",
    "- `@mokata /status` — the stack status badge",
    "- `@mokata /progress` — run progress & parallel lanes",
    "- `@mokata /memory` — memory + the health nudge",
    "- `@mokata /why` — governance & gate verdicts (why a gate blocked)",
    "",
    "For anything that **changes** your project (spec, develop, ship, remember…), I'll propose " +
    "the exact `/mokata:` command and you run it — mokata's human gates still apply. I never " +
    "write on your behalf."
  ].join("\n");
}

// Degrade-clean copy for when the host has no Chat API, or Copilot/MCP isn't available.
export const CHAT_API_UNAVAILABLE_MESSAGE =
  "The VS Code Chat API is not available in this editor (it needs VS Code 1.90+ with Copilot " +
  "Chat). The mokata panel and status badge still work — this is read-only either way.";

// ----------------------------------------------------------------- mokata-mcp wiring (Copilot)
// The bundled `mokata-mcp` console entry, as a VS Code / Copilot Chat MCP server definition.
// This MUST match mcp/mokata.mcp.json. Reads are safe; mokata's MCP write tools stay human-gated
// via the WriteGate inside the server — Copilot calling a write tool still hits mokata's gate.
export const MOKATA_MCP_SERVER = {
  command: "mokata-mcp",
  args: [] as string[],
  type: "stdio"
};

// The `.vscode/mcp.json` document content that registers it (what the setup command writes/shows).
export const MOKATA_MCP_CONFIG = {
  servers: {
    mokata: MOKATA_MCP_SERVER
  }
};
