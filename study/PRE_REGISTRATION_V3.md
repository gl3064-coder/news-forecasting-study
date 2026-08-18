# Pre-registration v3: does human discretion find trades the system skips?

> ## WITHDRAWN 2026-08-16, unrun.
>
> **Zero calls were logged and no price was ever fetched.** The pool was drawn,
> the instruments were built and one morning was rendered, at which point the
> rater measured the actual cost of the protocol against himself rather than
> against an estimate: a median morning is ~10,700 words, the index-speed read
> is 4 to 6 minutes, and 120 of them is 8 to 12 hours of his attention for a
> primary whose floor was already stated at ±30pp in §7.
>
> He withdrew it on that basis. That is the correct reading of §7, not a
> failure to follow through: the document said before the fact that the primary
> would very likely return a null at this sample size, and the honest response
> to a pre-registered floor that wide is to not spend the eight hours.
>
> **The instruments survive and work.** `make_v3_pool.py`, `feed_v3.py`,
> `log_v3.py`, `name_line_table.py` and `pull_forward_v3.py` are committed and
> tested end to end. If a human arm is ever wanted, the pool is drawn at seed 42
> and the feeder runs.
>
> **What it produced that outlived it.** The premise check in §1 is the finding
> that started v4: major issuer names are present in the pre-open text on 85% of
> mornings and the bot calls a broad market on 93% of those, taking a single
> name on 11 of 164 calls (6.7%) against 113 (69%) for oil. v4 tests that gap
> directly, with an extractor instead of a person.
>
> Superseded by `PRE_REGISTRATION_V4.md`. Nothing below was scored.

**Frozen 2026-08-16, before any v3 price data was pulled and before any v3 call was made.**

v3 is a **separate study**, not an amendment to v2. v2's result stands as
reported. This one asks a different question of the same corpus, and it gets its
own freeze, its own sample, and its own primary number. The reasoning is the
same as the reasoning for why v2 was not an amendment to v1: a scope change
proposed after unblinding cannot be distinguished from motivated reasoning even
when it is sincere.

The human rater is the study's owner. Everything in this document that
constrains him also constrains the operator assembling his input, and §9 states
the operator's constraints explicitly, because they are the easiest ones to
break by accident.

---

## 1. The question

v2 established that a frozen prompt reading the pre-open news, given a free
choice of any market, picks **oil 69% of the time** and a **single name 6.7% of
the time**. It does this on mornings when major issuer names are demonstrably
present in the text.

Measured on the 164 retrospective pre-open weekdays, before this freeze, using
the frozen `name_line_table.call_type()` classifier:

| | |
|---|---|
| Mornings with ≥1 major issuer name in the pre-open text | **139 / 164 (85%)** |
| Median distinct issuer names per morning | **6** |
| Of those 139, bot called a broad market anyway | **129 (93%)** |
| Bot's single-name calls across all 164 | **11 (6.7%)** |
| Bot's oil calls | **113 (69%)** |

The question v3 asks:

> **When a person reads the same pre-open text and is free to trade a single
> name, do the single-name calls he makes carry directional skill?**

And the descriptive that motivates it: **how often does a person judge that a
tradeable single-name story is present at all?**

### The 82% is an upper bound and must be treated as one

"A name appears in the text" is not "a tradeable story about that name
appeared." This project has already been burned by exactly this inference. A
regex proxy predicted ~100% directional coverage for crude; blind extraction
under the "a mention is not a forecast" rule returned **11%**, an overestimate
of 9×. See [[NQ Edge]]'s successor note and §10.4 of `PRE_REGISTRATION_V2.md`.

So 82% is a ceiling on the opportunity, not an estimate of it. The true rate is
unknown to everyone including the rater. **Measuring it is the point of v3.**

### Why this is not v2 with a human extractor

