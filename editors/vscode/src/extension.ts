// Stage 64 — mokata VS Code extension entry point.
//
// READ-ONLY by construction: this file never spawns the CLI directly — all process execution
// goes through the guarded helper in ./mokata (which only ever runs the read-only whitelist).
// The one write-adjacent affordance is `runInTerminal`, which OPENS a terminal and types a
// command for the HUMAN to run (and the CLI's own gates then apply) — it never auto-executes a
// durable write. Opt-in (mokata.enable) and degrade-clean (a friendly message, never a spew).
//
// Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

import * as vscode from "vscode";
import { formatBadge, runRead, READ_COMMANDS, ReadResult } from "./mokata";
import {
  PARTICIPANT_ID,
  resolveChatIntent,
  proposalForVerb,
  chatHelp,
  CHAT_API_UNAVAILABLE_MESSAGE,
  MOKATA_MCP_CONFIG,
  MOKATA_MCP_SERVER
} from "./chat";

// The read-only views shown in the panel, in display order.
const VIEWS: { id: string; label: string; icon: string }[] = [
  { id: "status", label: "Status", icon: "info" },
  { id: "progress", label: "Run progress & lanes", icon: "pulse" },
  { id: "governance", label: "Governance & gate verdicts", icon: "shield" },
  { id: "memory", label: "Memory & health", icon: "database" }
];

function cfg() {
  return vscode.workspace.getConfiguration("mokata");
}

function cliPath(): string {
  return cfg().get<string>("cliPath", "mokata");
}

function workspaceCwd(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length ? folders[0].uri.fsPath : undefined;
}

// ---------------------------------------------------------------- the read-only tree panel
class MokataNode extends vscode.TreeItem {
  constructor(
    label: string,
    collapsible: vscode.TreeItemCollapsibleState,
    public readonly viewId?: string
  ) {
    super(label, collapsible);
  }
}

class MokataPanelProvider implements vscode.TreeDataProvider<MokataNode> {
  private readonly _changed = new vscode.EventEmitter<MokataNode | undefined | void>();
  readonly onDidChangeTreeData = this._changed.event;

  refresh(): void {
    this._changed.fire();
  }

  getTreeItem(node: MokataNode): vscode.TreeItem {
    return node;
  }

  async getChildren(node?: MokataNode): Promise<MokataNode[]> {
    if (!node) {
      // top level: one collapsible category per read-only view
      return VIEWS.map(v => {
        const n = new MokataNode(v.label, vscode.TreeItemCollapsibleState.Collapsed, v.id);
        n.iconPath = new vscode.ThemeIcon(v.icon);
        return n;
      });
    }
    if (!node.viewId) {
      return [];
    }
    const res: ReadResult = await runRead(cliPath(), node.viewId, workspaceCwd());
    const lines = res.text.split(/\r?\n/).filter(l => l.trim().length > 0);
    if (!lines.length) {
      return [new MokataNode("(nothing to show)", vscode.TreeItemCollapsibleState.None)];
    }
    return lines.map(l => new MokataNode(l, vscode.TreeItemCollapsibleState.None));
  }
}

// ---------------------------------------------------------------- status-bar badge
async function refreshBadge(item: vscode.StatusBarItem): Promise<void> {
  if (!cfg().get<boolean>("enable", true)) {
    item.hide();
    return;
  }
  const res = await runRead(cliPath(), "status", workspaceCwd());
  if (res.ok) {
    item.text = formatBadge(res.text);
    item.tooltip = "mokata — click for the read-only panel & commands";
  } else {
    item.text = "$(shield) mokata";
    item.tooltip = res.text; // the friendly degrade-clean message (not installed / not init)
  }
  item.show();
}

// ---------------------------------------------------------------- the human-gated passthrough
async function runInTerminal(): Promise<void> {
  // Offer the common /mokata: workflow commands. Selecting one TYPES it into a terminal for
  // the human to review and run — the extension never executes a durable write itself.
  const picks = [
    "brainstorm", "spec", "develop", "review", "ship",
    "onboard", "memory", "status", "govern", "init"
  ];
  const choice = await vscode.window.showQuickPick(picks, {
    placeHolder: "Type a mokata command into a terminal (you run it — mokata's gates still apply)"
  });
  if (!choice) {
    return;
  }
  const term = vscode.window.createTerminal({ name: "mokata", cwd: workspaceCwd() });
  term.show();
  // sendText(text, false): do NOT append a newline — the human presses Enter to run it. This
  // keeps every durable action human-gated; the extension only stages the command.
  term.sendText(`${cliPath()} ${choice}`, false);
}

