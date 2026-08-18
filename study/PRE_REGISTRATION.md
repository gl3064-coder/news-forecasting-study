# Pre-registration: does Pulse's pre-open digest contain directional information?

**Frozen:** 2026-07-27
**Status:** committed BEFORE any price data was pulled and before any outcome was computed.

Every line marked **[DECISION]** is a choice that could reasonably have gone
another way. They are fixed now so that they cannot be chosen later to suit an
answer. Changing any of them after unblinding requires an entry in the
Amendments section at the bottom, with a date and a reason.

---

## 1. The question

> On days when Pulse's 08:00 ET digest states a directional bias for an
> instrument, does that bias predict the sign of that instrument's
> same-session (open to close) return, at a rate better than the
> instrument's own base rate over the same window?

This is a **measurement** question, not a strategy. See §8 for what is
explicitly not being claimed.

---

## 2. Data state at freeze

Nothing below this point was collected after the rule was written.

| item | value |
|---|---|
| corpus DB | `news_corpus.db`, 22,614,016 bytes |
| `newsletters` | **2,049 rows**, 2025-10-23 → 2026-07-27 |
| `forecasts` | **212 rows**, 2026-05-23 → 2026-07-27 |
| forecast source dump | `forecasts_raw.jsonl`, 5,139,726 bytes |
| dump SHA-256 | `deec06e15d96dfe087a60de19d8b9e7631c38c62445c538f60fd05bcecac04c2` |
| pre-open (before 09:30 ET) days | **61** |
| of which weekdays | **46** |
| weekday range | 2026-05-25 → 2026-07-27 |

Price data: **not yet pulled.** Nothing about any outcome is known at freeze time.

---

## 3. Sample selection

**[DECISION]** One forecast per date: the **latest row generated before 09:30 ET**
on that date. Observed generation times cluster tightly at 08:0x ET.

Rationale: the 08:00 run is the only one that precedes the session it comments
on, so it is the only unbiased choice. The 11:00, 14:00 and 17:00 runs are
excluded entirely.

**[DECISION]** Weekend dates are dropped (no session). Market holidays drop
naturally when price data is absent for that date; they are not hand-removed.
Note 2026-05-25 (Memorial Day) and 2026-07-03 are expected to drop this way.

**[DECISION]** 212 rows across 63 days means ~3.4 analyses per day. Scoring more
than one per day would inflate the sample with non-independent observations and
is prohibited.

---

## 4. Instruments (frozen list)

| instrument | ticker | directional coverage observed |
|---|---|---|
| Nasdaq futures | `NQ=F` | 63/63 days |
| Crude oil | `CL=F` | 63/63 days |
| 10-year Treasury yield | `^TNX` | 52/63 days |

**Excluded, and why, decided before scoring:**

- gold (1/63 coverage), bitcoin (5/63), dollar/FX (16/63) — too sparse
- S&P / ES (15/63) — also ~0.95 correlated with NQ, so it adds no independent information

**[DECISION]** No instrument may be added to this list after unblinding. Adding
instruments post hoc is the degrees-of-freedom failure that ended the NQ Edge
bot's usable sample.

The coverage figures above come from a crude proxy (a direction word within 120
characters of an instrument mention) and are therefore an **upper bound**. The
true usable rate after blind extraction will be lower, and whatever it turns out
to be gets reported as-is.

---

## 5. Label extraction

**[DECISION]** An LLM reads the forecast text and emits, per instrument, exactly
one of `up`, `down`, `no_call`.

Binding constraints on the extractor:

1. **`no_call` is always permitted.** Forcing a binary label onto vague text
   manufactures signal. No-calls are excluded from scoring and their count is
   reported.
2. **Dates are stripped from the text before extraction.** An LLM that sees
   "July 14, 2026" may recall what markets did that day. This is a real leakage
   path and closing it is mandatory.
3. **Price data is never shown to the extractor.**
4. **One prompt, used identically for all three instruments.** NQ must not be
   privileged by having the structured `nq_game_plan` field while oil and
   treasuries sit in prose.
5. **The prompt is written once and frozen.** Iterating the prompt while
   watching the score is fitting, and is prohibited.

