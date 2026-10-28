"""SE.S1/S2 — skill-triggering evals: the corpus, the deterministic CI gate, and the replay check.

Claude Code decides whether to invoke a skill (or a model-invocable command) from the listing text:
the skill's name, its `description` and its `when_to_use`, truncated at 1,536 characters. A skill
that never triggers is dead weight; one that triggers on the wrong request is worse. This module
grades that surface WITHOUT a model, an API key or a network, so it can gate every CI run.

THE UNITS are DERIVED, never typed: every `<skills>/<name>/SKILL.md` and every `<commands>/<name>.md`
under the directories `.claude-plugin/plugin.json` names. A name present in both is one unit (and
the two frontmatters must agree, or Claude is shown two triggers for one name).

THE CORPUS (`evals/skill_triggers/corpus/<unit>.json`), one file per unit:
    triggers   phrases copied VERBATIM from the unit's listing text — the phrases recorded
               positives rely on;
    positive   >= 5 prompts that must trigger the unit;
    near_miss  >= 5 prompts that must NOT trigger it; >= 1 of them names `expect`, the unit that
               should win instead (a cross-skill confusion).

THE DETERMINISTIC GATE (`python scripts/skill_evals.py gate`, run by ci.yml's `skill-evals` job):
  (a) a lint of the listing text — present, within the 1,536-char cap, `Engage when` + `Do NOT`
      shape, skill/command twins agree, every corpus trigger still verbatim present, and no two
      units' trigger vocabularies overlap past OVERLAP_MAX (IDF-weighted Jaccard);
  (b) the corpus against a LEXICAL selector model (IDF-weighted stem + bigram overlap with the
      `Engage when` half, a penalty for the `Do NOT` half): a positive keeps its unit in the top
      POSITIVE_MAX_RANK; a near-miss never contains every word of one of the unit's triggers; a
      confusion's expected unit outranks the unit;
  (c) REPLAY MOCKS (`evals/skill_triggers/replay/decisions.json`): recorded triggering decisions,
      prompt -> chosen unit, each with the trigger phrases it relied on and the lexical score/rank
      snapshot at record time. A description edit that removes a phrase a recorded positive relied
      on, or drags a recorded positive out of the top ranks, or lifts a recorded near-miss to the
      top, is red.

WHAT THE GATE PROVES, AND WHAT IT CANNOT (stated, because a green gate is easy to over-read):
  PROVES   every shipped unit has a corpus; its listing text is well-formed and under the cap; the
           phrases its recorded decisions relied on are still there; no two units have drifted into
           the same vocabulary; the corpus still separates under a fixed, inspectable lexical model.
  CANNOT   prove Claude will choose the skill. Claude's selection is a model judgement over the
           whole listing, sensitive to model version, other installed plugins and conversation
           context. The lexical model is a proxy that catches the regressions a description EDIT
           causes (a removed phrase, a vocabulary collision); it cannot see paraphrase, intent or
           model drift. That is the live tier's job: `scripts/skill_evals_live.py`, owner-run at
           the cut on the owner's own Claude Code login (never CI, never an API key), which runs
           the same corpus through `claude plugin eval` / `claude -p` and re-records the replay
           fixtures from real decisions.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EVAL_DIR = os.path.join("evals", "skill_triggers")
CORPUS_DIR = os.path.join(EVAL_DIR, "corpus")
REPLAY_FILE = os.path.join(EVAL_DIR, "replay", "decisions.json")

#: Claude Code truncates `description` + `when_to_use` at this many characters in the listing.
LISTING_CAP = 1536
#: A description shorter than this cannot say what the unit does AND when to use it.
MIN_DESCRIPTION = 30
MIN_POSITIVE = 5
MIN_NEAR_MISS = 5
MIN_CONFUSION = 1
#: A positive must keep its unit within this many places of the top under the lexical model.
POSITIVE_MAX_RANK = 3
#: Max IDF-weighted Jaccard between two units' `Engage when` vocabularies. MEASURED, not chosen:
#: the highest pair on the 0.0.26 tree is reported by `overlap` (see the stage report); this
#: ceiling sits above it with headroom, and a clone of one unit's text into another is ~1.0.
OVERLAP_MAX = 0.5
#: A recorded decision's lexical score may move this far (fraction) before the replay check reds.
REPLAY_DRIFT = 0.5
#: Live tier pass-rate threshold recorded for the cut (owner-run; see skill_evals_live.py).
LIVE_THRESHOLD = 0.8

UNIT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PREFIX = re.compile(r"^\s*mokata\s*·\s*", re.I)
NEGATION = re.compile(r"\bdo not\b", re.I)
NEGATION_WORD = re.compile(r"\b(?:do not|not|never)\b", re.I)
CLAUSE = re.compile(r"(?<=[.;])\s+")

STOPWORDS = frozenset("""
a about above after again against all already also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each either else etc
every few for from further had has have having he her here hers him his how i if in into is it its
itself just me more most my no nor not now of off on once only or other our ours out over own per
same she should so some such than that the their theirs them then there these they this those
through to too under until up upon us very via was we were what whatever when where whether which
while who whom why will with within without would yet you your yours e.g i.e eg ie
mokata engage user asks ask want wants wanted please let lets make sure its it's i'm i've don't
can't won't let's s t
""".split())


# ---------------------------------------------------------------------------------------------
# Units — derived from the plugin manifest's skills/commands directories.
# ---------------------------------------------------------------------------------------------

def _frontmatter(md: str) -> Dict[str, str]:
    """The same small `key: value` shape `mokata.agent_skills.parse_frontmatter` reads."""
    try:
        from mokata.agent_skills import parse_frontmatter
    except ImportError:          # pragma: no cover - only when src/ is not importable
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from mokata.agent_skills import parse_frontmatter
    return parse_frontmatter(md)


@dataclass
class Unit:
    name: str
    kinds: List[str]                    # "skill" and/or "command"
    paths: List[str]                    # repo-relative, '/'-separated
    description: str
    when_to_use: str
    twins_agree: bool = True

    @property
    def listing(self) -> str:
        """The text Claude Code shows for the unit (description + when_to_use)."""
        return (self.description + " " + self.when_to_use).strip()

    def _split(self) -> Tuple[str, str]:
        """(engage half, boundary half). A clause (split at `.`/`;`) is cut at its first
        negation (`do not`, `not`, `never`): the words after it are a boundary — `Repair, not
        first-time setup` must not count `setup` as a reason to fire."""
        body = PREFIX.sub("", self.description) + " " + self.when_to_use
        pos, neg = [], []
        for clause in CLAUSE.split(body):
            m = NEGATION_WORD.search(clause)
            if m:
                pos.append(clause[:m.start()])
                neg.append(clause[m.end():])
            else:
                pos.append(clause)
        return " . ".join(pos), " . ".join(neg)

    @property
    def engage_text(self) -> str:
        return self._split()[0]

    @property
    def negative_text(self) -> str:
        return self._split()[1]


def plugin_dirs(root: str) -> Tuple[str, str]:
    """(skills dir, commands dir), absolute, read from `.claude-plugin/plugin.json`."""
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    return (os.path.normpath(os.path.join(root, manifest["skills"])),
            os.path.normpath(os.path.join(root, manifest["commands"])))


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def load_units(root: str = ROOT) -> Dict[str, Unit]:
    skills_dir, commands_dir = plugin_dirs(root)
    found: Dict[str, List[Tuple[str, str, Dict[str, str]]]] = {}
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            path = os.path.join(skills_dir, name, "SKILL.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    found.setdefault(name, []).append(("skill", path, _frontmatter(fh.read())))
    if os.path.isdir(commands_dir):
        for fname in sorted(os.listdir(commands_dir)):
            if fname.endswith(".md"):
                path = os.path.join(commands_dir, fname)
                with open(path, encoding="utf-8") as fh:
                    found.setdefault(fname[:-3], []).append(("command", path, _frontmatter(fh.read())))
    units: Dict[str, Unit] = {}
    for name, entries in sorted(found.items()):
        fm = entries[0][2]
        key = lambda e: (e[2].get("description", ""), e[2].get("when_to_use", ""))  # noqa: E731
        units[name] = Unit(
            name=name, kinds=[e[0] for e in entries], paths=[_rel(root, e[1]) for e in entries],
            description=fm.get("description", ""), when_to_use=fm.get("when_to_use", ""),
            twins_agree=len({key(e) for e in entries}) == 1)
    return units


# ---------------------------------------------------------------------------------------------
# The lexical selector model.
# ---------------------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9][a-z0-9+#]*")
_DOUBLE = re.compile(r"([b-df-hj-kmnp-rtv-y])\1$")


def stem(word: str) -> str:
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    for suffix in ("ing", "ed"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            w = w[:-len(suffix)]
            if _DOUBLE.search(w):
                w = w[:-1]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def tokens(text: str) -> List[str]:
    """Content stems in order (stopwords dropped). Hyphen/slash/underscore split words."""
    out = []
    for raw in _WORD.findall(text.lower().replace("_", " ")):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        out.append(stem(raw))
    return out


def bigrams(seq: Sequence[str]) -> set:
    return {(a, b) for a, b in zip(seq, seq[1:])}


def _norm_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*\"'“”‘’]", "", text)).strip().lower()


def phrase_present(phrase: str, text: str) -> bool:
    return _norm_phrase(phrase) in _norm_phrase(text)


class Model:
    """IDF-weighted lexical overlap between a prompt and each unit's listing text."""

    BIGRAM_WEIGHT = 1.5
    NEGATIVE_WEIGHT = 0.5

    def __init__(self, units: Dict[str, Unit]):
        self.units = units
        self.pos: Dict[str, set] = {}
        self.neg: Dict[str, set] = {}
        self.pos_bi: Dict[str, set] = {}
        for name, u in units.items():
            seq = tokens(name.replace("-", " ")) + tokens(u.engage_text)
            self.pos[name] = set(seq)
            self.pos_bi[name] = bigrams(tokens(u.engage_text))
            self.neg[name] = set(tokens(u.negative_text)) - self.pos[name]
        n = len(units)
        df: Dict[str, int] = {}
        for s in self.pos.values():
            for t in s:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self._default_idf = math.log((n + 1) / 0.5)

    def w(self, t: str) -> float:
        return self.idf.get(t, self._default_idf)

    def score(self, prompt: str, name: str) -> float:
        seq = tokens(prompt)
        ts = set(seq)
        s = sum(self.w(t) for t in ts & self.pos[name])
        s += self.BIGRAM_WEIGHT * len(bigrams(seq) & self.pos_bi[name])
        s -= self.NEGATIVE_WEIGHT * sum(self.w(t) for t in ts & self.neg[name])
        return round(s, 4)

    def ranking(self, prompt: str) -> List[Tuple[str, float]]:
        scored = [(name, self.score(prompt, name)) for name in self.units]
        return sorted(scored, key=lambda x: (-x[1], x[0]))

    def rank(self, prompt: str, name: str) -> int:
        for i, (n, _s) in enumerate(self.ranking(prompt), 1):
            if n == name:
                return i
        return len(self.units) + 1

    def overlap(self, a: str, b: str) -> float:
        sa, sb = self.pos[a], self.pos[b]
        union = sum(self.w(t) for t in sa | sb)
        return round(sum(self.w(t) for t in sa & sb) / union, 4) if union else 0.0

    def overlaps(self) -> List[Tuple[float, str, str]]:
        names = sorted(self.units)
        out = [(self.overlap(a, b), a, b) for i, a in enumerate(names) for b in names[i + 1:]]
        return sorted(out, reverse=True)


