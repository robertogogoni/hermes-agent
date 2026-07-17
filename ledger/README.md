# Interaction Ledger

Unified interaction ledger across all Hermes surfaces (WhatsApp, CLI, cron, webui,
subagent). Records every exchange in a single SQLite DB (`interactions.db`)
alongside `state.db` in the Hermes data dir.

## Stage 1: DB + backfill

`build_interactions_db.py` creates `interactions.db` (exact schema from the brief)
and backfills it from `state.db`:
- `state.db.messages JOIN sessions` is the single source of truth.
- `channel` = `sessions.source` **verbatim** (whatsapp | cli | cron | webui | subagent).
- `actor` = `user` for any `role='user'` (user activity is `actor='user'` on every
  channel), `cron` for cron-sourced messages, else `hermes`.

The runtime DB (`interactions.db`) is git-ignored. It is fully regenerable.

### Run

```bash
python ledger/build_interactions_db.py            # auto-detect HERMES_HOME
HERMES_HOME=/path python ledger/build_interactions_db.py
python ledger/build_interactions_db.py --hermes-home /path --no-drop
```

No hardcoded paths: `HERMES_HOME` comes from `--hermes-home`, the `HERMES_HOME`
env var, or auto-detection of the dir containing `state.db`.

## Update safety (IMPORTANT)

The auto-updater (`scripts/install.ps1`, `hermes update`) runs on whatever branch is
currently checked out and may `git reset --hard origin/<branch>` when that branch
diverges from upstream. To keep this branch safe:

- **The live install checkout must stay on `main`.** Never switch the running
  checkout to `personal/ledger`.
- Ledger *code* lives here on `personal/ledger`; the runtime DB lives in the Hermes
  data dir (outside the repo), so repo resets never touch captured data.
- After each auto-update, rebase this branch onto the new `main`:
  `git -C <repo> fetch origin main && git -C <repo> rebase origin/main personal/ledger`
- After rebasing, push with `--force-with-lease` (NEVER plain `--force`):
  `git -C <repo> push --force-with-lease fork personal/ledger`
  `--force-with-lease` refuses to overwrite the remote ref if someone else (or
  another checkout) advanced it since your last fetch, preventing accidental
  clobber of concurrent work. Plain `--force` skips that safety check.
- Uncommitted work on `personal/ledger` would be at risk only if the live checkout
  were ever switched to it; we avoid that by keeping HEAD on `main`.

Alternative considered: a separate clone / git worktree for the ledger code, fully
outside the updater's managed repo. Safer against clobber but adds a second checkout
to maintain. Revisit at the next checkpoint if the rebase-guard proves fragile.