v2 tested whether the *system* has directional information. v3 tests whether
*discretion applied to the same input* surfaces a class of trade the system
structurally does not take. A null in v2's terms would not answer this, because
v2 never made a single-name call often enough to have an opinion: 16 calls in
nine months cannot be scored.

---

## 2. Data state at freeze

- Corpus: 2,049 NYT/WSJ newsletters, 2025-10-23 → 2026-07-27, in `news_corpus.db`.
- Bot calls: `calls_v2`, 805 rows across streams A–D. Stream A pre-open = 164 calls.
- Forward calls 2026-07-28 → 2026-08-14 live on the droplet (`forecast_labels.db`),
  written before 09:30 ET each morning by the guarded job in `droplet/`.
- No v3 call exists. No price has been fetched for any v3 purpose.

---

## 3. The pool

**120 mornings, drawn once at freeze, order frozen in `handlabel_v3_manifest.json`.**

- **109** sampled without replacement, `random.Random(42)`, from the 164
  retrospective pre-open weekdays (2025-10-24 → 2026-07-27).
- **11** forward mornings, 2026-07-31 → 2026-08-14, where stream A's call was
  written live before the open and is already locked. See Amendment 1: this was
  specified as 14 mornings from 2026-07-28, and three are unrecoverable.
- Shuffled once. Dates stripped from all displayed text via the existing
  `strip_dates()`. Presented as `MORNING 001` … `MORNING 120`.

The rater does not learn which mornings are forward until all 120 are logged.

**The blinding is partial and is declared as partial.** Dates are stripped but
the news dates itself: a story about a mayor taking office on January 1 places a
morning in November 2025 for anyone paying attention. §4 records a per-morning
recognition flag so the contamination is measured rather than assumed. It is
not prevented.

---

## 4. What the rater sees

Per morning, in this order, in one message:

1. **Index.** Newsletter subject lines in document order, word counts, and the
   ALL-CAPS section markers present in each, in document order.
2. **Name line.** Every entry of `NAME_LINE_TABLE` (§4a) found in the full
   morning text by case-insensitive word-boundary match, listed alphabetically.
3. **Full text.** The complete pre-open text, byte-identical in content to what
   streams A and B received, reflowed into paragraphs.

**Nothing is removed.** The index and name line are additive navigation. Every
character the bot saw is present below them. Two display-only transforms are
applied and are the only differences from the bot's input: paragraph reflow, and
removal of unsubscribe/help/promotional footers. Both are deterministic, applied
identically to all 120 mornings, and implemented in committed code.

### 4a. The name line

The name line exists because it was measured, before this freeze, that an index
built from headlines and roundup bullets surfaces only **6% (median)** of the
tradable names present in a morning's text. `Dow Jones` appears in the text on
90 of 164 mornings and in such an index on **0**. Market names live in body
prose, not headlines. Without the name line, the interface would systematically
hide the exact class of story v3 exists to measure.

`NAME_LINE_TABLE` is derived once, at freeze, from `MARKETS` + `MARKETS_FORWARD`
in `markets_v2.py` (165 entries), dropping entries that are common English words
and would fire on ordinary prose. The drop list is committed in
`name_line_table.py` and is **never edited after freeze**. The name line applies
no ranking, no filtering, and no relevance judgment.

**This is an asymmetry and it is declared.** The bot processed all ~58,700
characters and had to notice `Brent` itself; the rater is handed the names
pre-extracted. The bot is **not** re-run with the name line, deliberately: the
bot's tendency to reach for a broad market instead of a named stock is the
baseline being measured, and patching it would destroy the comparison. Any
finding from v3 is therefore a finding about **a human equipped with a name
detector**, not about an unaided human. It must be reported in those words.

---

## 5. What a call contains

Five fields per morning, all recorded before any outcome is seen:

| field | values |
|---|---|
| `market` | plain words, the rater's own wording |
| `direction` | `up` / `down` / `pass` |
| `conviction` | `high` / `low` |
| `horizon` | `today` / `week` / `month` |
| `recognized` | `yes` / `maybe` / `no` — do I know what day this is? |