# ---------------------------------------------------------------------------------------------
# Corpus + replay.
# ---------------------------------------------------------------------------------------------

def load_corpus(root: str = ROOT) -> Dict[str, dict]:
    d = os.path.join(root, CORPUS_DIR)
    out: Dict[str, dict] = {}
    if os.path.isdir(d):
        for fname in sorted(os.listdir(d)):
            if fname.endswith(".json"):
                with open(os.path.join(d, fname), encoding="utf-8") as fh:
                    out[fname[:-5]] = json.load(fh)
    return out


def iter_cases(corpus: Dict[str, dict]) -> Iterable[dict]:
    """Every corpus prompt as {target, kind, prompt, expect}; kind in positive|near_miss."""
    for target, entry in sorted(corpus.items()):
        for p in entry.get("positive", []):
            yield {"target": target, "kind": "positive", "prompt": p, "expect": target}
        for nm in entry.get("near_miss", []):
            yield {"target": target, "kind": "near_miss", "prompt": nm["prompt"],
                   "expect": nm.get("expect")}


def load_replay(root: str = ROOT) -> dict:
    path = os.path.join(root, REPLAY_FILE)
    if not os.path.isfile(path):
        return {"source": "absent", "decisions": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def relied_on(prompt: str, unit: str, corpus: Dict[str, dict]) -> List[str]:
    """The unit's corpus triggers that share a content word with the prompt."""
    ps = set(tokens(prompt))
    return [t for t in corpus.get(unit, {}).get("triggers", []) if ps & set(tokens(t))]


def make_decision(case: dict, chosen: Optional[str], model: Model, corpus: Dict[str, dict],
                  **extra) -> dict:
    """A replay record: the decision plus what the current descriptions say about it."""
    target = case["target"]
    rec = {"target": target, "kind": case["kind"], "prompt": case["prompt"],
           "expect": case["expect"], "chosen": chosen,
           "relied_on": relied_on(case["prompt"], chosen, corpus) if chosen else [],
           "lexical": {"target_score": model.score(case["prompt"], target),
                       "target_rank": model.rank(case["prompt"], target)}}
    rec.update(extra)
    return rec


def seed_replay(root: str = ROOT) -> dict:
    """Replay fixtures from the corpus EXPECTATIONS (chosen = what the case says should win).

    Labelled `source: "seed"` and never presented as a recording: they arm the replay check
    (relied-on phrases, lexical snapshot) until the first live run replaces them."""
    units = load_units(root)
    corpus = load_corpus(root)
    model = Model(units)
    decisions = [make_decision(c, c["expect"], model, corpus) for c in iter_cases(corpus)]
    return {"source": "seed",
            "note": "corpus expectations, not live decisions; re-record with "
                    "scripts/skill_evals_live.py (owner-run, own Claude Code login)",
            "decisions": decisions}


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False, sort_keys=False)
        fh.write("\n")


