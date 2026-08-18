# Pre-registration v2: can a system read the morning news and name one trade?

**Frozen:** 2026-07-28
**Status:** committed BEFORE any v2 label was extracted and before any v2 price
data was pulled.

This is a **separate study**, not an amendment to v1. v1 asked a narrow question
about three fixed instruments and returned a clean null. That null stands and is
not revisited here. v2 asks a different and wider question, and it carries its
own sample, its own statistic, and its own decision rule.

Every line marked **[DECISION]** is a choice that could reasonably have gone
another way. They are fixed now so that they cannot be chosen later to suit an
answer. Changing any of them after unblinding requires an entry in the
Amendments section at the bottom, with a date and a reason.

---

## 1. The question

> Given a morning's news, can a system name **one trade in any market** and be
> right about its direction more often than that market's own drift would
> produce by itself?

The unit under test is **the process**, not a pattern. Each call may name a
market that never appears again. That is intended and is not a defect: what has
to stay constant across the sample is the procedure (same frozen prompt, new
news, every trading day), not the instrument.

This is a **measurement** question, not a strategy. See §9 for what is
explicitly not being claimed.

### Why this is not v1 with more tickers

v1 scored three instruments chosen in advance from a coverage scan. v2 lets the
system choose. The two differ in what a null would mean: a null in v1 says the
digest carried no directional information *about NQ, crude and treasuries*; a
null in v2 says the system carried none *about whatever it elected to talk
about*. The second is the question that was actually being asked.

---

## 2. Data state at freeze

Nothing below this point was collected after this rule was written.

| item | value |
|---|---|
| corpus DB | `news_corpus.db` |
| `newsletters` | **2,049 rows**, 2025-10-23 → 2026-07-27 |
| `newsletters` distinct dates | **260** |
| of which weekdays | **185** (180 with ≥2 messages) |
| `forecasts` (Pulse digests) | **212 rows**, 63 days, 2026-05-23 → 2026-07-27 |
| forecast source dump | `forecasts_raw.jsonl`, 5,139,726 bytes |
| dump SHA-256 | `deec06e15d96dfe087a60de19d8b9e7631c38c62445c538f60fd05bcecac04c2` |
| v1 labels | 183 rows / 61 days (untouched by v2) |

The corpus DB is not hashed here because v2 writes new tables into it. The row
counts above are the pin.

**v2 price data: not pulled.** **v2 labels: not extracted.** Nothing about any
v2 outcome is known at freeze time.

---

## 3. The four streams

**[DECISION]** Two inputs crossed with two systems, run every trading morning.

| stream | input | must it call? |
|---|---|---|
| **A — PRIMARY** | raw NYT/WSJ newsletter text | yes, every day, with conviction |
| B | raw NYT/WSJ newsletter text | no, may pass |
| C | Pulse's pre-open digest | yes, every day, with conviction |
| D | Pulse's pre-open digest | no, may pass |

**"Raw newsletter text" means the full cleaned body of every NYT and WSJ
newsletter received before 09:30 ET that morning** — the entire article text as
it arrived, not subject lines and not headlines. Typically several messages
totalling tens of thousands of characters. Cleaning is Pulse's existing helper
plus `post_clean()`, unchanged from the corpus build.

**[DECISION] Stream A is the primary. B, C and D are pre-registered
secondaries.** They are declared here, they are reported every time the result
is reported, and **none of them may be promoted to the primary finding.**

Naming the primary in advance is the point of this section. Four streams is
four chances to see something, and without this clause the best-looking stream
would become "the result."

The crossing buys three comparisons, each pre-registered as secondary:

- **A vs C** — does Pulse's summarising layer add information or destroy it?
- **A vs B** — is passing genuinely selective, or merely quiet?
- **within A, high vs low conviction** — does the system know when it knows?

---

## 4. What a call contains

Each stream emits exactly one record per trading day:

| field | values |
|---|---|
| `market` | free text, unrestricted ("gold", "10-year yield", "USD/JPY") |
| `direction` | `up` / `down` |
| `conviction` | `high` / `low` — streams A and C only |
| `horizon` | `today` / `week` / `month` — the system's own choice |
| `evidence` | the phrase from the news the call rests on |
| `pass` | streams B and D only; when true, all other fields are null |

**[DECISION] The universe is fully open.** No instrument list. The system may
name anything.

---

## 5. Resolving a market to a price

**[DECISION]** A frozen resolution table maps market names to tickers. It is
written **before any label is extracted**, and covers the names a reader would
consider unambiguous (equity indices, sectors, major single names, oil, gold,
silver, copper, the major FX pairs, treasury yields by tenor, major crypto).