A `pass` is a real answer and is logged. Pass rate is reported.

`call_type` (`broad` / `single_name`) is **derived**, not asked, by a frozen
regex committed at freeze, so the rater cannot classify his own calls
inconsistently or with hindsight. The same regex is applied to stream A's 164
calls to produce the 10% baseline quoted in §1.

Calls are written to `calls_v2` as `stream='H'`, with `input_rule='pre_open'` and
`prospective` set to match the morning's provenance. This is deliberate: it
routes v3 through `score_v2.score_stream()` unchanged, so the rater's calls get
the identical normalisation cascade, the identical 09:30→16:00 horizon, and the
identical unscoreable rules. No new scoring code means no possibility that the
human arm was scored under different rules than the bot arm.

---

## 6. Resolving a market to a price

Frozen v2 rules apply without modification: `markets_v2.resolve()`, the
Amendment 1 normalisation cascade, `NOT_TRADEABLE` handling, and the
retrospective/forward mapping split enforced by `test_markets_v2.py`. The 106
retrospective mornings use the retrospective mapping; the 14 forward mornings
use the forward mapping. This matches what stream A received on those same days.

**No new resolution rule may be added for v3.** If the rater names something the
frozen cascade cannot resolve, it is logged unscoreable, exactly as the bot's
were. The v2 rule that was deliberately refused stays refused: the measuring
instrument is not handed to the thing being measured.

---

## 7. The primary test, and its floor stated before the fact

**Primary:** corrected directional skill on the rater's **single-name calls**,
using v2's statistic unchanged:

```
P(up | said up) − P(up | said down)
```

day-clustered bootstrap, 10,000 resamples, seed 42, 95% interval.

**The floor, computed now.** If the rater makes single-name calls on ~40 of 120
mornings and they split roughly evenly by direction, the 95% interval on this
statistic is approximately **±30pp**. That is wide. It is stated here so that a
null is read as "120 mornings cannot see an effect this size" and **not** as
"discretion has no value." v1's null was worth something for precisely this
reason: its realised CI half-width matched the floor computed before unblinding.

**Expect a null on the primary.** At this sample size the informative output is
the descriptive below, not the skill test. Saying so before unblinding is what
keeps the descriptive from being retrofitted into the headline afterwards.

**Primary descriptive, not a test:** the rater's single-name call rate, with a
binomial interval, against the bot's 6.7% (11/164). At n=120 this lands at ±9pp.
This quantity is **partly under the rater's control** — he could name a stock
every day and force it to 100% — so it is reported as a description of his
judgment, never as evidence of skill. It is paired with his pass rate, which is
the discipline check on it.

---

## 8. Secondary and exploratory

**Secondary**, declared underpowered:

- Paired comparison against stream A on all shared mornings, McNemar. To detect
  a 15pp paired difference needs ~120 discordant-eligible days; the realised
  power will be reported alongside the result.
- The rater's overall corrected skill across all calls, against the bot's
  retrospective +7.6pp, CI [−0.7, +15.9].
- High vs low conviction split. The bot's own spread was +9.8pp vs −12.4pp.

**Exploratory**, labelled as such in every report:

- Dollar return, gross and net of the 0.21% per-round-trip break-even from v2.
- `recognized=no` subset only.
- The 14 forward mornings alone. **This subset will not produce a number.**
  Fourteen days is a clean sample, not a result, and no skill figure will be
  quoted from it.
- Market overlap with stream A on the same mornings.

---

## 9. Constraints on the operator

The operator (Claude) assembles and delivers each morning. These are testable
commitments, not intentions:

1. **Verbatim delivery.** The full text is passed through unaltered except for
   the two declared display transforms in §4. No summarising, no reordering, no
   highlighting, no commentary on content.
2. **No reaction.** The operator does not remark on what is in a morning's news,
   before or after the call, until all 120 are logged.