# ---------------------------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    check: str
    unit: str
    detail: str

    def line(self) -> str:
        return f"[{self.check}] {self.unit}: {self.detail}"


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, check: str, unit: str, detail: str) -> None:
        self.findings.append(Finding(check, unit, detail))


def lint(units: Dict[str, Unit], corpus: Dict[str, dict], model: Model, rep: Report) -> None:
    for name, u in units.items():
        if not UNIT_NAME.match(name):
            rep.add("D0-name", name, "unit name is not lowercase [a-z0-9-]")
        if not u.description.strip():
            rep.add("D1-description", name, "no description: Claude has nothing to select on")
        elif len(PREFIX.sub("", u.description)) < MIN_DESCRIPTION:
            rep.add("D1-description", name, f"description under {MIN_DESCRIPTION} chars")
        if len(u.listing) > LISTING_CAP:
            rep.add("D2-listing-cap", name,
                    f"description+when_to_use is {len(u.listing)} chars > {LISTING_CAP}: "
                    "Claude Code truncates the tail")
        if u.when_to_use:
            if "engage when" not in u.when_to_use.lower():
                rep.add("D3-trigger-shape", name, "when_to_use has no 'Engage when' trigger clause")
            if not NEGATION.search(u.when_to_use):
                rep.add("D3-trigger-shape", name, "when_to_use has no 'Do NOT' boundary clause")
        if not u.twins_agree:
            rep.add("D4-twins", name, "skill and command of the same name have different "
                    "description/when_to_use: " + ", ".join(u.paths))
        for trig in corpus.get(name, {}).get("triggers", []):
            if not phrase_present(trig, u.listing):
                rep.add("D5-trigger-removed", name,
                        f"corpus trigger {trig!r} is no longer in the listing text")
    worst = model.overlaps()
    rep.stats["overlap_max"] = worst[0] if worst else None
    for ov, a, b in worst:
        if ov <= OVERLAP_MAX:
            break
        rep.add("D6-overlap", f"{a}~{b}", f"trigger vocabulary overlap {ov} > {OVERLAP_MAX}")