**[DECISION] Resolution is exact, never approximate.** If a named market is not
in the table, the call is recorded as **unscoreable** and excluded from the
statistic. It is **not** stretched onto the nearest available proxy. "Turkish
lira" does not become an emerging-market currency ETF.

**[DECISION] The unscoreable rate is reported alongside every result**, as a
count and a percentage, whether it is flattering or not. A system that survives
by naming things nobody can price is a finding in itself.

**[DECISION] Prefer cash-session instruments.** This is the v1 Amendment 2
lesson carried forward: a daily `=F` futures bar opens at the Globex session
start (18:00 ET the previous day), so scoring it open-to-close would measure a
~22-hour window including the overnight move the morning call already knew
about. Where a cash instrument tracking the 09:30–16:00 session exists, the
table uses it.

---

## 6. Label extraction

**[DECISION]** An LLM reads the morning's text and emits the §4 record.

Binding constraints, carried from v1 §5 because they closed real leakage paths:

1. **Dates are stripped from the text before extraction.** A model that sees
   "November 4, 2025" may recall what markets did that day.
2. **Price data is never shown to the extractor.**
3. **The prompt is written once and frozen**, and its SHA-256 is stored on every
   row. Iterating a prompt while watching the score is fitting, and is
   prohibited.
4. **One prompt across all four streams**, differing only in the two switches it
   must differ in: which text it is given, and whether passing is permitted.
5. **The model must ignore any recognition of the actual day.** If it believes it
   knows which day this is, it is instructed to reason from the text regardless.

**[DECISION] Streams B and D may pass; streams A and C may not.** A forced call
on a quiet day is expected to be a weak call, and that is the cost being
measured by the A-vs-B comparison.

---

## 7. Outcome, baseline, and the primary test

**[DECISION] Primary horizon: same session, open to close, on the call date.**
Entry at the 09:30 open, exit at the 16:00 close. Every call is scored this way
regardless of the horizon it stated.

**[DECISION] The stated horizon is scored too, and reported alongside**, at
`today` = same session, `week` = 5 trading days, `month` = 21 trading days,
measured from the same 09:30 open. It is secondary. Overlapping multi-day
windows make those observations non-independent, and that is precisely why they
are not the primary.

**Outcome:** the call is a hit when `sign(exit − entry)` matches `direction`.

### The baseline is direction-aware

**[DECISION]** Each call's no-skill expectation is that market's own behaviour
over the study window:

```
E[hit | no skill]  =  up_rate(market)        if direction was up
                      1 − up_rate(market)    if direction was down
```

where `up_rate(market)` is the fraction of sessions in which that market closed
above its open, measured over **the same window the calls being scored come
from** — the retrospective window for retrospective calls, the forward window
for forward calls, computed separately for each group so that neither borrows
the other's drift. Sessions are counted whether or not the market was called on
them; the base rate describes the market, not the sample of calls.

This differs from v1 §6 and the difference is deliberate. v1 used a flat
per-instrument base rate, which Amendment 1 showed conflates skill with being
wrong-footed on tilt; v1 solved that by switching to the conditional difference
`P(up|said up) − P(up|said down)`. That statistic does not transfer to an open
universe, because its two buckets would contain different markets — up-calls on
gold compared against down-calls on bonds. Making the baseline
**direction-aware** achieves the same thing by a different route: it neutralises
both the market's drift and the system's tilt, per call, and it pools across
heterogeneous markets cleanly because a corrected hit is a scalar.

### The primary test (one number)

> **Mean over all scoreable stream-A calls of `hit − E[hit | no skill]`**, with
> a 95% confidence interval from a bootstrap resampling **days** (10,000
> iterations, seed fixed at 42).

Null is **zero** under no skill.

**Decision rule, fixed in advance:**

- CI excludes zero and is positive → evidence of directional information at this sample size
- CI includes zero → **"no detectable directional information at this sample size."** That is the finding, it gets written up as the result, and it is not a licence to try variants
- CI excludes zero and is negative → the system is anti-predictive, which is also a real and reportable finding

---

## 8. Power, stated before the fact

Standard error of the corrected hit rate is approximately `sqrt(0.25/n)`.

| sample | n (days) | 2 SE | detectable hit rate |
|---|---|---|---|
| retrospective dry run, stream A | ~170 | 7.7pp | **≥57.7%** |
| retrospective dry run, stream C | ~46 | 14.7pp | ≥64.7% |
| forward, ~3 months | ~60 | 12.9pp | ≥63% |
| forward, ~1 year | ~250 | 6.3pp | ≥56% |

