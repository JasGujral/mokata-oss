# Privacy Policy — mokata

_Last updated: 2026-06-27_

mokata is an open-source, **local-first** plugin for Claude Code. It is designed so that
your code, prompts, and project knowledge stay on your machine.

## What mokata collects

**Nothing.** mokata has **no telemetry, no analytics, and no tracking.** It does not phone
home, does not send usage data to MoStack or any third party, and contains no advertising.
The maintainers of mokata receive no data about you or your usage.

## What mokata stores, and where

mokata stores its working state **locally**, inside your project, under the `.mokata/`
directory (configuration, memory, audit ledger, and run state). This data never leaves your
machine unless **you** explicitly configure an external backend.

## Optional external services (you control these)

mokata can optionally be wired to external storage you choose and configure yourself — for
example a Postgres / pgvector database, a Neo4j graph, or an Obsidian vault for shared team
memory. These integrations are **off by default**. If you enable one, data flows only to the
endpoint **you** specify, using credentials supplied via your own environment variables;
mokata never transmits that data anywhere else. Your use of any such third-party service is
governed by that service's own privacy policy.

## Claude Code

mokata runs inside Claude Code and issues no model calls of its own — Claude Code (and the
Anthropic API behind it) is the model. Any data sent to Anthropic is governed by Anthropic's
own privacy policy, not by mokata.

## Secrets

mokata includes a secret-scanning write gate that blocks credentials and high-entropy tokens
from being written into memory, shared files, or commits. This protection runs **locally** and
sends nothing externally.

## Changes

This policy may be updated; the "Last updated" date above reflects the current version, and the
history is visible in the repository.

## Contact

Questions: **jasmeet.gujral@mostacktechnology.com** ·
Project: <https://github.com/JasGujral/mokata-oss>