def check_corpus(units: Dict[str, Unit], corpus: Dict[str, dict], model: Model,
                 rep: Report) -> None:
    for name in units:
        if name not in corpus:
            rep.add("C1-no-corpus", name, f"no {CORPUS_DIR}/{name}.json: every shipped "
                    "skill/command needs eval cases")
    seen: Dict[str, str] = {}
    for name, entry in corpus.items():
        if name not in units:
            rep.add("C2-stale-corpus", name, "corpus for a unit the plugin no longer ships")
            continue
        pos, nms = entry.get("positive", []), entry.get("near_miss", [])
        conf = [n for n in nms if n.get("expect")]
        if len(entry.get("triggers", [])) < 1:
            rep.add("C3-counts", name, "no triggers")
        if len(pos) < MIN_POSITIVE:
            rep.add("C3-counts", name, f"{len(pos)} positives < {MIN_POSITIVE}")
        if len(nms) < MIN_NEAR_MISS:
            rep.add("C3-counts", name, f"{len(nms)} near-misses < {MIN_NEAR_MISS}")
        if len(conf) < MIN_CONFUSION:
            rep.add("C3-counts", name, f"{len(conf)} cross-skill confusions < {MIN_CONFUSION}")
        for p in pos + [n["prompt"] for n in nms]:
            if p in seen and seen[p] != name:
                rep.add("C4-duplicate", name, f"prompt also in {seen[p]}: {p!r}")
            seen[p] = name
    top1 = {"positive": [0, 0], "near_miss": [0, 0]}
    for case in iter_cases(corpus):
        t, p = case["target"], case["prompt"]
        if t not in units:
            continue
        first = model.ranking(p)[0][0]
        tally = top1[case["kind"]]
        tally[0] += (first == t) if case["kind"] == "positive" else (first != t)
        tally[1] += 1
        if case["kind"] == "positive":
            r = model.rank(p, t)
            if r > POSITIVE_MAX_RANK or model.score(p, t) <= 0:
                top = ", ".join(f"{n}={s}" for n, s in model.ranking(p)[:3])
                rep.add("P1-positive-unmatched", t,
                        f"rank {r} > {POSITIVE_MAX_RANK} for {p!r} (top: {top})")
            continue
        ps = set(tokens(p))
        for trig in corpus[t].get("triggers", []):
            ts = set(tokens(trig))
            if ts and ts <= ps:
                rep.add("N1-near-miss-matches-trigger", t,
                        f"near-miss {p!r} contains every word of trigger {trig!r}")
        x = case["expect"]
        if x:
            if x not in units:
                rep.add("N2-confusion", t, f"expect {x!r} is not a shipped unit ({p!r})")
            elif model.score(p, x) <= model.score(p, t):
                rep.add("N2-confusion", t, f"{x} ({model.score(p, x)}) does not outrank {t} "
                        f"({model.score(p, t)}) for {p!r}")
    # Informational, not gated: how sharply the lexical model separates the corpus at top-1.
    rep.stats["lexical_positive_top1"] = f"{top1['positive'][0]}/{top1['positive'][1]}"
    rep.stats["lexical_near_miss_not_top1"] = f"{top1['near_miss'][0]}/{top1['near_miss'][1]}"