Stream A's retrospective n comes from 180 weekdays with at least two messages,
less roughly 9 market holidays, less whatever proves unscoreable under §5.

**Realistic forecasting skill lives at 55–60%.** Stream A's dry run is therefore
the first sample in this project that reaches inside the range where a real
effect would live. v1's floor was 22 percentage points and never had a chance.

### The dry run is a strong hint, not the evidence

**[DECISION] Retrospective and prospective calls are stored with a `prospective`
flag and reported separately as well as pooled.**

The retrospective sample reads news from as far back as October 2025. Dates are
stripped (§6), but a model may still recognise a major event and recall what
followed. **This is unfixable, not merely reduced.** It biases the retrospective
number upward by an unknown amount.

Consequently: a positive retrospective result is **suggestive and must be
described as such**; a null retrospective result is more informative than a
positive one, because leakage would have worked in favour of a positive. The
forward record is the evidence.

**[DECISION] Forward calls are written before that session's outcome exists**,
by the same before-09:30 mechanism v1 uses. A forward call written after 09:30
ET is not recorded at all rather than recorded late.

### Re-scoring schedule

Re-run this exact rule, unchanged, on the grown forward sample at n ≈ 60 and
n ≈ 250 forward days. Re-running a frozen rule on a grown sample is not multiple
testing. Changing the rule between runs would be.

---

## 9. Explicitly exploratory

Reportable, labelled exploratory, and never promoted to the primary result:

- streams B, C and D individually and against each other
- the conviction split
- the stated-horizon scores
- which markets the system elects to name, and how that mix shifts over time
- any dollar or normalised-return view
- conditioning on news volume, market volatility, or day of week

**On any dollar view:** a multi-market P&L must be **normalised** (percent of
notional, or volatility-adjusted) before anything is summed. A point of gold is
not a dollar of NQ, and raw contract P&L cannot be added across heterogeneous
instruments. The hit-rate statistic pools as-is because a sign is a sign.

**Not claimed by any result here:** that this is tradeable. A hit rate says
nothing about magnitude, and a system can be directionally right 60% of the time
and still lose money if the wrong 40% are larger. Direction and profitability
are separate questions and only the first is being tested.

**Not claimed:** anything about v1. v1's null was computed under v1's rule on
v1's sample and is unaffected by anything here, in either direction.

---

## 10. Known open item

**Extractor validation is not yet scheduled.** v1's hand-label validation was
built and then skipped, which means v1's extractor accuracy is unverified and a
v1 null cannot be fully distinguished from a bad extractor. v2 inherits that gap
unless a hand-label pass is run. It is recorded here as a known limitation of
whatever v2 reports, not as a silent omission.

---

## 10a. Corrections

An amendment changes the frozen rule. A correction restores it: the rule was
right and the code did not implement it. Both are logged, but they are not the
same thing and should not be read as the same thing.

### Correction 1 — 2026-07-28. Look-ahead in streams A and B.

**The defect.** §3 defines the raw input as "the full cleaned body of every NYT
and WSJ newsletter received **before 09:30 ET that morning**." The function
that assembled it, `raw_days()` in `extract_v2.py`, selected every newsletter
carrying that ET date with **no time filter at all**.

**Why it matters.** Newsletter arrivals are strongly bimodal: a morning
briefing peak at 05:00–07:00 ET and a second, nearly equal peak at 16:00–17:00
carrying the closing wrap-ups. Across the weekday corpus, **43% of newsletters
(24% of the text) arrived at or after 09:30**. Streams A and B were therefore
shown the afternoon's account of what the market had already done, and then
asked for a call scored from that same morning's 09:30 open. That is not a
subtle leak; for those days it is reading the answer.

**Consequence.** Every stream A and stream B number computed before this date
is void, including the +4.1pp headline, the +28.2% percent view, the 20%
chance figure and the per-market ledger. Streams C and D are unaffected: they
read the 08:00 pre-open digest, which is pre-open by construction. Study I is
unaffected for the same reason.

**The fix.** `raw_days()` now drops any newsletter whose arrival timestamp,
converted to America/New_York, is at or after 09:30. Timezone-aware rather than
a fixed offset so the cutoff holds across DST. 164 weekdays retain at least two
pre-open newsletters, against 180 under the defective rule.

**Both versions are kept.** `calls_v2` gained an `input_rule` column
(`all_day` / `pre_open`) which is part of its primary key, so the defective
extraction is preserved beside the corrected one rather than overwritten. The
difference between them is reported, because the size of a leak is itself worth
knowing.

