# CLAUDE.md — BlueSky (BluePlan fork)

This directory is the [TUDelft-CNS-ATM/bluesky](https://github.com/TUDelft-CNS-ATM/bluesky)
fork that the BluePlan ecosystem runs against. It is a git submodule of the
parent `blueplan` repository; the parent's `../CLAUDE.md` and the
[ecosystem policies](../docs/ecosystem/) apply here.

## Fork relationship (read this first)

- **Branch:** `blueplan` (NOT `master` or `main`).
- **Origin remote:** `git@github.com:gndpwnd/bluesky.git`.
- **Upstream remote:** `https://github.com/TUDelft-CNS-ATM/bluesky.git`.
- **Merge base with `upstream/master`:** `6dee8d3a` (last upstream sync point).
  BluePlan-specific commits sit on top of that base, in chronological order:
  `45079865`, `92856b8b`, `54a94278`, `efcd2a68`, `a652ec4c`. See
  [README.md §"Fork-source modifications"](./README.md) for the per-commit table.
- **Submodule pin** is set in the parent repo; bumping the pin requires a
  parent-repo commit (the OWNER does that — do not commit submodule bumps from
  here).

The canonical fork/upstream contract is
[../docs/ecosystem/BLUESKY_FORK_POLICY.md](../docs/ecosystem/BLUESKY_FORK_POLICY.md).
Read it before any non-trivial change.

## Primary consumer

The fork is consumed by `../app/bluesky/` (the BluePlan runner). The runner
spawns BlueSky as a subprocess, feeds it scenarios + the NDJSON ADS-B bridge,
and captures CRELOG / screen-recording output. Any fork change that affects
runner behavior must be paired with a change in `../app/bluesky/` and the
contract tests under `../tests/` and `../adsb_analytics/tests/contracts/`.

## When to commit to the fork vs. somewhere else

- **Fork-source commit (this repo, branch `blueplan`)** — change should travel
  *with* BlueSky: bug fixes in `bluesky/`, deps in `pyproject.toml`, datalog
  format fixes (e.g. `54a94278`), runtime gates that belong in the engine (e.g.
  `efcd2a68`). Prefer the smallest possible diff against `upstream/master`;
  cherry-pickable, single-purpose commits make future rebases cheap.
- **Plugin / extension (parent `bluesky_extensions/`)** — change is
  BluePlan-glue: a new plugin (`adsbfeed_json`, `conflictcam`), a `cd_modes`
  resolver, scenario-archive helpers. These live outside the fork and get
  deployed alongside it via the parent's `deploy.sh ext` path.
- **App-level integration (parent `app/`)** — change is on the runner side of
  the seam: subprocess management, scenario generation, NDJSON relay,
  recording orchestration.

The CRELOG datalog fix (commit `54a94278` here, paired handoff
`../docs/handoffs/crelog-columns-fix-2026-05-25.md` at the parent level) is the
canonical example of a fork-local fix that should NOT have been a runtime
monkey-patch.

## Rebase / upstream-sync policy

- Prefer **rebase** of `blueplan` onto `upstream/master` (the fork commits are
  few, small, and ordered). Merging is discouraged because it loses the
  per-commit cherry-pickability that the
  [fork policy](../docs/ecosystem/BLUESKY_FORK_POLICY.md) relies on.
- Before bumping the submodule pin in the parent repo, run the BluePlan
  integration tests (`pytest -m integration` from the parent) and a full
  scenario via `app/bluesky_runner.py`. Do not bump the pin on faith.

## Out-of-scope inside the fork too

The four ecosystem non-goals apply here as well as in the parent:

- **No authentication** — BlueSky has none; do not add any. See
  [../docs/ecosystem/AUTHENTICATION_POLICY.md](../docs/ecosystem/AUTHENTICATION_POLICY.md).
- **No infrastructure scaling / orchestration** — no Kubernetes, Helm, service
  meshes, autoscalers, or load balancers. BlueSky is a single-process
  simulator.
- **No external notification webhooks** — no email, Slack, PagerDuty, Discord,
  Teams, or "alert pusher" sinks. Logs and the console are the monitoring
  channel.
- **No LLM / AI** — no chat assistants, RAG, embeddings, MCP servers, or
  hosted-model clients in the fork or its plugins.

See [../docs/ecosystem/DEVELOPMENT_SCOPE_POLICY.md](../docs/ecosystem/DEVELOPMENT_SCOPE_POLICY.md)
for the canonical statement.

## Files of interest (orientation)

- `bluesky/logging_manager.py` — heartbeat gate (`BLUESKY_HEARTBEAT`), psutil
  sampling.
- `bluesky/tools/datalog.py` — CRELOG / logging plumbing (the `54a94278` fix
  lives here).
- `bluesky/plugins/` — upstream + fork-source plugins. **`adsbfeed_json` and
  `conflictcam` live here post-2026-05-21 migration** — that is the move that
  retired the runtime-patches system. Edit them like any other fork-source
  file; do not put them back in `bluesky_extensions/plugins/`.
- `pyproject.toml` — fork-level deps (`psutil>=5.9` is a BluePlan dep, marked
  by an inline comment).
- `settings.cfg.template` — checked-in template for the runtime `settings.cfg`
  (see README §`settings.cfg.template`).

## Project docs (parent)

- [../docs/ecosystem/BLUESKY_FORK_POLICY.md](../docs/ecosystem/BLUESKY_FORK_POLICY.md)
  — fork/upstream contract.
- [../docs/handoffs/bluesky-migration-2026-05-21.md](../docs/handoffs/)
  — runtime-patches → fork-source migration handoff (see `handoffs/`).
- [README.md](./README.md) — fork-side integration overview.
