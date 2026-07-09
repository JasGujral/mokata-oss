---
name: git
description: mokata · Git workflow & versioning — atomic commits + change-sizing riding the WriteGate, not habits.
when_to_use: Engage when an approach touches the git workflow, a branching or merge strategy, commit structure, versioning or release tagging, or a `.gitignore`/`.gitattributes` policy, when the spec's `domains` constraint names `git`, or when a change is about HOW work lands (how commits are shaped, sized, or branched) rather than what the code does. Do NOT engage for ordinary in-file code changes with no versioning or landing-strategy decision, or for a CI pipeline change which is the ci-cd domain.
---

> **mokata Agent Skill.** This is mokata's `git` domain knowledge, attached to the pipeline so
> Claude engages it automatically when work touches how a change LANDS — the commit, the branch, the
> tag. It is NOT a parallel advisory: it enriches ship (landing), and every git action that persists
> a durable record rides the **existing WriteGate** (secret-scan → human approval → audit) — it adds
> no new gate. mokata's non-negotiables still hold — durable writes are **human-gated**, and the
> commit history is treated as the audit trail it is.

⛭ mokata git active — gate: every durable write is secret-scanned, human-approved, and audited

# mokata · Git workflow & versioning

Version control is mokata's **audit trail**, not a save button. A commit is the atomic unit of change
history — the thing you revert, bisect, cherry-pick, and read a year later to learn why. How work
lands (one coherent commit vs. a grab-bag, a short-lived branch vs. a long divergence, an honest
message vs. "wip") decides whether that history stays legible and whether a bad change can be undone
cleanly. This skill encodes the landing discipline as named principles and rides the WriteGate that
already governs mokata's durable writes: a git action that records something durable is secret-scanned,
human-approved, and audited.

## Atomic commits: one coherent change per commit
A commit should be **one logically self-contained change** — it does exactly one thing, it builds and
passes tests on its own, and it can be reverted without dragging unrelated work with it. Pro Git's
commit guidelines state this directly: try to make each commit a logically separate changeset, keep
it digestible, and don't mix unrelated changes into the same commit
(https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project). The payoff is mechanical:
`git revert` undoes exactly one concern, `git bisect` lands on a commit small enough to understand,
and a reviewer reads one intent at a time. A commit that mixes a bug fix, a rename, and a feature is
un-revertable and un-reviewable — split it.