**No rule changed here.** §3 is quoted above as it was frozen on 2026-07-28,
before any label existed. This entry records that the implementation failed to
match it and has been brought into line.

---

## 11. Amendments

The original text above is left untouched. Amendments are appended here, each
with a date, a statement of what was known at the time, and a reason.

---

### Amendment 1 — 2026-07-28, before any v2 price data was pulled

**Status when made:** all 478 retrospective calls existed; **no price data had
been downloaded and no outcome had been computed.** This amendment was prompted
by inspecting the *predictor* only, which cannot contaminate an outcome that
does not yet exist. Same standing as v1's Amendment 1.

**What was found.** Under §5's exact-match rule, **84% of live calls were
unscoreable** — but almost none of that was the system naming exotic markets.
It was the resolution table being too literal about strings:

| the model wrote | the table holds |
|---|---|
| `crude oil (brent)` | `brent`, `crude oil` |
| `Brent crude oil` | `brent` |
| `nasdaq 100 futures` | `nasdaq 100` |
| `the 10-year treasury yield` | `10-year yield` |
| `WTI crude oil` | `wti` |

**Why that breaks §5 as written.** §5's purpose is to stop a call being
stretched onto a *different* instrument — "Turkish lira" quietly becoming an
emerging-market currency ETF. It was not written to reject a call because the
model wrote "Brent crude oil" instead of "brent". Discarding 84% of the sample
over surface syntax measures the table's vocabulary, not the system's skill.

**The change.** `resolve()` gains a documented, ordered normalisation cascade.
Every stage is a rewrite of the *name*; none substitutes a different
instrument:

1. exact match on the normalised string (unchanged)
2. a parenthetical qualifier, preferred over the outer term because it is more
   specific — `crude oil (brent)` resolves to Brent, not WTI
3. the string with parentheticals removed
4. the string with non-identifying modifiers removed: `futures`, `shares`,
   `stock`, `index`, `price`, `contract`, `front-month`, `treasury`, `us`

Plus three aliases whose mapping is factually determined rather than
discretionary: `brent crude oil` → BNO, `wti crude oil` → USO,
`long-dated bonds` → TLT.

**Why this cannot be tuned toward a result.** No price existed when it was
written, and each mapping has exactly one correct answer. Brent is BNO. There
is no version of this change that makes a hit more likely than a miss.

**What is NOT changed.** The exact-never-approximate principle stands. A named
market still either resolves or is recorded unscoreable; nothing is stretched
to a neighbour. Single names outside the frozen list (Micron, Warner Bros.
Discovery), foreign instruments with no US-listed equivalent (Argentine
sovereign bonds, Dutch TTF gas), and the 2-year yield (no Yahoo series) all
remain unscoreable and are reported as such.

**Effect, stated before unblinding:** coverage rises from 16% to roughly 80%.
**Both figures are reported with the result**, and the pre-amendment
exact-match scoring is computed and reported alongside the amended scoring as
a robustness check. If the two disagree, the disagreement is itself reported.

---

### Amendment 2 — 2026-07-28, FORWARD SAMPLE ONLY

**Status when made:** the retrospective result was already computed and
visible. **This amendment therefore does not touch it.** It applies only to
calls flagged `prospective = 1`, none of which existed when it was written.

**What was found.** Of 137 scored retrospective calls, 119 (87%) resolved to
instruments anyone could actually have bought at the open and sold at the
close. The other 18 resolved to `^NDX`, `^GSPC`, `^TNX` and `^TYX` — an index
is not a security and a yield is not a price. Those 18 calls carried +11.9% of
the +28.2% total: **13% of the calls produced 42% of the return, and they are
the ones that were never tradeable.**

**The change, forward only.** `^NDX → QQQ` and `^GSPC → SPY` in a separate
`MARKETS_FORWARD` table. Measured over 206 sessions, QQQ agrees with `^NDX` on
the sign of the cash-session move 98% of the time (r = 0.99) and SPY with
`^GSPC` 96% (r = 0.98), so this substitutes a tradeable instrument for an
untradeable one without changing which market is being called.

**What is deliberately NOT changed.** The yields stay on `^TNX` and `^TYX`. The
available proxies move inversely to the yield, and over the same window the
sign-preserving ones agreed with the yield's direction only 79% (TBX vs `^TNX`,
r = 0.63) and 80% (TBF vs `^TYX`, r = 0.81) of the time. Swapping would trade a
clean measurement for a proxy that disagrees one day in five — worse
measurement in exchange for tradeability §9 does not claim. Yield calls are
measured on the yield and reported as not directly tradeable.