**Extractor validation:** a random 20 days are hand-labelled independently, and
the agreement rate with the extractor is reported alongside the main result.
This is reported whether it is flattering or not. Hand-labelling happens before
any score is computed.

---

## 6. Outcome, baseline, and the primary test

**[DECISION] Horizon: same session, open to close, on the forecast date.**
The forecast text describes an "intraday" bias, so that is the claim being
tested. Entry at the 09:30 open, exit at the 16:00 close.

Prior-close-to-close is rejected: part of the overnight move was already visible
at 08:00, so it would leak.

**Outcome:** `sign(close − open)` per instrument-day.

**[DECISION] Baseline:** each instrument's own up-rate over **the same 61-day
window**. Not 50%, and not a long-run historical average. NQ drifts upward, so a
bull-biased forecaster would look skilled against a coin-flip null while being
nothing of the sort. The window's own drift is the thing to beat.

### The primary test (one number)

> Pooled hit rate across NQ, crude and treasuries, minus each instrument's
> same-window base rate, with a 95% confidence interval from a **bootstrap that
> resamples DAYS, not instrument-days** (10,000 iterations, seed fixed at 42).

Day-clustering is how the within-day correlation gets handled. All three
instruments on one day share a single macro read, so their errors are correlated;
resampling days rather than rows accounts for that without anyone having to
guess a correlation coefficient.

**Decision rule, fixed in advance:**

- CI excludes zero and is positive → evidence of directional information at this sample size
- CI includes zero → **"no detectable directional information at this sample size."** That is the finding, and it gets written up as the result. It is not a licence to try variants.
- CI excludes zero and is negative → the digest is anti-predictive, which is also a real and reportable finding

---

## 7. Power, stated before the fact

Roughly 46 weekday forecasts, minus holidays and no-calls. Standard error of a
hit rate is `sqrt(0.25/n)`, so with day-clustering the effective sample sits
between 44 and ~130.

**Detectable effect at this size is roughly 61% or better. Realistic
forecasting skill lives at 55–60%. This pilot is therefore UNDERPOWERED and a
null result is the expected outcome.**

A null here means the sample cannot see an effect. It does **not** mean no effect
exists. Anyone quoting a null from this pilot as "the digest has no predictive
power" is overreading it, including me.

**Re-scoring schedule.** The forecast record accrues on the droplet without
intervention. Re-run this exact rule, unchanged, at:

- n ≈ 190 trading days (detectable ≥57%), expected around February 2027
- n ≈ 315 trading days (detectable ≥56%), expected around August 2027

Re-running a frozen rule on a grown sample is not multiple testing. Changing the
rule between runs would be.

---

## 8. Explicitly exploratory

These may be looked at and reported, but are **labelled exploratory** and cannot
be promoted to the headline claim:

- the cross-sectional version (is it relatively more right about oil than NQ?)
- per-instrument breakdowns
- conditioning on `signal` tier, volatility, or news volume
- the `bull_case` versus `bear_case` framing comparison
- anything involving the `nq_game_plan`'s stated entry triggers

**Not claimed by any result here:** that this is tradeable. A hit rate says
nothing about magnitude, and a forecaster can be directionally right 60% of the
time and still lose money if the 40% are larger. Direction and profitability are
separate questions, and only the first one is being tested.

---

## 9. Amendments

The original text above is left untouched. Amendments are appended here.

---

### Amendment 1 — 2026-07-27, before any price data was pulled

**Status when made:** no price data existed, no outcome was known, nothing had
been scored. This amendment was prompted by looking at the *predictor* only,
which cannot contaminate an outcome that does not yet exist.

**What was found.** The stated NQ bias across the 62 parseable pre-open
forecasts is **41 bearish, 15 bullish, 5 mixed, 1 unclear** (see
`check_bias_variance.py`, committed in `20d9a47`). The digest leans bearish by
roughly 2.7 to 1.

**Why that breaks §6 as written.** The frozen primary test was *hit rate minus
the instrument's base rate*. With a persistently bearish predictor in a market
that drifts up, that statistic conflates two different things:

1. genuine directional skill, and
2. being wrong-footed on overall bias

