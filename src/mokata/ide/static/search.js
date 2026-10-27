// mokata ide — Search: ONE box (0.0.26 P36) with toggles — regex (.*), match case (Aa), Meaning
// (mokata's own local code embeddings, ruling R2) — and a scope (all / changed / this session's
// files). Word/regex is rg (or the pure-Python floor — named). A Meaning answer that cannot be
// given says so; it is never word results under the word "meaning".
// SAFETY: repo text enters the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;
  // `word` / `meaning` each hold the key of the question last ASKED (so a settled query is asked
  // once — R17/G26), its AbortController, and (word) the pages drawn so far.
  const S = (MK.search = {
    regex: false, case: false, meaning: false, scope: "all", timer: 0,
    word: { key: "", ctl: null, files: [], total: 0, next: null, res: null },
    mean: { key: "", ctl: null },
  });
  const SETTLE_MS = 250;   // a query is "settled" once typing pauses this long

  function changedPaths() { return (ui.files || []).filter((f) => f.status !== "deleted").map((f) => f.path); }

  // P34 (R28): the ONE Viewing context provides `MK.sessionFiles()` — the files the session in
  // Viewing ▾ changed, or null when Viewing is not a session (the scope then says why it is off).
  function sessionPaths() { return MK.sessionFiles(); }

  function scopePaths() {
    if (S.scope === "changed") return changedPaths();
    if (S.scope === "session") return sessionPaths();
    return null;
  }

  function syncToggles() {
    for (const b of document.querySelectorAll("button[data-stoggle]")) {
      const on = !!S[b.dataset.stoggle];
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
    $("sr-meaning").hidden = !S.meaning;
    $("search-cols").classList.toggle("two", S.meaning);
    for (const b of document.querySelectorAll("button[data-sscope]")) {
      b.classList.toggle("active", b.dataset.sscope === S.scope);
      b.setAttribute("aria-checked", b.dataset.sscope === S.scope ? "true" : "false");
      if (b.dataset.sscope === "session") {
        const v = MK.viewing();
        b.disabled = MK.sessionFiles() === null;
        b.textContent = b.disabled ? "this session's files" : `${v.label}'s files`;
        b.title = b.disabled ? "pick a session in Viewing ▾ (top bar) to search only the files it changed" : `only the files ${v.label} changed`;
      }
    }
  }

  function toggle(name) {
    S[name] = !S[name];
    syncToggles();
    if (name === "meaning" && !S.meaning) abortMeaning();
    run();
  }

  function openSearch(text) {
    MK.setMode("search");
    if (text !== undefined) $("sq").value = text;
    $("sq").focus();
    run();
  }
  MK.openSearch = openSearch;

  function schedule() { clearTimeout(S.timer); S.timer = setTimeout(run, SETTLE_MS); }

  function abortWord() { if (S.word.ctl) S.word.ctl.abort(); S.word.ctl = null; }
  function abortMeaning() { if (S.mean.ctl) S.mean.ctl.abort(); S.mean.ctl = null; S.mean.key = ""; }

  // R17/R40/G26/G31 — ONE request per settled query, per source. A question already asked (the
  // same text, toggles and scope) is not asked again — Enter after the pause, or turning Meaning
  // on, re-asks nothing that is already answered or on its way. A NEW question aborts the one it
  // supersedes (its job is left, and the server stops it). Each source renders the moment it
  // arrives: word results never wait for meaning. Results on screen for an older question are
  // marked "updating…", and the header names the NEW query at once — never the old one.
  function run() {
    clearTimeout(S.timer);
    const q = $("sq").value;
    const only = scopePaths();
    if (!q.trim()) {
      abortWord(); abortMeaning();
      S.word.key = "";
      strips().word.done(); strips().meaning.done();
      renderWord(null, q); $("sr-meaning-list").replaceChildren(); $("sr-meaning-state").hidden = true;
      return;
    }
    if (S.scope === "session" && !only) { $("sr-word-note").textContent = "this session's files: pick a session in Viewing ▾ first"; return; }
    const wkey = JSON.stringify([q, S.regex, S.case, only]);
    if (wkey !== S.word.key) askWord(q, only, wkey);
    const mkey = JSON.stringify([q, only]);
    if (S.meaning && mkey !== S.mean.key) askMeaning(q, only, mkey);
    engineLine();
  }

  function askWord(q, only, key, offset) {
    const more = typeof offset === "number" && offset > 0;
    if (!more) abortWord();
    S.word.key = key;
    const ctl = new AbortController();
    S.word.ctl = ctl;
    const live = () => S.word.key === key && S.word.ctl === ctl;
    const st = strips();
    const body = { q, mode: S.regex ? "regex" : "word", case: S.case, only, offset: offset || 0 };
    if (!more) {
      $("sr-word-h").textContent = `${S.regex ? "Regex" : "Word"} search — ${q} · searching…`;
      $("sr-word-list").classList.add("stale");
      $("sr-word-note").textContent = "updating…";
    }
    st.word.say(more ? `loading more hits for ${q}…` : `searching for ${q}…`);
    api("/api/search", body, {
      signal: ctl.signal,
      onJob: (j) => { if (live()) st.word.set(j); },
      // a first page streams its hits so far; a further page appears whole (its strip shows n/N)
      onPartial: (part) => { if (live() && !more) renderWord(Object.assign({ engine: "rg" }, part), q, true, false); },
    }).catch((e) => (e && e.name === "AbortError" ? undefined : (e || null))).then((res) => {
      if (res === undefined || !live()) return;
      S.word.ctl = null;
      st.word.done();
      if (res && res.error && !res.files) { S.word.key = ""; }   // a refusal is not "answered"
      renderWord(res, q, false, more);
      engineLine();
    });
  }

  function askMeaning(q, only, key) {
    abortMeaning();
    S.mean.key = key;
    const ctl = new AbortController();
    S.mean.ctl = ctl;
    const live = () => S.mean.key === key && S.mean.ctl === ctl;
    const st = strips();
    $("sr-meaning-list").classList.add("stale");
    st.meaning.say(`ranking by meaning: ${q}…`);
    api("/api/meaning", { q, only }, { signal: ctl.signal, onJob: (j) => { if (live()) st.meaning.set(j); } })
      .catch((e) => (e && e.name === "AbortError" ? undefined : null)).then((res) => {
        if (res === undefined || !live()) return;
        S.mean.ctl = null;
        st.meaning.done();
        if (!res || res.state === "failed" || res.state === "building") S.mean.key = "";   // re-ask later
        renderMeaning(res);
        S.mean.res = res;
        engineLine();
      });
  }

  function engineLine() {
    const parts = [];
    const w = S.word.res;
    if (w && w.engine) parts.push(`word/regex: ${w.engine}${w.engine === "python-floor" ? " (pure-Python floor — rg is not on PATH)" : ""}${w.engine_note ? " — " + w.engine_note : ""}`);
    if (S.meaning) {
      const m = S.mean.res;
      parts.push(m && m.available ? "meaning: local model · linked to graph symbols · nothing leaves your machine" : m ? `meaning: ${m.state || "unavailable"}` : "meaning: asking…");
    }
    $("s-engine").textContent = parts.join("   ·   ");
  }

  function strips() {
    if (!S.strips) {
      const mk = (id) => (MK.jobs ? MK.jobs.strip($(id), "in-col") : { say() {}, set() {}, done() {} });
      S.strips = { word: mk("sr-word"), meaning: mk("sr-meaning") };
      // the strip sits right under each column's header, where the eye already is
      $("sr-word").insertBefore(S.strips.word.el || document.createTextNode(""), $("sr-word-note"));
      const mh = $("sr-meaning-state");
      if (S.strips.meaning.el) $("sr-meaning").insertBefore(S.strips.meaning.el, mh);
    }
    return S.strips;
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

  function fileCard(f, changed) {
    const card = el("div", "sr-file");
    const head = el("button", "sr-file-head mono");
    head.type = "button";
    head.append(el("span", "grow", f.path));
    // G33: a file over 1 MB is searched in full, and says how big it is
    if (f.size_note) head.append(el("span", "sr-big small", f.size_note));
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
    return card;
  }

  // `more` = this answer is a further page: its files are APPENDED to the pages already drawn
  // (G13/R16: no hit is out of reach — "Load more" asks for `next_offset`).
  function renderWord(res, q, partial, more) {
    const box = $("sr-word-list");
    const note = $("sr-word-note");
    const h = $("sr-word-h");
    const kindName = S.regex ? "Regex" : "Word";
    if (!more) { box.replaceChildren(); S.word.files = []; }
    box.classList.remove("stale");
    note.textContent = "";
    h.textContent = `${kindName} search`;
    const old = box.querySelector(".sr-more"); if (old) old.remove();
    if (!q.trim()) { note.textContent = "Type to search. Toggle .* for a regex, Aa for case, Meaning to also rank by what the code does."; return; }
    if (!res) { note.textContent = "search failed"; return; }
    MK.markSample(res);
    if (res.error) { note.textContent = res.note || res.error; return; }
    if (!partial) S.word.res = res;
    const files = res.files || [];
    const changed = new Set(changedPaths());
    for (const f of files) box.append(fileCard(f, changed));
    S.word.files = more ? S.word.files.concat(files) : files.slice();
    const total = res.total || 0;
    const nFiles = new Set(S.word.files.map((f) => f.path)).size || files.length;
    h.textContent = `${kindName} search — ${q} · ${partial || res.more ? "≥" : ""}${total} hit(s) in ${nFiles} file(s)${partial ? " so far" : " · " + res.engine}`;
    const big = (res.large_files || []).length;
    if (big) note.textContent = `${big} large file(s) over 1 MB searched in full — their size is shown`;
    if (!S.word.files.length && !files.length && !partial) { note.textContent = "No match."; return; }
    if (!partial && res.more && res.next_offset != null) {
      const b = el("button", "btn sr-more", `Load more — hits after ${res.next_offset}`);
      b.type = "button";
      b.onclick = () => { b.disabled = true; b.textContent = "loading…"; askWord(q, scopePaths(), S.word.key, res.next_offset); };
      box.append(b);
    }
  }

  function renderMeaning(res) {
    const list = $("sr-meaning-list");
    const stBox = $("sr-meaning-state");
    list.replaceChildren();
    list.classList.remove("stale");
    stBox.replaceChildren();
    stBox.hidden = true;
    if (!res) { stBox.hidden = false; stBox.append(el("b", "", "Semantic search failed to answer.")); return; }
    MK.markSample(res);
    if (res.state === "failed") {
      // R18: a failure is a stated state with its reason (Activity has the failed job), never a 500
      stBox.hidden = false;
      stBox.append(el("b", "", "Meaning search failed"), el("p", "small", res.note || res.error || ""),
        el("p", "small dim", "Word results beside it are unaffected. Ask again to retry."));
      return;
    }
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
        b.onclick = async () => { b.disabled = true; b.textContent = "fetching…"; await api("/api/semantic/fetch", {}); S.mean.key = ""; run(); };
        stBox.append(b);
      }
      stBox.append(el("p", "small dim", "Meaning search never falls back to word search under this name — the word results beside it are labelled as word results."));
      return;
    }
    if (res.state === "building") {
      // P32: the index builds as an Activity job; these hits are ranked from what it holds so far.
      const p = (res.building && res.building.progress) || {};
      stBox.hidden = false;
      stBox.append(el("b", "", "The meaning index is still building"),
        el("p", "small", `${p.done || 0}/${p.total || "?"} files embedded — showing partial results ranked from what is indexed so far. Activity (.) shows its progress and Cancel.`));
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
    for (const b of document.querySelectorAll("button[data-stoggle]")) b.onclick = () => toggle(b.dataset.stoggle);
    $("sq").oninput = schedule;
    $("sq").onkeydown = (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); run(); return; }
      if (ev.altKey && !ev.ctrlKey && !ev.metaKey) {
        const k = { r: "regex", c: "case", m: "meaning" }[(ev.key || "").toLowerCase()];
        if (k) { ev.preventDefault(); toggle(k); }
      }
    };
    for (const b of document.querySelectorAll("button[data-sscope]")) b.onclick = () => { S.scope = b.dataset.sscope; syncToggles(); run(); };
    syncToggles();
  };
  MK.onModeSearch = (mode) => { if (mode === "search") { syncToggles(); $("sq").focus(); } };
  // Viewing changed: the session scope follows it (a scope that no longer applies falls back to
  // all files, and an on-screen answer is asked again for the new scope).
  MK.onViewing.push(() => {
    if (S.scope === "session" && MK.sessionFiles() === null) S.scope = "all";
    syncToggles();
    if (ui.mode === "search" && S.scope === "session" && $("sq").value.trim()) run();
  });
})();
