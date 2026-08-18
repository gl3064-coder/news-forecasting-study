# Pre-registration v4: single names instead of macro

**Frozen 2026-08-16, before any v4 call was extracted and before any v4 price was fetched.**

v4 is a **separate study**. v1's null stands, v2's result stands, v3 is
withdrawn unrun. Same reasoning as before: a scope change proposed after seeing
a result cannot be distinguished from motivated reasoning even when it is
sincere, so it gets its own freeze and its own sample.

---

## 1. The question

v2 gave a frozen prompt a free choice of any market and it chose oil. Measured
over the 164 retrospective pre-open weekdays with the frozen classifier:

| | |
|---|---|
| Mornings with ≥1 major issuer name in the pre-open text | **139 / 164 (85%)** |
| Of those, the bot called a broad market anyway | **129 (93%)** |
| Bot's single-name calls, all mornings | **11 (6.7%)** |
| Bot's oil calls | **113 (69%)** |

v3 was going to test that gap with a human rater and was withdrawn on cost. v4
tests it the cheap way: **change the instruction, not the reader.**

> **Does an extractor restricted to individual company stocks, and allowed to
> name several a day or none, produce a positive net return where the
> free-universe extractor produced a null?**

### Why this can estimate a return where v1 and v2 could not

Both earlier studies scored *forecast accuracy* rather than *expected return*,
because macro-frequency return estimation needs roughly 16 years of data (see
[[Frequency Tax]]) and this corpus is 0.8 years. That constraint came from the
number of independent bets, not from the calendar.

v4 changes the number of bets. One macro call a day over 164 days is 164
observations. Up to five uncorrelated single names a day is up to 820, and
measurement on the 25-name sample below shows the market factor accounts for
only **13%** of single-name daily variance, so same-day names are close to
independent rather than five copies of one bet. Return becomes estimable at
this corpus size. §7 states the floor.

---

## 2. Data state at freeze

- Corpus: 2,049 newsletters, 2025-10-23 → 2026-07-27, `newsletters`, untouched
  since the v2 freeze and asserted intact.
- Forward block: 93 newsletters over 11 mornings, 2026-07-31 → 2026-08-14,
  `newsletters_forward`, pulled 2026-08-16.
- Universe: `universe_v4.json`, **745 aliases → 557 tickers**, frozen before any
  extraction. Built by `universe_v4.py` from S&P 500 constituents plus the 149
  hand-checked aliases already in `markets_v2.MARKETS_FORWARD`. Every ticker was
  required to have ≥150 sessions of price history in 2025-10-01 → 2026-08-15;
  two failed and were dropped.
- No v4 call exists. No v4 price has been fetched.

---

## 3. The streams

Both read the same pre-open text streams A and B received, under the same
`input_rule='pre_open'` 09:30 ET cutoff that Correction 1 of v2 restored.

| | universe | count per morning |
|---|---|---|
| **E** (PRIMARY) | individual companies only | **0 to 5**, model's discretion |
| **F** | individual companies only | **exactly 1**, must call |
| **A** (existing, not re-run) | free, any market | exactly 1 |

**E is the study.** F isolates whether the freedom to pass is doing the work:
if E and F score the same, discretion over *when* to trade adds nothing and
only the single-name restriction mattered. A is the pre-existing control and is
**not re-extracted**, so its 164 calls stay exactly as v2 scored them.

The cap of 5 is arbitrary and is declared as arbitrary. It exists to stop a
shotgun morning of twenty names from swamping the day-clustered bootstrap. Rule
6 of the prompt forbids naming a company for diversification reasons.

---

## 4. What a call contains

`company` (plain words, never a ticker), `direction` (`up`/`down`),
`conviction` (`high`/`low`), `evidence` (a quoted phrase).

Calls are written to a new table `calls_v4` with one row per company per
morning, carrying `date_et`, `stream`, `prospective`, `input_rule`,
`prompt_sha256`, `model`, `extracted_at`.

---

## 5. Resolving a company to a ticker

`company` is lowercased and looked up in `universe_v4.json`. **Exact alias match
only.** No cascade, no fuzzy matching, no stemming.

Anything not in the frozen universe is logged **unscoreable** and excluded from
every number. This is expected to be the largest single cost in the study and
it is accepted deliberately. v2 refused the rule "use whatever ticker the model
names" because it hands the choice of measuring instrument to the thing being
measured; v4 keeps that refusal and pays for it by declaring the universe in
advance instead. The unscoreable rate is reported as a headline figure, not
buried.

