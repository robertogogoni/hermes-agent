# Stage 4 scope additions (owner decision 3, 2026-07-17)

These requirements are ADDED to the Stage 4 brief (digest prompt patch + validation).

## Timezone rule (HARD)

- `LAST_DIGEST_TS` is stored in **UTC** (ISO 8601, e.g. `2026-07-17T03:10:19Z`).
- Every digest window filter converts **America/Sao_Paulo local time → UTC**
  BEFORE querying the ledger. The digest cron runs on a schedule expressed in
  local time (e.g. 23:00 Sao Paulo); that boundary must be converted to UTC
  (`23:00 -03:00` = `02:00 UTC` next day) so the window query uses UTC bounds.
- The digest prompt MUST state this conversion explicitly so the
  **00:39 vs 03:39 bug can never recur**. Concretely, the prompt must include
  a line like:

  > NOTE: LAST_DIGEST_TS and all window bounds are UTC. Convert the local
  > Sao Paulo digest time to UTC before calling interactions_search. Do not
  > query using local-time strings.

## Why

On Jul 16 the evening digest claimed "no links came through the WhatsApp
channel" while the owner had sent links at **00:39 Sao Paulo** (= **03:39 UTC**).
The window filter had been applied in local time against UTC-stored timestamps,
so the 00:39 local message (03:39 UTC) fell outside the queried window and was
missed. Storing LAST_DIGEST_TS in UTC and converting the local window to UTC
before query closes this gap permanently.

## Checklist for Stage 4 implementation

- [ ] Persist `LAST_DIGEST_TS` (UTC) in gateway/agent state; update only after
      a successful digest send.
- [ ] Digest wrapper converts the local schedule boundary to UTC before building
      the `since`/`until` window passed to `interactions_search`.
- [ ] Insert the verbatim timezone note into BOTH digest prompts (08:00, 23:00).
- [ ] Self-audit row after each digest (kind='digest', actor='cron', meta with
      per-channel counts).