def check_replay(units: Dict[str, Unit], corpus: Dict[str, dict], model: Model, replay: dict,
                 rep: Report) -> None:
    cases = {(c["target"], c["kind"], c["prompt"]) for c in iter_cases(corpus)}
    decisions = replay.get("decisions", [])
    recorded = set()
    passed = 0
    for d in decisions:
        t, p = d["target"], d["prompt"]
        recorded.add((t, d["kind"], p))
        if (t, d["kind"], p) not in cases:
            rep.add("R1-stale-replay", t, f"recorded decision for a prompt not in the corpus: "
                    f"{p!r} (re-record with skill_evals_live.py, or drop the record)")
            continue
        if t not in units:
            rep.add("R1-stale-replay", t, f"recorded decision for a unit the plugin no longer "
                    f"ships: {p!r} (re-record with skill_evals_live.py, or drop the record)")
            continue
        chosen = d.get("chosen")
        if chosen and chosen not in units:
            rep.add("R1-stale-replay", t, f"recorded choice {chosen!r} is no longer shipped")
            continue
        good = (chosen == t) if d["kind"] == "positive" else (chosen != t)
        passed += good
        for phrase in d.get("relied_on", []):
            if chosen in units and not phrase_present(phrase, units[chosen].listing):
                rep.add("R2-relied-phrase-removed", chosen,
                        f"recorded choice for {p!r} relied on {phrase!r}, which the "
                        "description no longer contains")
        snap = d.get("lexical", {})
        score, rank = model.score(p, t), model.rank(p, t)
        if d["kind"] == "positive" and good:
            floor = snap.get("target_score", 0) * (1 - REPLAY_DRIFT)
            if rank > POSITIVE_MAX_RANK or score < floor:
                rep.add("R3-recorded-positive-drifted", t,
                        f"{p!r} was chosen at record time (score {snap.get('target_score')}, "
                        f"rank {snap.get('target_rank')}); now score {score}, rank {rank}")
        elif d["kind"] == "near_miss" and good:
            was = snap.get("target_score", 0)
            ceiling = was + abs(was) * REPLAY_DRIFT + 1.0
            if (rank == 1 and snap.get("target_rank", 1) != 1) or score > ceiling:
                rep.add("R4-recorded-near-miss-drifted", t,
                        f"{p!r} was correctly NOT routed here at record time (score "
                        f"{snap.get('target_score')}, rank {snap.get('target_rank')}); now "
                        f"score {score}, rank {rank}")
    missing = len(cases - recorded)
    rep.stats["replay_source"] = replay.get("source", "unknown")
    rep.stats["replay_decisions"] = len(decisions)
    rep.stats["replay_pass_rate"] = round(passed / len(decisions), 4) if decisions else None
    rep.stats["replay_unrecorded_cases"] = missing
    if decisions and replay.get("source") != "live":
        rep.notes.append(f"replay fixtures are source={replay.get('source')!r}: corpus "
                         "expectations, NOT live recordings — the owner re-records them at the "
                         "cut with scripts/skill_evals_live.py")
    if missing:
        rep.notes.append(f"{missing} corpus case(s) have no recorded decision yet — recorded at "
                         "the next live run (scripts/skill_evals_live.py); not a gate failure")