The prompt forbids emitting tickers (§4, "Do not emit a ticker symbol") so that
the model cannot route around the universe by naming a symbol directly.

---

## 6. The prompt

`EXTRACTOR_PROMPT_V4.md`, written once, frozen, sha256 stored on every row.
Iterating it while watching the score is prohibited.

---

## 7. Outcome, the primary test, and its floor stated before the fact

**Outcome per trade.** Same-session 09:30 → 16:00 on the call's own morning,
the horizon v2 froze. Return is **residual**: the name's return minus SPY's
return over the identical window, beta assumed 1.0 and declared as an
approximation rather than estimated per name. Estimating beta from the same
window that produces the result would be fitting.

**Costs.** **10 basis points per round trip**, flat, subtracted from every
trade before the primary number. Chosen at freeze. Roughly a retail fill on a
liquid large cap: half-spread each way plus commission and slippage.
Conservative for megacaps, about right for the smaller end of a 557-ticker
universe.

**Primary:** mean net residual return per trade in stream E, day-clustered
bootstrap, 10,000 resamples, seed 42, 95% interval.

**The floor, measured before the fact.** Residual standard deviation on a
25-name large-cap sample over 2025-10-01 → 2026-07-28 is **2.78%** at one
session. Smallest detectable per-trade edge at 95%, over 164 mornings:

| trades/day | n | detectable edge |
|---|---|---|
| 1 | 164 | 0.43% |
| 3 | 492 | 0.25% |
| 5 | 820 | **0.19%** |

Against a 0.10% cost, the study can see an edge if the gross edge is roughly
0.3% per trade or better. **If stream E names fewer than about 2 companies per
morning on average, the floor rises above 0.3% and the primary is very likely
to return a null regardless of whether an edge exists.** The realised trades
per morning will be reported next to the primary so this is checkable rather
than assumed.

---

## 8. Secondary and exploratory

**Secondary**, each with its multiplicity stated:

- Stream F, same statistic. Two primaries would be two tests; F is secondary.
- **2-day and 1-week horizons.** Declared secondary and **expected to be
  nulls**: at 5 trades a day their floors are 0.38% and 0.98%, and a
  news-driven edge of 1% per trade over a week is not plausible. They are
  reported because the question was asked, not because they can answer it.
  Volatility grows with the square root of time while non-overlapping trade
  count falls linearly with it, which is the whole of the arithmetic.
  Three horizons means three tests; intervals are Bonferroni-widened.
- Hit rate and v2's corrected skill statistic, so v4 is comparable to v1 and v2
  on their own terms.
- High vs low conviction. v2's spread was +9.8pp against −12.4pp.

**Exploratory**, labelled as such wherever reported:

- Gross return, and net at 5 and 25 bps as a sensitivity band.
- Break-even cost per round trip, the figure v2 reported as 0.21%.
- Realised same-day correlation between names, as a diagnostic on the
  independence assumption in §1. This is a check on the power calculation, not
  a result.
- The 11 forward mornings alone. **This will not produce a number.**
- Sector concentration, the v4 analogue of v2's oil concentration finding.

---

## 9. Known limitations

1. **Survivorship bias in the universe.** Membership is current at freeze and
   applied across a 10-month window. Roughly 10-15 of 503 names are affected.
   It applies identically to E and F and cannot flatter one over the other.
2. **Beta is assumed 1.0.** Estimating it per name from the scoring window
   would be fitting. A high-beta name in a strong tape will look better than it
   was, and the reverse.
3. **Unscoreable rate is unknown at freeze** and could be large. The universe is
   557 tickers and the news names companies outside it constantly. §5 accepts
   this rather than loosening resolution.
4. **The 09:30 open is not a fill.** A stock reacting to overnight news gaps at
   the open; a trade placed on the open print captures none of the gap. This
   study measures 09:30 → 16:00 and therefore measures the *drift after the
   gap*, not the news reaction itself. That is a narrower and harder question
   than "did the news move the stock", and results must be reported in those
   words.
5. **Concentration risk in the cap.** A cap of 5 with no diversification rule
   means five correlated semiconductor names on a chip-news morning count as
   five bets when they are closer to one. §8's correlation diagnostic measures
   how often this happened; it does not correct for it.

---

## 10. Corrections and amendments

Any change is recorded here, dated, and classified. **Correction** restores a
rule this document already stated that the code failed to implement; both
versions are retained and re-scored. **Amendment** changes a frozen rule and
must state whether it applies to the existing sample or forward only.

