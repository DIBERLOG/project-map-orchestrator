# Project Map development

This repository implements the user's local project map and orchestration workspace.
Read SPEC.md for scope and README.md for running instructions. Use Python standard
library and the current vanilla web UI unless a real requirement warrants a dependency.

Track substantive tasks and corrections in Project Map when its tools are available.
Every task/fix record automatically becomes a map node; updates are transactional events.
Do not claim all chats are captured: hooks require trust and host support. Shell edits
and external editors are not automatically captured by the apply_patch hook.

Run `python -m unittest discover -s tests -v` and `node --check web/app.js` for relevant
behavior changes. Tests use temporary fixtures. Do not reset the live data/map.sqlite3.
Preserve optimistic version checks, dependency checks and evidence gates.

No automatic commits, push, merge, releases or deployments. No autonomous agent launches
until implemented with explicit scope, isolated worktrees, resource limits and recovery.
