# Automated News Forecasting Study

Two connected projects testing one question: **does the morning financial news predict
that day's trading session in a way you can act on at the open?**

Measured three separate ways, the answer is no. This repo holds both halves: the pipeline
that generated the forecasts, and the study that scored them.

```
pipeline/   the service that read the news and wrote a forecast every morning
study/      the pre-registered studies that scored those forecasts
```

## How it worked

**`pipeline/`** pulled *WSJ* and *NYT* newsletters out of Gmail each morning, summarized
each one, and wrote a dated directional forecast before the market opened. FastAPI on
SQLite, in Docker behind Caddy, scheduled in-process. It ran unattended for 87 days and
produced 281 forecasts. Decommissioned August 2026.

**`study/`** scored those forecasts against what the market actually did.
`study/forward/` holds the live forward-test job that wrote one genuine pre-open call per
weekday, with its guards.

## Methodology

- **Accuracy, not return.** Estimating expected return at macro frequency needs roughly
  16 years of data. A hit rate has bounded variance where returns do not, so scoring
  accuracy is what made this answerable in a single summer.
- **Pre-registered before any price was fetched.** Rules, instruments, and the detection
  floor for each study were committed first. See the four `PRE_REGISTRATION*.md` files.
- **Detection floors stated up front.** Each study declared the effect size it could and
  could not see, before unblinding. All three landed where their power analysis said.
- **Frozen prompts, hash-checked.** The extractor stores the SHA-256 of its prompt on
  every row and refuses to run if the committed hash no longer matches, so a silently
  edited prompt cannot contaminate a run.
- **Guards enforced in code, not in cron.** The forward job refused to write a call at or
  after 09:30 ET, checked every message against that cutoff individually, and opened the
  production database read-only. A call written after the open is not a forecast.
- **Withdrawal recorded, not hidden.** A fourth study was designed, priced honestly at
  8-12 hours of manual labelling, and withdrawn before a single call rather than half-run.

## Results

| Study | Design | Result |
|---|---|---|
| v1 | three fixed instruments | null |
| v2 | free choice of market (it chose oil) | +7.6pp, 95% CI [-0.7, +15.9] |
| v3 | human-judgment arm | withdrawn unrun, on cost |
| v4 | route by news type | behavioural effect large, money null |

The v2 interval contains zero: no detectable directional information at this sample size.
Raw scorer output is in `study/results/`.

v4 found something real that still does not make money. Changing one paragraph of
instruction moved the extractor's share of single-name picks from **6.7% to 60.1%**. A
model that steerable is not reading the news independently, it is following the frame it
was handed. 37% of its calls were unscoreable for want of a wider instrument universe,
which is the one repair still worth doing.

The forward test reached 15 of the roughly 60 trading days it needed before the pipeline
was shut down. A prospective record cannot be backfilled, so it is closed unresolved
rather than paused.

## The look-ahead defect

An earlier v2 run returned **+4.1pp** and looked like a signal.

It was reading the *afternoon* newsletters, including the 16:00 market-close wrap-ups. It
was "predicting" a session it had already read a summary of.

The result is void and is recorded as void in `PRE_REGISTRATION_V2.md`. It was found by
auditing a number that was in my favour, not by a number that looked wrong. That is the
most transferable thing here, and it generalizes well past this project: a positive result
from a pipeline you have not audited for temporal leakage is not evidence of anything.

## Stack

**Study:** Python · pandas · NumPy · SQLite · Anthropic API (Batch) · yfinance · pytest
**Pipeline:** FastAPI · Docker · Caddy · Gmail API · APScheduler · SQLite

## Note on withheld material

The newsletter corpus, the raw forecast dump, and the hand-labelling worksheets are not in
this repo. The worksheets embed verbatim publisher text, and the corpus is licensed
content. Their exact state at freeze time is recorded in `study/PRE_REGISTRATION.md` as
row counts plus a SHA-256 of the dump, which is what makes the freeze verifiable without
shipping the data.

This repo demonstrates the methodology and a negative result. It is not a strategy.