### Amendment 1 — 2026-08-16, before any call was extracted and before any price was fetched

**The rater redefined the study before it ran.** No v4 call existed, no v4
price had been fetched, and no extraction batch had been submitted, so nothing
here could have been influenced by an outcome. Recorded as an amendment rather
than a silent rewrite because the frozen version is the honest baseline for
what changed and why.

**1. The universe is unrestricted again. The instruction is what changes.**

§1 and §3 as frozen restricted streams E and F to individual company stocks.
That made the universe the experimental variable. His spec instead keeps the
universe identical to stream A and changes only the **routing rule** in the
prompt:

> *"I would make it anything honestly as long as there is some sort of
> incentive for a move towards something, oil or nasdaq. It just depends on the
> article, if its major geo-political and shit then we can do nasdaq or oil but
> if it is mainly featuring one company and they did something great like
> doubled earnings, merged with another company, bought another company out or
> made any move that seems very beneficial then buy it and this goes vice versa
> for any bad news about the company."*

This is the better test and it is a **cleaner** one. Under the frozen version,
a difference between E and A confounded two changes at once: the narrower
universe and the freedom to pass. Under this version the universe is held
constant against stream A and the only manipulated variable is the instruction.
The question becomes:

> **Does telling the extractor how to route news type to instrument beat
> leaving the choice open?**

The motivating gap in §1 is unchanged and still the reason to run it: given a
free universe and no routing rule, the bot took oil on 69% of calls and a
single name on 6.7%, on mornings where a major issuer name was present in the
text 85% of the time.

**2. Stream F is dropped.** Cost, his call. E is the primary and always was;
F was the secondary that would have separated "single names help" from
"knowing when to pass helps." Without it, a good E result cannot distinguish
those two. Stated here so the limitation is on the record rather than
discovered at write-up. Halves the extraction spend.

**3. The primary statistic changes from residual return to raw return.**

Residual return (the name's return minus SPY's over the same window) is
undefined for Brent, gold, or a bond yield, and the amended universe contains
all three. The primary becomes:

> **Mean net return per trade, signed by the call's direction, minus 10 bps
> per round trip, day-clustered bootstrap, 10,000 resamples, seed 42.**

Raw return is also the more honest answer to the question actually being asked,
which is whether the calls make money after costs. Residual return survives as
a **secondary, computed on the equity subset only**, where it isolates
company-specific skill from the market's drift. Beta is still assumed 1.0 and
still declared as an approximation.

**Long-bias caveat, stated before unblinding.** Raw return rewards being long
in a rising tape for free. The day-clustered bootstrap does not correct for
this on its own. The market's own return over the identical windows is
therefore reported next to the primary, and the direction split (how many calls
were `up` versus `down`) is reported with it. If the calls are overwhelmingly
long and the window was up, the primary must be read against those two figures
and not on its own.

**4. Floors recomputed on the amended universe.** Measured over
2025-10-01 → 2026-07-28: single-name daily sd **3.00%**, macro-instrument daily
sd **2.28%**. Mixing macro in slightly *lowers* volatility and therefore
slightly *improves* the floor. At a 70/30 name-to-macro split over 175
mornings:

| calls/day | n | floor (95%) |
|---|---|---|
| 1 | 175 | 0.42% |
| 3 | 525 | **0.24%** |
| 5 | 875 | **0.19%** |

Materially unchanged from the frozen §7 figures. The §7 warning stands
verbatim: **if stream E averages under about 2 calls a morning, the floor rises
above 0.3% and the primary is very likely to return a null regardless of
whether an edge exists.** Realised calls per morning are reported beside the
primary.

**5. Resolution now spans both halves of the universe.** §5's exact-match rule
against `universe_v4.json` (745 aliases → 557 tickers) is tried first; anything
it does not resolve falls through to the frozen `markets_v2.resolve()` cascade,
which covers oil, indices, metals, rates and FX. Both tables were frozen before
any extraction. No new mapping rule is created, and the v2 refusal stands: the
model emits plain words, never a ticker, so it cannot route around the
universe. Anything neither table resolves is logged unscoreable and the rate is
reported as a headline figure.

Everything else in this document is unchanged: the 09:30 → 16:00 horizon and
its §9.4 caveat, the 10 bps cost, the model (`claude-opus-5`, matching stream
A), the secondary horizons and their expected nulls, and the §9 limitations.
