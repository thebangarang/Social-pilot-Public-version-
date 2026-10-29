# AGENTS.md — social-pilot-public

**What this is.** The public showcase mirror of `social-pilot`: the same application with the client-shaped parts
removed, published so the work can be shown without handing anyone the private tree. The private repository is the
project; this one is the shop window.

## Local rules

- **Nothing real enters this tree.** Synthetic data only, and the demo database under `data/` stays `0600` — it was
  world-readable once, which is the kind of finding a privacy rule exists to catch.
- **The mirror is public on purpose**, and that is declared in `~/hermes-admin/public-by-design.json` with its
  reason. It is not a licence to publish anything else: the private tree, any client row, any credential or any
  path from this machine stays out.
- **Anything published here is published to strangers.** Before a file is committed, read it as a stranger would:
  no hostnames, no keys, no paths under `/home/hermes`, no screenshot with a real name on it.
- **Pushing is the operator's act.** A commit here is local until he says publish.

## Where the state is

The upstream project's `worklog.md` carries the history of the application; this directory's `worklog.md` carries
only what is specific to the mirror. `SECURITY.md` states what the demo does and does not defend.
