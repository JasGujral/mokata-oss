// Stage 64b — unit tests for the pure chat-intent mapping in ../chat (no vscode runtime needed).
// Run after compiling: `npm run compile && npm test` (node's built-in test runner).
//
// Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

import { test } from "node:test";
import * as assert from "node:assert";
import {
  resolveChatIntent,
  proposalForVerb,
  CHAT_READ_VIEWS,
  WRITE_VERBS,
  MOKATA_MCP_CONFIG
} from "../chat";
import { READ_COMMANDS } from "../mokata";

test("every chat read view targets the Stage-64 READ_COMMANDS whitelist (read-only)", () => {
  const allowed = new Set(Object.keys(READ_COMMANDS)); // status, progress, governance, memory
  for (const view of Object.values(CHAT_READ_VIEWS)) {
    assert.ok(allowed.has(view), `chat targets a non-whitelisted view: ${view}`);
  }
});

test("subcommand resolves to a read view", () => {
  assert.deepStrictEqual(resolveChatIntent("status", ""), { kind: "read", view: "status" });
  assert.deepStrictEqual(resolveChatIntent("why", ""), { kind: "read", view: "governance" });
  assert.deepStrictEqual(resolveChatIntent("lanes", ""), { kind: "read", view: "progress" });
});

test("prompt keyword resolves to a read view when no subcommand", () => {
  assert.deepStrictEqual(resolveChatIntent(undefined, "show me the memory health"),
    { kind: "read", view: "memory" });
  assert.deepStrictEqual(resolveChatIntent(undefined, "why did this gate block?"),
    { kind: "read", view: "governance" });
});

test("write verbs resolve to a PROPOSE intent (defer, never run)", () => {
  for (const verb of ["ship", "develop", "remember", "init", "reset"]) {
    const intent = resolveChatIntent(undefined, `please ${verb} it now`);
    assert.strictEqual(intent.kind, "propose", `${verb} should be proposed, not run`);
    assert.strictEqual(intent.verb, verb);
    // a propose intent never carries a read view to spawn
    assert.strictEqual(intent.view, undefined);
  }
});

test("WRITE_VERBS and read views are disjoint (no write verb is a read view)", () => {
  for (const v of WRITE_VERBS) {
    assert.ok(!Object.prototype.hasOwnProperty.call(CHAT_READ_VIEWS, v),
      `${v} is both a write verb and a read intent`);
  }
});

test("an unknown ask falls back to help (no spawn, no write)", () => {
  assert.strictEqual(resolveChatIntent(undefined, "hello there").kind, "help");
});

test("proposalForVerb shows both the CLI and slash forms", () => {
  assert.deepStrictEqual(proposalForVerb("ship", "mokata"),
    { slash: "/mokata:ship", cli: "mokata ship" });
});

test("the MCP config registers the bundled mokata-mcp console entry", () => {
  assert.strictEqual(MOKATA_MCP_CONFIG.servers.mokata.command, "mokata-mcp");
});