async function showView(viewId: string, channel: vscode.OutputChannel): Promise<void> {
  const res = await runRead(cliPath(), viewId, workspaceCwd());
  channel.clear();
  channel.appendLine(`# mokata ${viewId}`);
  channel.appendLine(res.text);
  channel.show(true);
}

// ---------------------------------------------------------------- Copilot Chat participant (64b)
// READ-ONLY by construction: a chat request resolves (in chat.ts, pure) to one of the Stage-64
// read-only views and is rendered via the SAME guarded runRead — or, for a write-ish ask, to a
// PROPOSAL that defers to the human (a copy-able command + a button that STAGES it in a terminal).
// The participant never spawns a write; it never auto-runs anything.
async function handleChat(
  request: vscode.ChatRequest,
  _context: vscode.ChatContext,
  stream: vscode.ChatResponseStream,
  _token: vscode.CancellationToken
): Promise<void> {
  const intent = resolveChatIntent(request.command, request.prompt);

  if (intent.kind === "read" && intent.view) {
    const res = await runRead(cliPath(), intent.view, workspaceCwd());
    if (!res.ok) {
      stream.markdown(`⚠️ ${res.text}`); // friendly degrade-clean (not installed / not init)
      return;
    }
    stream.markdown(`mokata · **${intent.view}** (read-only):\n\n\`\`\`\n${res.text}\n\`\`\``);
    return;
  }

  if (intent.kind === "propose" && intent.verb) {
    const p = proposalForVerb(intent.verb, cliPath());
    stream.markdown(
      `mokata is **read-only in chat** — I won't run a change for you. To do it yourself ` +
      `(mokata's human gates still apply), run:\n\n\`\`\`\n${p.cli}\n\`\`\`\n` +
      `…or the slash command **${p.slash}** in Claude Code.`
    );
    // the button STAGES the command in a terminal (the human presses Enter) — never auto-run
    stream.button({
      command: "mokata.runInTerminal",
      title: `Stage \`${p.cli}\` in a terminal`
    });
    return;
  }

  stream.markdown(chatHelp());
}

function registerChatParticipant(context: vscode.ExtensionContext): void {
  // Degrade-clean: older VS Code / no Copilot Chat -> the Chat API is absent; skip silently
  // (the panel + badge still work). We probe at runtime so the extension still activates.
  const chatApi = (vscode as { chat?: { createChatParticipant?: Function } }).chat;
  if (!chatApi || typeof chatApi.createChatParticipant !== "function") {
    return;
  }
  try {
    const participant = vscode.chat.createChatParticipant(PARTICIPANT_ID, handleChat);
    participant.iconPath = new vscode.ThemeIcon("shield");
    context.subscriptions.push(participant);
  } catch {
    // never let a chat-registration hiccup break activation
    void CHAT_API_UNAVAILABLE_MESSAGE;
  }
}