3. **No lookahead.** The operator does not read, query, or reference stream A's
   call, or any price outcome, for any morning in the pool until all 120 calls
   are logged. The scoring script is not run early.
4. **No mid-study interface change.** The index format, the name line, and
   `NAME_LINE_TABLE` are frozen at commit. If a defect is found, it is recorded
   as a Correction under §11 with both versions retained, following the
   precedent set by the v2 look-ahead defect.

---

## 10. Known open items

1. **The name-detector asymmetry (§4a).** The largest threat to interpretation.
   Cannot be removed without destroying the baseline. Declared, not solved.
2. **Partial blinding (§3).** Mitigated by the recognition flag, not fixed.
3. **The rater is not blind to v2's results.** He knows the bot picked oil 69%
   of the time and that its single-name rate is 6.7%. This may push him toward
   single names. Since the single-name rate is explicitly a descriptive and not
   a test (§7), this biases the descriptive and not the primary. Stated so the
   descriptive is never read as a skill claim.
4. **Skimming is the declared reading method.** A morning is ~10,700 words and
   full reading is ~43 minutes; 120 mornings would be ~86 hours. The rater
   triages using the index and name line. His triage is part of the discretion
   under test. The bot read 100% of the text and the rater will not.
5. **Sample is 120 of 178 available mornings**, so results do not generalise
   beyond this corpus and this window.

---

## 11. Corrections and amendments

Any change is recorded here, dated, and classified:

- **Correction** — the code never implemented a rule this document already
  stated. Restores the frozen rule. Both versions retained and re-scored.
- **Amendment** — changes a frozen rule. Must state whether it applies to the
  existing sample or forward only. Anything that changes what has already been
  scored is forward-only.

### Amendment 1 — 2026-08-16, before any call was made and before any price was fetched

Two changes, both discovered while building the instruments named in §4 and §5.
No v3 call existed, no v3 price had been fetched, and the pool had not been
drawn, so neither could have been influenced by an outcome.

**1. The forward block is 11 mornings, not 14, and starts 2026-07-31.**

§3 as frozen specified 14 forward mornings from 2026-07-28. The raw pre-open
text for a forward morning lives only in Pulse's `pulse.db`, which holds a
**rolling 200-email window**. At the time of the pool draw that window began at
2026-07-31, so 07-28, 07-29 and 07-30 are unrecoverable. They are not lost data
from a v3 point of view: they were never captured for v3, and no substitute
exists, because re-pulling them from Gmail now would produce text assembled
after the fact rather than the text the live job actually saw.

The retrospective block absorbs the difference (106 → 109) so the pool stays at
120. The 11 surviving forward mornings all carry 8–9 pre-open newsletters and
56k–72k characters, in line with the retrospective median, so the block is not
systematically lighter than the rest of the pool.

This strengthens rather than weakens §8's existing statement that the forward
subset will not produce a number. Eleven is further below any useful floor than
fourteen was. Nothing in §7 depends on it.

**2. §1's figures are restated against the frozen classifier.**

§1 originally quoted 82% / 125-of-135 / 16 (10%) / 112 (68%), computed with an
ad-hoc regex written while testing the premise. `name_line_table.call_type()`,
which is the classifier the study will actually use, gives 85% / 129-of-139 /
**11 (6.7%)** / 113 (69%) over the same 164 mornings. The direction and size of
the gap are unchanged; the baseline the primary descriptive is measured against
moves from 10% to 6.7%.

The difference is that the frozen classifier correctly files baskets and
commodity futures as broad. `U.S. gasoline futures (RBOB)`, `cannabis stocks
(the MSOS complex)`, `Japanese defense stocks`, `Indian stocks (Nifty 50)` and
`U.S. retail stocks (the XRT retail ETF)` are not single names, and the ad-hoc
regex had counted them as such. §1 and §7 now quote the classifier's numbers so
the document and the code cannot disagree.