def gate(root: str = ROOT) -> Report:
    units = load_units(root)
    corpus = load_corpus(root)
    model = Model(units)
    rep = Report()
    rep.stats["units"] = len(units)
    rep.stats["cases"] = sum(1 for _ in iter_cases(corpus))
    lint(units, corpus, model, rep)
    check_corpus(units, corpus, model, rep)
    check_replay(units, corpus, model, load_replay(root), rep)
    return rep


# ---------------------------------------------------------------------------------------------
# `claude plugin eval` suite generation (the live tier's input; never run in CI).
# ---------------------------------------------------------------------------------------------

#: Keys `claude plugin eval` accepts in prompt.md frontmatter (an unknown key is an error).
PROMPT_KEYS = frozenset({"schema_version", "name", "description", "tags", "plugins", "runs",
                         "expected_outcome", "model", "max_turns", "timeout_seconds",
                         "allowed_tools", "append_system_prompt", "env"})
GRADER_KEYS = frozenset({"type", "weight", "arm", "tool", "input_match", "min", "max"})


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)        # a JSON string is a valid YAML scalar


def case_dir_name(case: dict, index: int) -> str:
    return f"{case['target']}--{'pos' if case['kind'] == 'positive' else 'nm'}-{index:02d}"


def generate_plugin_eval(corpus: Dict[str, dict], out_dir: str, plugin: str = "mokata",
                         runs: int = 3) -> List[str]:
    """Write one `claude plugin eval` case per corpus prompt; return the case directories.

    A positive is graded by `tool_used: Skill` matching the unit (min 1); a near-miss by the same
    grader with min 0 / max 0 and `arm: both`, the documented "must not invoke" shape. The suite is
    meant for `--ablation none` (no no-plugin arm: a trigger check has no baseline)."""
    written = []
    counters: Dict[Tuple[str, str], int] = {}
    for case in iter_cases(corpus):
        key = (case["target"], case["kind"])
        counters[key] = counters.get(key, 0) + 1
        name = case_dir_name(case, counters[key])
        d = os.path.join(out_dir, name)
        os.makedirs(os.path.join(d, "graders"), exist_ok=True)
        tags = ["skill-trigger", case["target"], case["kind"]]
        front = [
            "---",
            f"name: {name}",
            f"description: {_yaml_str(case['kind'] + ' for ' + case['target'])}",
            f"tags: [{', '.join(tags)}]",
            f"runs: {runs}",
            "max_turns: 3",
            "timeout_seconds: 180",
            "allowed_tools: [Skill]",
            "---",
            "",
        ]
        with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(front) + case["prompt"] + "\n")
        # Unit names are [a-z0-9-] (checked), so they go in unescaped: `re.escape` would emit `\-`,
        # which a JavaScript regex compiled with the `u` flag rejects.
        if not UNIT_NAME.match(case["target"]):
            raise ValueError(f"unit name {case['target']!r} is not [a-z0-9-]")
        match = "'\"skill\"\\s*:\\s*\"(?:" + plugin + ":)?" + case["target"] + "\"'"
        if case["kind"] == "positive":
            grader = ["---", "type: tool_used", "tool: Skill", f"input_match: {match}", "min: 1",
                      "---", "", f"The {case['target']} skill fired."]
        else:
            grader = ["---", "type: tool_used", "tool: Skill", f"input_match: {match}", "min: 0",
                      "max: 0", "arm: both", "---", "",
                      f"The {case['target']} skill did NOT fire (near-miss)."]
        with open(os.path.join(d, "graders", "trigger.md"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write("\n".join(grader) + "\n")
        written.append(d)
    return written


# ---------------------------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------------------------

def _print_report(rep: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": rep.ok, "stats": rep.stats, "notes": rep.notes,
                          "findings": [asdict(f) for f in rep.findings]}, indent=1))
        return
    for f in rep.findings:
        print(f.line())
    for n in rep.notes:
        print("note: " + n)
    s = rep.stats
    print(f"skill-evals gate: {s.get('units')} units, {s.get('cases')} cases, "
          f"max overlap {s.get('overlap_max')}, replay source={s.get('replay_source')} "
          f"({s.get('replay_decisions')} decisions, pass-rate {s.get('replay_pass_rate')}), "
          f"lexical top-1: positives {s.get('lexical_positive_top1')}, near-misses not-top-1 "
          f"{s.get('lexical_near_miss_not_top1')} -> "
          + ("GREEN" if rep.ok else f"RED ({len(rep.findings)} finding(s))"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="the deterministic CI gate (exit 1 on any finding)")
    g.add_argument("--json", action="store_true")
    e = sub.add_parser("explain", help="rank every unit for one prompt under the lexical model")
    e.add_argument("prompt")
    sub.add_parser("overlap", help="the ten most-overlapping unit pairs")
    sub.add_parser("seed-replay", help="(re)write the replay fixtures from the corpus "
                   "expectations, labelled source=seed")
    gen = sub.add_parser("generate-plugin-eval", help="write a `claude plugin eval` suite")
    gen.add_argument("out_dir")
    gen.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)

    if args.cmd == "gate":
        rep = gate(args.root)
        _print_report(rep, args.json)
        return 0 if rep.ok else 1
    if args.cmd == "seed-replay":
        data = seed_replay(args.root)
        write_json(os.path.join(args.root, REPLAY_FILE), data)
        print(f"wrote {len(data['decisions'])} seed decision(s) to {REPLAY_FILE}")
        return 0
    units = load_units(args.root)
    model = Model(units)
    if args.cmd == "explain":
        for name, score in model.ranking(args.prompt)[:8]:
            print(f"{score:8.3f}  {name}")
        return 0
    if args.cmd == "overlap":
        for ov, a, b in model.overlaps()[:10]:
            print(f"{ov:.4f}  {a} ~ {b}")
        return 0
    if args.cmd == "generate-plugin-eval":
        n = len(generate_plugin_eval(load_corpus(args.root), args.out_dir, runs=args.runs))
        print(f"wrote {n} case(s) under {args.out_dir}")
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
