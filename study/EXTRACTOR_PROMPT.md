# Extractor prompt (DRAFT — edit before freezing)

Loaded verbatim by the scoring code. Once it is frozen and committed, it must
not change. Iterating this while watching the score is fitting.

Everything below the line is the prompt.

---

You are reading one morning market commentary. Your only job is to record what
the commentary **says**. You are not forecasting anything yourself.

For each of these three instruments, decide what direction the commentary
predicts for **today's session**:

- `NQ` — Nasdaq 100 futures
- `CL` — crude oil
- `TNX` — the 10-year Treasury **yield**

For each one, output exactly one label:

- `up` — the commentary predicts this instrument rises today
- `down` — the commentary predicts this instrument falls today
- `no_call` — the commentary makes no prediction about this instrument

## Rules

**1. Record the stated view, never your own.** If the commentary's reasoning
looks wrong to you, ignore that. You are a transcriber, not an analyst.

**2. Hedging does not erase direction.** These all still count as a direction:

- "cautiously bearish" → `down`
- "cautious short-to-neutral" → `down` (there is a short lean)
- "neutral-to-cautiously-long" → `up` (there is a long lean)
- "short or flat" → `down`
- "mildly bearish" → `down`

Use `no_call` only when there is genuinely no lean, for example "neutral,
waiting for the Fed" or when both directions are given equal weight with no
tilt either way.

**3. A mention is not a forecast.** Only label an instrument if the commentary
predicts *that instrument's* move. Background references do not count.

- "oil above $100 is pressuring tech" → NQ `down`, CL `no_call`
  (it says where oil *is*, not where it is *going*)
- "we expect crude to keep climbing on Hormuz risk" → CL `up`

**4. TNX is a yield, so watch the sign.** Bond prices and yields move
opposite. Label the direction of the **yield**:

- "yields rise" / "the 10-year pushes higher" → TNX `up`
- "bonds rally" / "a flight to safety into Treasuries" → TNX `down`
- "rate hike risk is growing" → TNX `up`

**5. Conditional statements are not calls.** "If Hormuz closes, oil spikes" is
a scenario, not a prediction. That alone is `no_call` for CL. But a stated bias
plus a condition is still a call: "cautiously bearish unless TNX holds below
4.65%" → NQ `down`.

**6. Use nothing but the text in front of you.** No outside knowledge of what
markets actually did. If you think you recognise the day, ignore that
completely.

## Output

Return only JSON, in this shape, with no commentary around it:

```json
{
  "NQ":  {"label": "down", "evidence": "cautiously bearish intraday"},
  "CL":  {"label": "up",   "evidence": "crude likely grinds higher on supply risk"},
  "TNX": {"label": "no_call", "evidence": ""}
}
```

`evidence` is a short quote lifted from the text, or an empty string for
`no_call`. Do not paraphrase it. The quote is how a human spot-checks you.

Here is the commentary:

{TEXT}
