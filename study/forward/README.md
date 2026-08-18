# Droplet: daily forward-label job

Deployed to `/opt/forecast-labels/` on the Pulse droplet (DROPLET_IP).

## Why it exists

Amendment 1 of `../PRE_REGISTRATION.md` requires forward labels to be written
**before** that session's outcome exists. Batch-extracting months later would
make them retrospective and forfeit the whole point of forward testing. So the
extraction has to run every morning on the always-on machine, not on a laptop.

## Layout on the droplet

```
/opt/forecast-labels/
  daily_label.py         the job
  run.sh                 wrapper; pulls ANTHROPIC_API_KEY out of /root/pulse.env
  EXTRACTOR_PROMPT.md     copy of the frozen prompt (hash-checked at runtime)
  forecast_labels.db      the forward record (separate from pulse.db)
  label.log              append-only run log
  .venv/                 anthropic + pydantic
```

Cron (`crontab -l` as root):

```
*/10 11-14 * * 1-5 /opt/forecast-labels/run.sh >> /opt/forecast-labels/label.log 2>&1
```

`*/10` over 11-14 UTC spans 07:00-10:50 EDT and 06:00-09:50 EST. The schedule is
deliberately loose because the real window is enforced in code, so DST cannot
shift it. Runs that find nothing, or find the day already labelled, exit in
milliseconds; only the first successful run of the day calls the API.

## The three guards

1. **Prospective.** Refuses to write at or after 09:30 America/New_York. Once the
   session has opened the outcome exists and the label is no longer prospective.
   Timezone-aware, so DST cannot defeat it.
2. **Prompt hash.** Refuses to run unless `EXTRACTOR_PROMPT.md` hashes to
   `56764fad48373a4dbacb10e5cd09e4386d3320c24ca28a17407c3b63adc42f65`. Silent
   prompt drift on the server would invalidate every label written after it.
3. **Read-only source.** `pulse.db` is opened `mode=ro`. This job cannot corrupt
   the production database it reads.

## Checking on it

```bash
ssh root@DROPLET_IP 'tail -20 /opt/forecast-labels/label.log'
ssh root@DROPLET_IP 'cd /opt/forecast-labels && python3 -c "
import sqlite3; c=sqlite3.connect(\"forecast_labels.db\")
print(c.execute(\"select count(distinct date_et) from labels\").fetchone()[0], \"days\")"'
```

## Flags (for testing only)

- `--dry-run` runs the full path on the most recent pre-open forecast, writes
  nothing, skips the time guard.
- `--db PATH --allow-any-date` exercises the write path against a throwaway DB
  without touching the real forward record.

## Deployment notes

- `python3.12-venv` had to be apt-installed; the box had Python 3.12 but no venv
  module, and Pulse itself runs from a Dockerfile rather than a host venv.
- `/root/pulse.env` contains a line that is not `KEY=value`, so `. pulse.env`
  emits an error. `run.sh` greps the single key out instead of sourcing it.
- The API key is **not** duplicated here. Rotating it in `/root/pulse.env` is
  sufficient.

## Merging the forward record back

The forward labels live on the droplet; the retrospective 61 days live in
`../news_corpus.db`. When re-scoring, pull the droplet table down and union it,
keeping the `prospective` flag so the two groups can be reported separately as
Amendment 1 requires.

---

# Scorecard page

**https://DROPLET-IP.nip.io/scorecard/** — regenerated daily, mobile-friendly,
light and dark.

Served as a **static file directly by Caddy**, so the Pulse container and its code
are untouched. The Caddyfile gained one `handle_path /scorecard*` block ahead of
the existing `reverse_proxy`; the original was backed up to
`/etc/caddy/Caddyfile.bak.<epoch>` and `caddy validate` was run before reload.
Both routes were confirmed 200 afterwards.

## Cron (root)

```
*/10 11-14 * * 1-5  run.sh    # label the morning digest, pre-open
45   14    * * 1-5  build.sh  # rebuild page after labelling
0    22    * * 1-5  build.sh  # rebuild after the 16:00 ET close, when the
                              # day's closing price exists
```

## Design notes

Built against the `dataviz` skill's method. The palette is a single categorical
slot (blue `#2a78d6` light / `#3987e5` dark) and passed the validator's six checks
in both modes; contrast against the dark surface measures 4.79:1.

Two decisions worth keeping:

- **The uncertainty band is the loud element, not the point estimate.** The CI
  renders as a filled wash spanning ~86% of its track with the estimate as a 2px
  rule inside it, so "this number is noise" is legible before you read a word.
  Verified in-browser: no horizontal overflow, zero-line inside the fill, point
  inside the fill, both modes.
- **Nothing is colored red or green.** A loss painted red would editorialise a
  figure that is statistically meaningless. The minus sign carries it; text stays
  in ink tokens, per the skill's rule that text never wears the data color.

Single series, so no legend (the title names what is plotted). A table view of
the last 15 trades is included as the non-color fallback.

## Data layout

The droplet is now the single source of truth for labels. `forecast_labels.db`
holds all 61 retrospective days (`prospective=0`, seeded from `news_corpus.db`)
plus every forward day the cron adds (`prospective=1`). The page reports the
split, so Amendment 1's requirement to separate the two groups is preserved.
