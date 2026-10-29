# Worklog — social-pilot-public

This directory is the **public demo mirror** of `social-pilot`: the same application with the client-specific parts
removed, published so the work can be shown without handing anyone the private tree. Its own history lives in the
upstream project's `worklog.md`; only what is specific to the mirror belongs here.

## 2026-10-29 — two files the isolation check asks every project for

`AGENTS.md` and this worklog were missing. This one is added; the other is a gated write (creating an
agent-instruction file needs the operator's approval), so it is still owed.

Also closed here: `data/social-pilot.db` (and its `-shm`/`-wal` companions) were `0644` — readable by anyone on the
machine — while holding the demo's rows. Set to `0600`. The file is not tracked by git; it is a local artefact of
running the mirror, and a database readable by every account is the kind of finding a privacy rule exists to catch.