A forecaster with good timing but a standing bearish tilt scores badly on it.
The statistic answers "was it well calibrated to the drift," which is not the
question in §1.

**The change.** The primary statistic becomes the **conditional difference**:

> P(instrument up | commentary said up) − P(instrument up | commentary said down)
>
> pooled across NQ, CL and TNX, with a 95% CI from the same day-clustered
> bootstrap (10,000 iterations, seed 42).

Null is **zero** under no skill. Decision rule as in §6: CI excluding zero and
positive is evidence, CI containing zero is "no detectable directional
information at this sample size," CI excluding zero and negative is
anti-predictive.

**A side effect worth noting.** This statistic needs **no drift baseline at
all**. It is invariant to both the market's drift and the forecaster's tilt, so
the §6 ambiguity about whether the base rate should be computed over all 46 days
or only the scored days simply dissolves. That ambiguity is withdrawn rather
than resolved.

**The old statistic is retained as secondary** and will be reported alongside,
labelled as such. It is a legitimate measure of "did this beat always guessing
up," just not a measure of skill.

**Revised power, stated honestly before the fact.** The binding constraint is
now the **smaller bucket**, which is the 15 bullish days. Standard error of a
difference in two proportions is `sqrt(p₁(1−p₁)/n₁ + p₂(1−p₂)/n₂)`; at
n₁=15, n₂=41 and p≈0.5 that is 15.1 points, so **2 SE is roughly 30 percentage
points**. No real forecasting effect is that large.

If the bearish tilt persists at about 24% bullish, then detecting a difference of:

| effect | days needed (NQ alone) | approx. calendar |
|---|---|---|
| 20 points | ~142 | ~7 months |
| 10 points | ~568 | ~2.3 years |

Pooling three instruments could cut those figures by up to a factor of three if
CL and TNX carry independent information, which is unknown until extraction runs.

**Consequence, accepted in advance:** the historical 62-day run is a **dry run**
whose expected output is "cannot tell yet." It is being run to prove the pipeline
works, not to answer the question. The answer comes from forward accumulation.

**Forward-testing addition.** For all dates from 2026-07-28 onward, labels are
to be extracted and stored **before that session's outcome exists**, making the
record prospective rather than retrospective. Historical dates (through
2026-07-27) stay flagged as retrospective in the results, and the two groups are
reported separately as well as pooled.

---

### Amendment 2 — 2026-07-28, before any price data was pulled

**Status when made:** no price data had been downloaded, no outcome computed.
This resolves an internal contradiction in the original text; it is not a
response to any result.

**The conflict.** §4 names the tickers `NQ=F`, `CL=F`, `^TNX`. §6 fixes the
horizon as "same session, open to close… Entry at the 09:30 open, exit at the
16:00 close," and explicitly **rejects** prior-close-to-close because part of the
overnight move was already visible to the 08:00 forecast.

Those two clauses cannot both be honoured. A daily bar for a `=F` futures ticker
opens at the **Globex session start (18:00 ET the previous day)**, not 09:30 ET.
Scoring open-to-close on `NQ=F` would therefore measure a ~22-hour window that
*includes the overnight move the forecast already knew about* — exactly the leak
§6 was written to exclude.

**Resolution: the horizon governs; the ticker was a data-source note.** Score on
instruments whose daily open-to-close *is* the 09:30–16:00 cash session:

| instrument | pre-registered ticker | scored on | why |
|---|---|---|---|
| NQ | `NQ=F` | **`^NDX`** | Nasdaq-100 cash index; bar is exactly the RTH session |
| CL | `CL=F` | **`USO`** | RTH-traded crude proxy (only ~7 usable calls either way) |
| TNX | `^TNX` | **`^TNX`** | unchanged; already a cash-session index |

**Both are computed and reported.** The RTH-session version is **primary**, and
that designation is made here, in writing, before any price was fetched. The
`=F` version is reported alongside as a robustness check. If the two disagree,
the disagreement is itself reported.

**Bootstrap degenerate draws.** A day-clustered resample can contain zero
`said up` rows or zero `said down` rows, leaving the conditional difference
undefined. Such iterations are discarded and the discard count is reported.
