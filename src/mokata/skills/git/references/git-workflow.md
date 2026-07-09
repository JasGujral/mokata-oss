# Git workflow & versioning — primary sources (JIT detail)

Pulled in just-in-time when the Git-workflow skill is engaged and the heavier detail is needed.
Authored clean-room in mokata's own words; every external claim is anchored to a primary source below
(the Pro Git book / official git docs, trunk-based development, Conventional Commits, and Semantic
Versioning). Where a specific tool behaviour could not be verified against the live source at
authoring time, it is marked **UNVERIFIED** and must be confirmed at the cited URL before it is relied
on.

## Atomic commits — one coherent change per commit

A commit is the atomic unit of change history — the thing you revert, bisect, cherry-pick, and read
later. Pro Git's commit guidelines: make each commit a **logically separate changeset**, keep it
digestible, don't mix unrelated changes into one commit, and don't ship half-done work as a commit.
Source: https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project

The payoff is mechanical: `git revert` undoes exactly one concern, `git bisect` lands on a commit
small enough to understand, and a reviewer reads one intent at a time. A commit mixing a fix, a
rename, and a feature is un-revertable and un-reviewable — split it.

## Commit messages — the durable why

Pro Git recommends: a short **imperative** summary line (~50 characters), a blank line, then a body
explaining the **motivation** for the change and contrasting it with prior behaviour — the *why*,
because the *what* is already visible in the diff. Source (same guidelines page):
https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project

For machine-readable intent, **Conventional Commits** adds a structured `type(scope): summary` prefix
(e.g. `fix:`, `feat:`), which release tooling can parse to derive changelogs and version bumps.
Source: https://www.conventionalcommits.org/ — the exact tooling behaviour is **UNVERIFIED** here;
confirm against the release setup actually in use.

## Trunk-based development — short-lived branches, continuous integration to mainline

Prefer a single shared mainline (trunk) that everyone integrates into continuously, with **short-lived
branches** merged back in days, not weeks. This is the core practice of trunk-based development, and
the opposite of long-running feature branches that diverge until the merge is a project of its own.
Small, frequent merges keep integration cheap and the mainline releasable — which is what the CI/CD
domain's "keep the build green" depends on. Source: https://trunkbaseddevelopment.com/

The specific branching variants (release branches, scaled TBD with short-lived feature branches) are
described at the cited source; treat any specific team-size guidance as **UNVERIFIED** until confirmed
there.

## Change-sizing (advisory) — small, reviewable, blast-radius-informed

Prefer the smallest change that lands the intent. A small, single-concern change is easier to review
correctly, safer to revert, and less likely to hide a defect than a large one. This pairs with
mokata's **change-sizing advisory** (SK.S3): the blast-radius computed at brainstorm/develop informs
how the change should be split. It is guidance, **not a hard gate** this release — mokata surfaces an
oversized or multi-concern change and advises a split, but does not block on size.

## Versioning — tag a version to a revision (SemVer)

A release tag pins a human version to an exact commit, so a version string resolves to a revision you
can check out, diff, and roll back to. **Semantic Versioning** encodes what the number promises:
MAJOR for a breaking change, MINOR for a backward-compatible addition, PATCH for a backward-compatible
fix. Source: https://semver.org/

Tag from a clean, tested mainline revision (this composes with the CI/CD domain's reproducible release
build), never from an ad-hoc local working state — otherwise the version does not reliably map to
buildable source.

## How a git-workflow decision becomes a recorded result in mokata

A git-workflow decision — the adopted branching model, the commit convention, a versioning rule and
why — is recorded as a typed `context` memory item through the human-gated **WriteGate** (secret-scan
→ human approval → audit) and written to the audit ledger under the `domain` kind, so it is walkable
later (P7). Every git action that persists a durable record rides that existing WriteGate; a secret in
a commit mokata drives is a hard block approval cannot override. This skill adds **no new gate** — it
attaches the landing discipline to the WriteGate that already governs mokata's durable writes, and
change-sizing stays advisory.
