// Stage 64 — unit tests for the pure helpers in ../mokata (no vscode runtime needed).
// Run after compiling: `npm run compile && npm test` (uses node's built-in test runner).
//
// Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

import { test } from "node:test";
import * as assert from "node:assert";
import {
  formatBadge,
  isNotInitialized,
  isNotInstalledError,
  readArgs,
  READ_COMMANDS,
  NOT_INSTALLED_MESSAGE,
  NOT_INITIALIZED_MESSAGE
} from "../mokata";

test("READ_COMMANDS contains only read-only subcommands", () => {
  const allowed = new Set(["status", "progress", "govern", "memory"]);
  for (const args of Object.values(READ_COMMANDS)) {
    assert.ok(allowed.has(args[0]), `non-read subcommand wired: ${args[0]}`);
  }
});

test("readArgs returns a copy for a known view", () => {
  const a = readArgs("progress");
  assert.deepStrictEqual(a, ["progress", "--lanes"]);
  a.push("mutated");
  assert.deepStrictEqual(readArgs("progress"), ["progress", "--lanes"]); // not aliased
});

test("readArgs throws for an unknown / write view (read-only guard)", () => {
  for (const bad of ["init", "ship", "remember", "reset", "spec"]) {
    assert.throws(() => readArgs(bad), /read-only/, `${bad} should be rejected`);
  }
});

test("isNotInitialized recognises the CLI's uninitialized message", () => {
  assert.ok(isNotInitialized("error: mokata is not initialized in '/x' (no .mokata/manifest.json)."));
  assert.ok(!isNotInitialized("mokata ▸ [brainstorm · spec · ›develop‹ · review · ship]"));
});

test("isNotInstalledError keys off ENOENT", () => {
  assert.ok(isNotInstalledError({ code: "ENOENT" }));
  assert.ok(!isNotInstalledError({ code: "ETIMEDOUT" }));
  assert.ok(!isNotInstalledError(null));
});

test("formatBadge keeps one bounded line with the shield glyph", () => {
  assert.strictEqual(formatBadge("mokata: standard\nextra line"), "$(shield) mokata: standard");
  assert.strictEqual(formatBadge(""), "$(shield) mokata");
  assert.ok(formatBadge("x".repeat(200)).length <= "$(shield) ".length + 60);
});

test("degrade-clean messages are friendly, not error spew", () => {
  assert.match(NOT_INSTALLED_MESSAGE, /not installed/);
  assert.match(NOT_INITIALIZED_MESSAGE, /not initialized/);
});