// ---------------------------------------------------------------- mokata-mcp -> Copilot (64b)
// A user-initiated, merge-safe setup of the bundled mokata-mcp server in the workspace's
// .vscode/mcp.json. This is EDITOR config (not mokata state) and is explicitly confirmed; it
// never clobbers existing servers. VS Code then asks the user to start/trust the server — that
// step stays the human's. mokata's MCP WRITE tools remain human-gated inside the server.
async function setupCopilotMcp(): Promise<void> {
  const cwd = workspaceCwd();
  const snippet = JSON.stringify(MOKATA_MCP_CONFIG, null, 2);
  if (!cwd) {
    const doc = await vscode.workspace.openTextDocument({ language: "json", content: snippet });
    await vscode.window.showTextDocument(doc);
    vscode.window.showInformationMessage(
      "Open a folder, then paste this into .vscode/mcp.json to register mokata-mcp with Copilot Chat."
    );
    return;
  }
  const choice = await vscode.window.showInformationMessage(
    "Register the bundled mokata-mcp server with Copilot Chat by adding it to .vscode/mcp.json? " +
    "VS Code will then ask you to start/trust the server (that step is yours).",
    { modal: true },
    "Create / merge .vscode/mcp.json",
    "Just show me the snippet"
  );
  if (choice === "Just show me the snippet") {
    const doc = await vscode.workspace.openTextDocument({ language: "json", content: snippet });
    await vscode.window.showTextDocument(doc);
    return;
  }
  if (choice !== "Create / merge .vscode/mcp.json") {
    return; // dismissed — nothing written
  }
  const mcpUri = vscode.Uri.joinPath(vscode.Uri.file(cwd), ".vscode", "mcp.json");
  let existing: { servers?: Record<string, unknown> } = {};
  try {
    const bytes = await vscode.workspace.fs.readFile(mcpUri);
    existing = JSON.parse(Buffer.from(bytes).toString("utf8"));
  } catch {
    existing = {}; // absent or unreadable -> create fresh
  }
  const servers = { ...(existing.servers || {}) };
  if (servers.mokata) {
    vscode.window.showInformationMessage("mokata-mcp is already in .vscode/mcp.json (left as-is).");
    return; // never clobber a user's existing entry
  }
  servers.mokata = MOKATA_MCP_SERVER;
  const merged = JSON.stringify({ ...existing, servers }, null, 2) + "\n";
  await vscode.workspace.fs.writeFile(mcpUri, Buffer.from(merged, "utf8"));
  const opened = await vscode.workspace.openTextDocument(mcpUri);
  await vscode.window.showTextDocument(opened);
  vscode.window.showInformationMessage(
    "Added mokata-mcp to .vscode/mcp.json. Use the Start/Trust prompt VS Code shows to enable it in Copilot Chat."
  );
}

export function activate(context: vscode.ExtensionContext): void {
  if (!cfg().get<boolean>("enable", true)) {
    return; // opt-in: the user turned mokata off in this editor
  }

  const channel = vscode.window.createOutputChannel("mokata");
  const panel = new MokataPanelProvider();
  const badge = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  badge.command = "mokata.showStatus";

  context.subscriptions.push(
    channel,
    badge,
    vscode.window.registerTreeDataProvider("mokataPanel", panel),
    vscode.commands.registerCommand("mokata.refresh", () => {
      panel.refresh();
      void refreshBadge(badge);
    }),
    vscode.commands.registerCommand("mokata.showStatus", () => showView("status", channel)),
    vscode.commands.registerCommand("mokata.showProgress", () => showView("progress", channel)),
    vscode.commands.registerCommand("mokata.showGovernance", () => showView("governance", channel)),
    vscode.commands.registerCommand("mokata.showMemory", () => showView("memory", channel)),
    vscode.commands.registerCommand("mokata.runInTerminal", () => runInTerminal()),
    vscode.commands.registerCommand("mokata.setupCopilotMcp", () => setupCopilotMcp())
  );

  // Stage 64b — the read-only @mokata Copilot Chat participant (degrades cleanly if absent).
  registerChatParticipant(context);

  // Refresh when anything under .mokata/ changes (run state, ledger, memory).
  const watcher = vscode.workspace.createFileSystemWatcher("**/.mokata/**");
  const onChange = () => {
    panel.refresh();
    void refreshBadge(badge);
  };
  watcher.onDidChange(onChange);
  watcher.onDidCreate(onChange);
  watcher.onDidDelete(onChange);
  context.subscriptions.push(watcher);

  // Periodic badge refresh (0 disables the timer; the file-watch still refreshes).
  const secs = cfg().get<number>("refreshIntervalSeconds", 30);
  if (secs > 0) {
    const timer = setInterval(() => void refreshBadge(badge), secs * 1000);
    context.subscriptions.push({ dispose: () => clearInterval(timer) });
  }

  // Keep the keys of the read-only whitelist as the source of truth for what's reachable.
  void READ_COMMANDS;
  void refreshBadge(badge);
}

export function deactivate(): void {
  // nothing to tear down beyond context.subscriptions (handled by VS Code)
}
