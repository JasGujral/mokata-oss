// mokata ide — the ONE searchable chooser (0.0.23 C5). Every pick-one-of-many control on the page
// is this combobox: type to filter, ↑/↓ to move, Enter to choose, Escape to close. WAI-ARIA 1.2
// combobox pattern (role=combobox on the input, role=listbox popup, aria-activedescendant).
//
// Markup: <div class="combo" id="X" data-label="what it picks" data-placeholder="…"></div>.
// The host element keeps the old <select> contract the rest of the page relies on:
//   host.value (get/set), a bubbling "change" event, host.hidden — so callers did not change shape.
// Options: host.combo.setOptions([{ value, label, sub? }]). Option text goes in via textContent.
"use strict";

(function () {
  const MK = (window.MK = window.MK || {});
  let seq = 0;

  function make(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function init(host) {
    if (host.combo) return host.combo;
    const id = host.id || "combo-" + (++seq);
    const listId = id + "-listbox";
    const input = make("input", "combo-input");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("aria-label", host.dataset.label || id);
    input.placeholder = host.dataset.placeholder || "type to filter";
    const toggle = make("button", "combo-toggle");
    toggle.type = "button";
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-hidden", "true");
    toggle.textContent = "▾";
    const list = make("ul", "combo-list");
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", host.dataset.label || id);
    list.hidden = true;
    host.replaceChildren(input, toggle, list);

    const st = { options: [], value: "", shown: [], active: -1, open: false };

    function labelOf(v) {
      const o = st.options.find((x) => x.value === v);
      return o ? o.label : "";
    }
    function showValue() { input.value = labelOf(st.value); }

    function filter(q) {
      const terms = String(q || "").toLowerCase().split(/\s+/).filter(Boolean);
      return st.options.filter((o) => {
        const hay = (o.label + " " + (o.sub || "") + " " + o.value).toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }

    function render() {
      list.replaceChildren();
      st.shown.forEach((o, k) => {
        const li = make("li", "combo-opt" + (k === st.active ? " active" : ""));
        li.id = `${id}-opt-${k}`;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", o.value === st.value ? "true" : "false");
        li.append(make("span", "combo-opt-label", o.label));
        if (o.sub) li.append(make("span", "combo-opt-sub", o.sub));
        // mousedown, not click: the input keeps focus, so blur does not close the list first.
        li.addEventListener("mousedown", (ev) => { ev.preventDefault(); choose(o.value); });
        li.addEventListener("mousemove", () => { if (st.active !== k) { st.active = k; paint(); } });
        list.append(li);
      });
      if (!st.shown.length) list.append(make("li", "combo-empty", "no match"));
      paint();
    }

    function paint() {
      [...list.querySelectorAll("li.combo-opt")].forEach((li, k) => li.classList.toggle("active", k === st.active));
      const cur = st.shown[st.active];
      if (cur) {
        input.setAttribute("aria-activedescendant", `${id}-opt-${st.active}`);
        const li = document.getElementById(`${id}-opt-${st.active}`);
        if (li && li.scrollIntoView) li.scrollIntoView({ block: "nearest" });
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    // The list is position:fixed, placed under (or above) the input, so a scrolling column can
    // never clip it. CSSOM style writes are allowed under the page's strict CSP.
    function place() {
      const r = input.getBoundingClientRect();
      const below = window.innerHeight - r.bottom;
      const h = Math.min(260, Math.max(120, Math.max(below, r.top) - 12));
      list.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - Math.max(r.width, 200) - 4))}px`;
      list.style.width = `${Math.max(r.width, 200)}px`;
      list.style.maxHeight = `${h}px`;
      if (below < 140 && r.top > below) { list.style.top = ""; list.style.bottom = `${window.innerHeight - r.top + 2}px`; }
      else { list.style.bottom = ""; list.style.top = `${r.bottom + 2}px`; }
    }

    function open(q) {
      st.shown = filter(q);
      const at = st.shown.findIndex((o) => o.value === st.value);
      st.active = at >= 0 ? at : (st.shown.length ? 0 : -1);
      st.open = true;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      host.classList.add("open");
      place();
      render();
    }

    function close() {
      st.open = false;
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      host.classList.remove("open");
      showValue();
    }

    function choose(v) {
      const changed = v !== st.value;
      st.value = v;
      close();
      if (changed) host.dispatchEvent(new Event("change", { bubbles: true }));
    }

    input.addEventListener("input", () => open(input.value));
    input.addEventListener("focus", () => { if (input.select) input.select(); });
    // Opening by click selects the shown label, so typing REPLACES it and filters from scratch.
    input.addEventListener("click", () => { if (!st.open) { open(""); if (input.select) input.select(); } });
    input.addEventListener("blur", () => { if (st.open) close(); });
    toggle.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      if (st.open) close(); else { input.focus(); open(""); }
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        ev.stopPropagation();
        if (!st.open) { open(""); return; }
        if (!st.shown.length) return;
        const d = ev.key === "ArrowDown" ? 1 : -1;
        st.active = (st.active + d + st.shown.length) % st.shown.length;
        paint();
      } else if (ev.key === "Enter") {
        if (!st.open) return;
        ev.preventDefault();
        ev.stopPropagation();
        const cur = st.shown[st.active];
        if (cur) choose(cur.value); else close();
      } else if (ev.key === "Escape") {
        if (!st.open) return;
        ev.preventDefault();
        ev.stopPropagation();
        close();
      } else if (ev.key === "Tab" && st.open) {
        close();
      }
    });

    // A column that scrolls under an open list (the user's wheel, or a live refresh shifting the
    // content) moves the list WITH the input; once the input leaves that column's view, it closes.
    // The input's own horizontal scroll while typing, and the list's own scroll, are not that.
    window.addEventListener("scroll", (ev) => {
      const t = ev.target;
      if (!st.open || t === list || t === input) return;
      if (!(t === document || (t && t.contains && t.contains(host)))) return;
      place();
      const r = input.getBoundingClientRect();
      const c = t === document ? { top: 0, bottom: window.innerHeight } : t.getBoundingClientRect();
      if (r.bottom < c.top || r.top > c.bottom) close();
    }, true);
    window.addEventListener("resize", () => { if (st.open) place(); });

    const api = {
      input, list,
      setOptions(opts, keep) {
        st.options = (opts || []).map((o) => (typeof o === "object" && o !== null
          ? { value: String(o.value), label: String(o.label ?? o.value), sub: o.sub ? String(o.sub) : "" }
          : { value: String(o), label: String(o), sub: "" }));
        const want = keep === undefined ? st.value : keep;
        st.value = st.options.some((o) => o.value === want) ? want : (st.options[0] ? st.options[0].value : "");
        input.disabled = !st.options.length;
        // A refresh while the user is typing keeps their text (and the filter it implies).
        if (st.open) open(input.value); else if (document.activeElement !== input) showValue();
      },
      get value() { return st.value; },
      set value(v) { const s = String(v ?? ""); if (st.options.some((o) => o.value === s)) { st.value = s; showValue(); } },
      get options() { return st.options.slice(); },
      open, close,
    };
    Object.defineProperty(host, "value", {
      configurable: true, get: () => api.value, set: (v) => { api.value = v; } });
    host.combo = api;
    // A host may carry its options inline: data-options="1,2,3" data-value="2".
    if (host.dataset.options) api.setOptions(host.dataset.options.split(","), host.dataset.value);
    return api;
  }

  MK.combo = (idOrEl) => {
    const host = typeof idOrEl === "string" ? document.getElementById(idOrEl) : idOrEl;
    return host ? init(host) : null;
  };
  for (const host of document.querySelectorAll(".combo")) init(host);
})();