**Why forward only.** The retrospective instrument mix and its returns are both
already known. Re-mapping instruments after seeing which ones produced the
return is indistinguishable from choosing the mapping that flatters the answer,
however defensible each individual swap is. `MARKETS` is untouched, the
retrospective scoring is byte-identical to what it was, and a test asserts it.

---

### Amendment 3 — 2026-07-28, FORWARD SAMPLE ONLY

**Status when made:** the corrected retrospective result was visible; **no
forward call existed.** Applies only to `prospective = 1` rows.

**What was found.** 15% of corrected retrospective calls named a market the
frozen table did not contain, and about half of those were ordinary US-listed
securities — Micron, Uber, DoorDash, Moderna, Warner Bros. Discovery. Twice the
model named the ticker itself ("the **MSOS** / U.S. marijuana equity complex",
"U.S. retail stocks (the **XRT** retail ETF)"). Nothing about those is hard to
price. They were absent from a list written before anyone knew what the model
would say, which is the cost of freezing a universe in advance.

**The change.** `FORWARD_ADDITIONS`: 43 large- and mid-cap US single names, 12
thematic ETFs, 12 international exposures via US-listed ETFs, and gasoline via
UGA. **Every ticker was verified to have more than 150 sessions of history
before being added; none was rejected.** The Amendment 1 cascade also gained
two stages — corporate suffixes (`Corporation`, `Inc`, `Holdings`) and
abbreviating periods ("Warner Bros. Discovery") — because those are name
rewrites, not instrument substitutions.

**Effect:** forward unscoreable rate falls from 15% to **8%**.

**What is deliberately NOT added.** UK gilt yields, Dutch TTF gas, the 2-year
Treasury yield, Argentine sovereign bonds, and Copenhagen- and Tokyo-listed
single names. None has a clean US-listed cash-session equivalent, and a
stretched proxy is worse than an honest gap. Those calls stay unscoreable and
the rate is reported.

**One rule deliberately rejected.** The model sometimes names a ticker
correctly, and "use whatever ticker it names" would close most of the remaining
gap. It is refused because it hands the choice of measuring instrument to the
thing being measured — a far larger degree of freedom than the 8% it would
recover.

**Retrospective is untouched and proven so.** The cascade extension could in
principle have made a previously-unscoreable retrospective call resolve against
the frozen table. It does not: the frozen-table unscoreable count is 24 of 164
pre-open calls and 36 of 179 all-day calls, identical before and after, and a
test asserts the frozen mapping still resolves as frozen.

---

### Amendment 4 — 2026-07-28, FORWARD SCOPE ONLY

**Forward collection is narrowed to stream A. B, C and D stop accruing forward
days as of this date. All four retrospective results stand exactly as reported.**

**Grounds: cost, not results.** Four Claude Opus 5 calls per weekday over the
full pre-open text measure out at roughly $8/month. The forward record needs
between two and five years to distinguish a small win from a small loss, so the
committed spend is $200-500 to resolve an effect whose point estimate is worth
about $50/year on a $10,000 position. Stream A alone runs about $2/month. This
amendment is made with the retrospective results already visible, which is
disclosed here rather than buried; the deciding number is the API bill, and the
bill does not depend on which way the result came out.

**Which stream survives, and why that one.** A is the stream §7 already named
PRIMARY. Keeping the primary and dropping the secondaries is the one narrowing
that leaves the pre-registered test unchanged. Choosing instead to keep, say, C
because its retrospective number looked better would be selecting a stream on
results, which is exactly what this document exists to prevent.

**What it forfeits, stated plainly.** The raw-versus-digest contrast (+7.6pp raw
against -1.5pp digest) is the most interesting thing the retrospective found,
and dropping C and D means it can never be forward-validated. It stays a
retrospective finding, permanently. That is a real loss and it is the price of
the cut.

**Second-order effect worth recording.** With C and D closed, nothing in the
forward record reads Pulse's digest any more, so Pulse's own summariser model is
no longer an input to this study and can be changed on cost or quality grounds
without touching the measurement. While C and D were live, swapping it would
have silently altered the instrument mid-study.

**Reversal is one line.** `ACTIVE_STREAMS` in `daily_call_v2.py` — the full
STREAMS table, the schema and the scoring path are all left intact. Any restart
must be recorded here as its own amendment, with the restart date, so that a
stream cannot be quietly switched back on after a look at the numbers.

**Study I (v1) forward collection is closed on the same grounds and the same
date.** Its result is a reported null, its design is the weaker one (three fixed
instruments, digest input — the input the retrospective found worse), and stream
A tests the same question better. The scorecard says so on the page rather than
letting a frozen counter read as a live one.