## Commit messages: say what and why, not "wip"
The message is the durable explanation the diff cannot give. Pro Git recommends a short imperative
summary line (~50 chars), a blank line, then a body explaining the **motivation** and contrasting
with prior behaviour — the *why*, because the *what* is already in the diff
(https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project). A message that records the
reason turns the history into memory a future reader (or a `bisect`) can actually use; "wip" or "fix"
throws that away. For machine-readable intent, the Conventional Commits convention adds a structured
`type(scope): summary` prefix (https://www.conventionalcommits.org/) — useful where release tooling
derives changelogs/versions from it, but the exact tooling behaviour is **UNVERIFIED** here; confirm
against the setup in use.

## Trunk-based development: integrate to mainline, keep branches short-lived
Prefer a single shared mainline (trunk) that everyone integrates into continuously, with **short-lived
branches** merged back in days, not weeks — the opposite of long-running feature branches that
diverge until the merge is a project of its own. Trunk-based development names this as its core
practice (https://trunkbaseddevelopment.com/): small, frequent merges to trunk keep integration
cheap and the mainline releasable, which is exactly what the CI/CD domain's "keep the build green"
depends on. The specific branching variants (release branches, scaled TBD) are described at the cited
source; treat any specific team-size guidance as **UNVERIFIED** until confirmed there.

## Change-sizing (advisory): small, reviewable, blast-radius-informed
Prefer the smallest change that lands the intent. A small, single-concern change is easier to review
correctly, safer to revert, and less likely to hide a defect than a large one — this pairs with
mokata's **change-sizing advisory** (SK.S3): the blast-radius of the change informs how it should be
split. This is guidance, **not a hard gate** this release — mokata surfaces an oversized/multi-concern
change and advises splitting it, but does not block on size. Use the blast-radius already computed at
brainstorm/develop to decide the split, and record the reasoning rather than landing a sprawling
commit on a hunch.

## Versioning: tag releases so a version maps to a revision
A release tag pins a human version to an exact commit, so "v1.4.2" resolves to a revision you can
check out, diff, and roll back to. Semantic Versioning encodes what a version *number* promises —
MAJOR for a breaking change, MINOR for a compatible addition, PATCH for a fix
(https://semver.org/) — so the tag communicates the compatibility contract, not just an increment.
Tag from a clean, tested mainline revision (this composes with the CI/CD domain's reproducible
release build), never from an ad-hoc local state.

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches the git workflow (branch policy,
  commit structure, `.gitignore`/`.gitattributes`, release tagging), the domain classifier puts
  `git` in the spec's `domains`, so the landing strategy is a first-class, human-approved constraint.
- **ship (landing)** — shape the change into atomic, single-concern commits with honest messages,
  integrate to a short-lived branch off trunk, and tag a release from a clean, tested revision.
- **WriteGate on git actions** — a git action that records something durable (recording a
  branch/commit-convention decision, a versioning decision) rides the EXISTING WriteGate:
  secret-scan → human approval → audit. A secret in a commit is a hard block approval cannot override.
- **memory + ledger** — a git-workflow decision (the adopted branching model, the commit convention,
  a versioning rule and why) is recorded as a typed `context` entry through the human gate and written
  to the audit ledger, so it is walkable later (P7). Record it with mokata's domain-decision path —
  never as loose prose.

## Gate (write-gate)
This skill rides **write-gate** — a BACKED mokata gate: every durable write is secret-scanned,
human-approved, and audited. A git action that persists a durable record (a recorded decision, and —
where mokata drives it — a commit that would carry a secret) goes through it; approval cannot override
a secret block. This skill adds **no new gate**: it attaches the landing discipline to the WriteGate
that already governs mokata's durable writes. Change-sizing stays **advisory** this release (mokata
advises a split, it does not block on size), and any change to an approved spec's landing scope routes
through the deviation gate.

## Grounding discipline
Decide from the repository state and the primary sources, not from assumption. Before asserting a
commit is atomic, a branch is short-lived, a tag maps to the right revision, or a message explains the
why, VERIFY it against the actual repo (read the diff, the log, the tags) and the cited source. Prefer
the primary source (Pro Git, trunk-based development, Semantic Versioning) over memory or a blog, and
CITE the URL. Flag anything you could not verify as **UNVERIFIED** rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "I'll bundle the fix, the rename, and the feature into one commit — it's faster." | A mixed commit is un-revertable and un-reviewable; make each commit one logically self-contained change (Pro Git). |
| "`wip` is fine, I'll write the real message later." | The message is the durable *why* the diff can't give; an honest summary + motivation is what a future reader and `bisect` rely on. |
| "I'll keep this feature branch going for a few weeks." | Long-lived branches diverge until the merge is its own project; integrate to trunk with short-lived branches (trunk-based development). |
| "The change is big but it's all one feature — land it whole." | Prefer the smallest change that lands the intent; use the blast-radius to split it (change-sizing advisory) — a big commit hides defects. |
| "I'll tag the release from my local working state." | A tag must map a version to a clean, tested revision; tag from mainline, not ad-hoc local state, or the version means nothing. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] each commit is one logically self-contained change that builds/passes on its own (revertable in isolation)
- [ ] each commit message has an honest imperative summary + the motivation (the why), not "wip"
- [ ] work integrated to a short-lived branch off trunk, not a long-lived divergence (trunk-based)
- [ ] an oversized/multi-concern change was surfaced and split per the blast-radius (change-sizing advisory)
- [ ] any durable git decision was recorded through the WriteGate (secret-scanned, human-approved, audited); a release tag maps a SemVer version to a clean, tested revision

## References — pulled in just-in-time (not loaded inline)

- `references/git-workflow.md` — atomic commits, commit-message discipline, trunk-based development, change-sizing, and SemVer tagging in full, each with its primary-source URL

## Contract

**CAN**
- classify the landing/git surface and shape the change into atomic, single-concern commits with honest messages
- integrate to a short-lived branch off trunk, tag a release (SemVer) from a clean tested revision, and advise a change-split from the blast-radius
- record a git-workflow decision as a typed `context` entry to memory + the ledger (human-gated)

**MUST NOT**
- persist a git-workflow decision, or drive a commit carrying a secret, without the write gate (gate: write-gate)
- change an approved spec's landing scope beyond approval (gate: deviation)
- bundle unrelated concerns into one commit, land on a long-lived divergent branch, or block on change-size (advisory — sizing is advisory this release)

**DEPENDS ON**
- the spec carrying the `git` domain (classified at brainstorm; amend-in if reached late) (advisory)
- the repository/graph for blast-radius-informed change-sizing — degrades to reading the diff/log, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
