// mokata ide — Search: Word and Regex (rg, or the pure-Python floor — named), and Meaning
// (mokata's own local code embeddings, ruling R2). A Meaning answer that cannot be given says so;
// it is never word results under the word "meaning".
// SAFETY: repo text enters the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;
  const S = (MK.search = { kind: "word", gen: 0, timer: 0 });

  function changedPaths() { return (ui.files || []).filter((f) => f.status !== "deleted").map((f) => f.path); }

  function setKind(kind) {
    S.kind = kind;
    for (const b of document.querySelectorAll("button[data-skind]")) b.classList.toggle("active", b.dataset.skind === kind);
    $("sr-meaning").hidden = kind !== "meaning";
    $("search-cols").classList.toggle("two", kind === "meaning");
    run();
  }

  function openSearch(text) {
    MK.setPanel("search", true);
    MK.setMode("search");
    if (text !== undefined) $("sq").value = text;
    $("sq").focus();
    run();
  }
  MK.openSearch = openSearch;

  function schedule() { clearTimeout(S.timer); S.timer = setTimeout(run, 180); }

  async function run() {
    const gen = ++S.gen;
    const q = $("sq").value;
    // Nothing typed = nothing to ask: no repo walk at page load or on an empty box.
    if (!q.trim()) { renderWord({}, q); $("sr-meaning-list").replaceChildren(); return; }
    const only = $("s-changed").checked ? changedPaths() : null;
    const body = { q, mode: S.kind === "regex" ? "regex" : "word", case: $("s-case").checked, only };
    const jobs = [api("/api/search", body).catch(() => null)];
    if (S.kind === "meaning") jobs.push(api("/api/meaning", { q, only }).catch(() => null));
    const [word, meaning] = await Promise.all(jobs);
    if (gen !== S.gen) return;
    renderWord(word, q);
    if (S.kind === "meaning") renderMeaning(meaning);
    const parts = [];
    if (word && word.engine) parts.push(`word/regex: ${word.engine}${word.engine === "python-floor" ? " (pure-Python floor — rg is not on PATH)" : ""}${word.engine_note ? " — " + word.engine_note : ""}`);
    if (S.kind === "meaning") parts.push(meaning && meaning.available ? "meaning: local model · linked to graph symbols · nothing leaves your machine" : "meaning: unavailable");
    $("s-engine").textContent = parts.join("   ·   ");
  }

  function highlight(text, spans) {
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const [a, b] of spans || []) {
      if (a < at) continue;
      frag.append(document.createTextNode(text.slice(at, a)), el("b", "m", text.slice(a, b)));
      at = b;
    }
    frag.append(document.createTextNode(text.slice(at)));
    return frag;
  }

  function renderWord(res, q) {
    const box = $("sr-word-list");
    box.replaceChildren();
    const note = $("sr-word-note");
    note.textContent = "";
    const h = $("sr-word-h");
    h.textContent = S.kind === "regex" ? "Regex search" : "Word search";
    if (!res) { note.textContent = "search failed"; return; }
    MK.markSample(res);
    if (res.error) { note.textContent = res.note || res.error; return; }
    if (!q.trim()) { note.textContent = "Type to search. Word = the literal text; Regex = a regular expression."; return; }
    const files = res.files || [];
    h.textContent = `${S.kind === "regex" ? "Regex" : "Word"} search — ${q} · ${res.total} hit(s) in ${files.length} file(s) · ${res.engine}`;
    if (res.truncated) note.textContent = `stopped at ${res.total} hits — narrow the search`;
    if (!files.length) { note.textContent = "No match."; return; }
    const changed = new Set(changedPaths());
    for (const f of files) {
      const card = el("div", "sr-file");
      const head = el("button", "sr-file-head mono");
      head.type = "button";
      head.append(el("span", "grow", f.path));
      if (changed.has(f.path)) head.append(el("span", "chg small", "changed"));
      head.append(el("span", "dim small", String(f.matches.length)));
      head.onclick = () => MK.openFile(f.path, f.matches[0].line, null);
      card.append(head);
      for (const m of f.matches.slice(0, 50)) {
        const row = el("button", "sr-line mono");
        row.type = "button";
        row.append(el("span", "ln", m.line));
        const t = el("span", "lt"); t.append(highlight(m.text, m.spans)); row.append(t);
        row.onclick = () => MK.openFile(f.path, m.line, null);
        card.append(row);
      }
      if (f.matches.length > 50) card.append(el("div", "dim small pad", `… ${f.matches.length - 50} more in this file`));
      box.append(card);
    }
  }

  function renderMeaning(res) {
    const list = $("sr-meaning-list");
    const stBox = $("sr-meaning-state");
    list.replaceChildren();
    stBox.replaceChildren();
    stBox.hidden = true;
    if (!res) { stBox.hidden = false; stBox.append(el("b", "", "Semantic search failed to answer.")); return; }
    MK.markSample(res);
    if (!res.available) {
      stBox.hidden = false;
      stBox.append(el("b", "", res.note || "semantic unavailable"));
      if (res.install) {
        const p = el("p", "", "Install the local model: ");
        p.append(el("code", "", res.install));
        stBox.append(p);
      }
      if (res.can_fetch) {
        const b = el("button", "btn", "Fetch the model once (~30 MB from Hugging Face)");
        b.type = "button";
        b.onclick = async () => { b.disabled = true; b.textContent = "fetching…"; await api("/api/semantic/fetch", {}); run(); };
        stBox.append(b);
      }
      stBox.append(el("p", "small dim", "Meaning search never falls back to word search under this name — the word results beside it are labelled as word results."));
      return;
    }
    if (!(res.q || "").trim()) { list.append(el("li", "dim small", "Describe what you are looking for, in words.")); return; }
    if (!res.results.length) { list.append(el("li", "dim small", "Nothing above the model's noise floor.")); return; }
    const changed = new Set(changedPaths());
    res.results.forEach((r, i) => {
      const li = el("li", "sem-card" + (i === 0 ? " best" : ""));
      const head = el("button", "sem-head");
      head.type = "button";
      head.append(el("span", "kind", r.kind === "class" ? "cls" : r.kind === "module" ? "mod" : "fn"),
                  el("b", "mono", r.name), el("span", "dim small mono grow", `${r.path}:${r.line}`));
      if (changed.has(r.path)) head.append(el("span", "chg small", "changed"));
      head.append(el("span", "dim small", r.score.toFixed(2)));
      if (i === 0) head.append(el("span", "best-tag small", "best match"));
      head.onclick = () => MK.openFile(r.path, r.line, r.kind === "module" ? null : r.name);
      li.append(head);
      list.append(li);
    });
    list.append(el("li", "dim small", `${res.indexed_chunks} chunks indexed locally under .mokata/ · noise floor ${res.noise_floor}`));
  }

  MK.wireSearch = function wireSearch() {
    for (const b of document.querySelectorAll("button[data-skind]")) b.onclick = () => setKind(b.dataset.skind);
    $("sq").oninput = schedule;
    $("sq").onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); run(); } };
    $("s-case").onchange = run;
    $("s-changed").onchange = run;
    setKind("word");
  };
  MK.onModeSearch = (mode) => { if (mode === "search") $("sq").focus(); };
})();
