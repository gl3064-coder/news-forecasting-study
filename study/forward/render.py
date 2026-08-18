r"""
Page rendering for the forecast scorecard. Imported by build_scorecard.py.

AESTHETIC BRIEF
    This page reports a null result. A fintech dashboard idiom — glossy cards,
    green and red, confident deltas — would lie about what it contains. So the
    reference is a laboratory calibration certificate: serif prose, monospace
    data, hairline rules, ruler ticks on the instrument scales, numbered
    sections, and an INCONCLUSIVE stamp. Something that would be embarrassed to
    overstate itself.

    Two skills were in tension here. The dataviz skill's reference design system
    specifies system-sans everywhere; the frontend-design skill forbids generic
    system fonts. Resolution: dataviz calls its type and surfaces *swappable
    parameters* and its color/mark rules the substantive part. So the validated
    palette, the band geometry, the no-red-no-green rule, the single-series
    no-legend rule and the table-view fallback all survive unchanged. Type,
    layout, texture and motion are the parts that changed.

    No external requests: fonts are local stacks, the grain is an inline SVG
    data URI, all CSS is inline. The page must stay small enough to regenerate
    twice a day without thought.

THE ONE MEMORABLE THING
    The confidence band animates outward from the zero tick on load, expanding
    to its full width. You watch the uncertainty spread past the estimate.
"""

from __future__ import annotations

import html

# Local stacks only. Charter / Iowan / Sitka are genuine text serifs with
# personality; Georgia is the floor. Nothing generic-sans anywhere.
SERIF = ('Charter,"Bitstream Charter","Iowan Old Style","Sitka Text",'
         'Cambria,Georgia,serif')
MONO = ('"SF Mono","Cascadia Mono","JetBrains Mono","IBM Plex Mono",'
        'Menlo,Consolas,"DejaVu Sans Mono",monospace')

# Paper grain. feTurbulence, inline, ~300 bytes. Sits at 3% over the plane.
GRAIN = (
    "data:image/svg+xml;charset=utf-8,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E"
    "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9'"
    " numOctaves='3'/%3E%3C/filter%3E"
    "%3Crect width='120' height='120' filter='url(%23n)' opacity='.5'/%3E%3C/svg%3E"
)

CSS = f"""
:root {{
  color-scheme: light;
  --plane:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink2:#52514e;
  /* --muted carries only TEXT here (small caps labels, ruler numerals), so it
     has to clear WCAG AA at 10-11px. The reference palette's #898781 measures
     3.41:1 on this plane and fails; #6f6d67 measures 4.91:1 and holds the same
     warm family. Dark mode keeps #898781, which already measures 5.41:1. */
  --muted:#6f6d67;
  --rule:#e1e0d9; --base:#c3c2b7; --hair:rgba(11,11,11,.10);
  --series:#2a78d6; --track:#cde2fb; --grain:.032;
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) {{
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --rule:#2c2c2a; --base:#383835; --hair:rgba(255,255,255,.10);
    --series:#3987e5; --track:#184f95; --grain:.055;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --plane:#0d0d0d; --surface:#1a1a19;
  --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --rule:#2c2c2a; --base:#383835; --hair:rgba(255,255,255,.10);
  --series:#3987e5; --track:#184f95; --grain:.055;
}}

*,*::before,*::after {{ box-sizing:border-box; }}
html {{ -webkit-text-size-adjust:100%; }}
body {{
  margin:0; background:var(--plane); color:var(--ink);
  font:400 17px/1.62 {SERIF};
  padding:0 20px 72px; overflow-x:hidden;
  -webkit-font-smoothing:antialiased;
}}
body::before {{           /* paper grain */
  content:""; position:fixed; inset:0; pointer-events:none; z-index:99;
  background-image:url("{GRAIN}"); opacity:var(--grain);
  mix-blend-mode:multiply;
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) body::before {{ mix-blend-mode:screen; }}
}}
:root[data-theme="dark"] body::before {{ mix-blend-mode:screen; }}

main {{ max-width:660px; margin:0 auto; }}

/* ---- staggered entrance: one orchestrated moment, then stillness ---- */
.rise {{ animation:rise .62s cubic-bezier(.16,.84,.3,1) both; }}
@keyframes rise {{ from {{ opacity:0; transform:translateY(10px); }}
                   to   {{ opacity:1; transform:none; }} }}
@media (prefers-reduced-motion: reduce) {{
  .rise {{ animation:none; }}
  .bandfill {{ animation:none !important; transform:none !important; }}
}}

/* ---- masthead ---- */
header {{ padding:52px 0 0; position:relative; }}
.eyebrow {{
  font:500 11px/1 {MONO}; letter-spacing:.20em; text-transform:uppercase;
  color:var(--muted); margin:0 0 20px;
}}
h1 {{
  font:400 clamp(30px,8.4vw,44px)/1.06 {SERIF};
  letter-spacing:-.021em; margin:0 0 4px;
}}
h1 em {{ font-style:italic; color:var(--ink2); }}
.dek {{ color:var(--ink2); font-size:15.5px; margin:0; max-width:46ch; }}

.stamp {{
  display:inline-block; margin:26px 0 0; transform:rotate(-2.2deg);
  padding:7px 15px 6px; color:var(--muted);
  border:1px solid var(--base); box-shadow:0 0 0 3px var(--plane),0 0 0 4px var(--base);
  font:500 11px/1 {MONO}; letter-spacing:.22em; text-transform:uppercase;
}}

/* ---- numbered sections separated by rules, not cards ---- */
section {{ margin:0; padding:38px 0 0; }}
.srule {{ border:0; border-top:1px solid var(--rule); margin:38px 0 0; }}
.shead {{ display:flex; gap:14px; align-items:baseline; margin:0 0 22px; }}
.snum {{
  font:500 11px/1.4 {MONO}; letter-spacing:.14em; color:var(--muted);
  flex:0 0 auto; padding-top:3px;
}}
.stitle {{ font:400 20px/1.25 {SERIF}; letter-spacing:-.01em; margin:0; }}
.snote {{ color:var(--ink2); font-size:14.5px; margin:5px 0 0; max-width:52ch; }}

/* ---- the measurement ---- */
.value {{
  font:400 clamp(38px,11.6vw,60px)/1 {MONO};
  letter-spacing:-.035em; font-variant-numeric:tabular-nums;
  margin:0; display:block;
}}
.value.small {{ font-size:clamp(30px,8.4vw,40px); }}
.interval {{
  font:400 13px/1.5 {MONO}; font-variant-numeric:tabular-nums;
  color:var(--ink2); margin:12px 0 0; letter-spacing:-.005em;
}}
.interval b {{ font-weight:500; color:var(--muted);
  letter-spacing:.13em; text-transform:uppercase; font-size:10.5px;
  display:block; margin-bottom:3px; }}

/* ---- instrument scale ---- */
.scale {{ margin:26px 0 0; }}
.band {{
  position:relative; height:56px;
  background:
    repeating-linear-gradient(90deg,var(--rule) 0 1px,transparent 1px 100%) center bottom/
      calc(100%/20) 7px no-repeat,
    linear-gradient(var(--base),var(--base)) left bottom/100% 1px no-repeat;
}}
.bandfill {{
  position:absolute; top:8px; height:32px; border-radius:3px;
  background:var(--series); opacity:.17;
  animation:spread .84s .22s cubic-bezier(.16,.84,.3,1) both;
}}
@keyframes spread {{ from {{ transform:scaleX(.001); }} to {{ transform:scaleX(1); }} }}
.bandzero {{ position:absolute; top:0; height:47px; width:1px; background:var(--base); }}
.bandzero::after {{
  content:"0"; position:absolute; top:49px; left:50%; transform:translateX(-50%);
  font:500 10px/1 {MONO}; color:var(--muted);
}}
.bandpoint {{
  position:absolute; top:2px; height:44px; width:2px; border-radius:1px;
  background:var(--series);
}}
.bandends {{
  display:flex; justify-content:space-between; margin:20px 0 0;
  font:400 11.5px/1 {MONO}; font-variant-numeric:tabular-nums; color:var(--muted);
}}

/* ---- stat grid: hairlines, no boxes ---- */
.stats {{
  display:grid; grid-template-columns:repeat(2,1fr);
  border-top:1px solid var(--rule); margin:0;
}}
@media (min-width:560px) {{ .stats {{ grid-template-columns:repeat(4,1fr); }} }}
.stat {{ padding:16px 14px 18px 0; border-bottom:1px solid var(--rule); }}
.stat + .stat {{ padding-left:14px; border-left:1px solid var(--rule); }}
@media (min-width:560px) {{ .stat {{ border-bottom:0; }} }}
.stat dt {{
  font:500 10px/1.3 {MONO}; letter-spacing:.13em; text-transform:uppercase;
  color:var(--muted); margin:0 0 7px;
}}
.stat dd {{
  font:400 25px/1 {MONO}; font-variant-numeric:tabular-nums;
  letter-spacing:-.02em; margin:0;
}}

/* ---- meter ---- */
.meter {{ height:7px; background:var(--track); margin:20px 0 11px; position:relative; }}
.meter i {{ display:block; height:100%; background:var(--series);
  animation:fill 1s .3s cubic-bezier(.16,.84,.3,1) both; transform-origin:left; }}
@keyframes fill {{ from {{ transform:scaleX(0); }} to {{ transform:scaleX(1); }} }}
.mlabels {{ display:flex; justify-content:space-between;
  font:400 11.5px/1 {MONO}; color:var(--muted); font-variant-numeric:tabular-nums; }}

/* ---- log ---- */
details {{ margin:0; }}
summary {{
  cursor:pointer; font:500 11px/1 {MONO}; letter-spacing:.14em;
  text-transform:uppercase; color:var(--ink2); padding:4px 0;
  list-style:none;
}}
summary::-webkit-details-marker {{ display:none; }}
summary::before {{ content:"+ "; color:var(--muted); }}
details[open] summary::before {{ content:"\\2212 "; }}
.scroll {{ overflow-x:auto; margin-top:18px; }}
table {{ width:100%; border-collapse:collapse;
  font:400 13.5px/1 {MONO}; font-variant-numeric:tabular-nums; }}
th,td {{ text-align:right; padding:9px 6px; border-bottom:1px solid var(--rule);
  white-space:nowrap; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ font-weight:500; font-size:10px; letter-spacing:.13em; text-transform:uppercase;
  color:var(--muted); }}

/* ---- aside: a second reading inside the same section ---- */
.aside {{ margin:34px 0 0; padding:22px 0 0; border-top:1px solid var(--hair); }}
.asidehead {{ font:500 10px/1 {MONO}; letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin:0 0 14px; }}

/* ---- a fault the reader must not miss: weight and a rule, not colour ---- */
.warn {{ margin:20px 0 0; padding:14px 16px; border:1px solid var(--base);
  border-left:3px solid var(--ink); font-size:14.5px; color:var(--ink); }}
.warn b {{ font-weight:600; }}

/* ---- ledger: outcome by weight, never by colour ---- */
td.tick {{ color:var(--muted); font-size:12px; }}
td.hit {{ color:var(--ink); }}
td.miss {{ color:var(--muted); }}
.scroll.tall {{ max-height:390px; overflow-y:auto; }}
.scroll.tall thead th {{ position:sticky; top:0; background:var(--plane);
  box-shadow:0 1px 0 var(--rule); }}

/* ---- colophon ---- */
footer {{ margin:44px 0 0; padding-top:26px; border-top:1px solid var(--rule);
  color:var(--muted); font-size:13.5px; line-height:1.66; }}
footer p {{ margin:0 0 12px; max-width:56ch; }}
footer strong {{ color:var(--ink2); font-weight:400; font-style:italic; }}
.prov {{ display:grid; gap:2px 18px; margin:0;
  font:400 11.5px/1.6 {MONO}; grid-template-columns:auto 1fr; }}
.prov dt {{ color:var(--muted); letter-spacing:.08em; text-transform:uppercase;
  font-size:10px; padding-top:3px; }}
.prov dd {{ margin:0; color:var(--ink2); word-break:break-all; }}

/* ---- register tabs: two studies filed in one document -------------------- */
/* CSS-only. Radios carry the state so the page needs no script; native radio
   semantics also give arrow-key navigation and a focus ring for free. */
.tabin {{ position:absolute; width:1px; height:1px; opacity:0;
  clip-path:inset(50%); pointer-events:none; }}
.tabs {{ display:flex; margin:34px 0 0;
  border-bottom:1px solid var(--rule); }}
.tabs label {{
  font:500 10.5px/1 {MONO}; letter-spacing:.15em; text-transform:uppercase;
  color:var(--muted); padding:13px 0 11px; margin-right:26px; cursor:pointer;
  border-bottom:2px solid transparent; margin-bottom:-1px;
  transition:color .18s ease; white-space:nowrap;
}}
.tabs label:hover {{ color:var(--ink2); }}
/* The numeral is quieter than the word, but via a real colour rather than
   opacity: at 10.5px an opacity blend drops below the AA floor, and --muted
   clears it in both planes (4.91 light / 5.41 dark). */
.tabs label .tnum {{ font-style:normal; color:var(--muted); margin-right:7px; }}
#t1:checked ~ .tabs label[for="t1"],
#t2:checked ~ .tabs label[for="t2"] {{
  color:var(--ink); border-bottom-color:var(--series); }}
#t1:focus-visible ~ .tabs label[for="t1"],
#t2:focus-visible ~ .tabs label[for="t2"] {{
  outline:2px solid var(--series); outline-offset:3px; }}
.panel {{ display:none; }}
#t1:checked ~ .p1, #t2:checked ~ .p2 {{ display:block; }}
/* shared trailer, outside both panels */
.colophon {{ margin:44px 0 0; padding-top:18px;
  border-top:1px solid var(--rule);
  font:400 10.5px/1.6 {MONO}; letter-spacing:.06em; color:var(--muted); }}
@media (max-width:400px) {{
  .tabs label {{ margin-right:18px; letter-spacing:.11em; }}
}}

/* ---- comparison block: four bands sharing ONE domain --------------------- */
.cmpgroup + .cmpgroup {{ margin-top:22px; padding-top:18px;
  border-top:1px solid var(--hair); }}
.cmphead {{ font-family:{MONO}; font-size:10px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--muted); margin:0 0 12px; }}
.cmprow {{ display:grid; grid-template-columns:1fr; gap:4px 14px;
  align-items:center; }}
.cmprow + .cmprow {{ margin-top:14px; }}
.cmplab {{ font-size:14px; color:var(--ink2); }}
.cmplab b {{ color:var(--ink); font-weight:600; }}
.cmpn {{ font-family:{MONO}; font-size:11px; color:var(--muted);
  margin-left:8px; }}
.cmpval {{ font-family:{MONO}; font-size:15px; color:var(--ink);
  font-variant-numeric:tabular-nums; }}
@media (min-width:620px) {{
  .cmprow {{ grid-template-columns:190px 1fr 74px; }}
  .cmpval {{ text-align:right; }}
}}
/* the shared-domain axis, drawn once beneath the group */
.cmpaxis {{ display:flex; justify-content:space-between;
  font-family:{MONO}; font-size:10px; color:var(--muted); margin-top:10px; }}
@media (min-width:620px) {{
  .cmpaxis {{ margin-left:204px; margin-right:88px; }}
}}
"""


def domain(items: list[tuple[float, float, float]]) -> tuple[float, float]:
    """One domain spanning every (point, lo, hi) passed in, so bands drawn with
    it are directly comparable. Bands on independent domains would look
    comparable and not be — the whole point of the comparison block."""
    span_lo = min([0.0] + [v for it in items for v in it])
    span_hi = max([0.0] + [v for it in items for v in it])
    pad = (span_hi - span_lo) * 0.09 or 1.0
    return span_lo - pad, span_hi + pad


def scale(point: float, lo: float, hi: float, fmt, label: str,
          dom: tuple[float, float] | None = None, ends: bool = True) -> str:
    """One instrument scale. The band is the loud element; the estimate is a
    2px rule inside it. It expands outward from the zero tick on load, so the
    uncertainty is seen spreading past the estimate.

    Pass `dom` to draw several bands against a shared domain; omit it and the
    band scales itself."""
    if dom is not None:
        d0, d1 = dom
    else:
        span_lo, span_hi = min(lo, 0.0, point), max(hi, 0.0, point)
        pad = (span_hi - span_lo) * 0.09 or 1.0
        d0, d1 = span_lo - pad, span_hi + pad

    def p(v: float) -> float:
        return (v - d0) / (d1 - d0) * 100

    left, width = p(lo), p(hi) - p(lo)
    # scaleX origin as a fraction *within the fill*, so growth starts at zero
    origin = 0.0 if width <= 0 else min(100.0, max(0.0, (p(0.0) - left) / width * 100))
    return f"""<div class="scale">
      <div class="band" role="img" aria-label="{html.escape(label)}">
        <div class="bandfill" style="left:{left:.2f}%;width:{width:.2f}%;
             transform-origin:{origin:.1f}% 50%"></div>
        <div class="bandzero" style="left:{p(0.0):.2f}%"></div>
        <div class="bandpoint" style="left:{p(point):.2f}%"></div>
      </div>
      {f'<div class="bandends"><span>{fmt(lo)}</span><span>{fmt(hi)}</span></div>'
       if ends else ''}
    </div>"""


def comparison(v2: dict, pp) -> str:
    """Four streams, one shared domain. The finding is meant to be seen rather
    than read: the raw-news bands sit to the right of the digest bands."""
    order = [("A", "B", "Read the newspapers"), ("C", "D", "Read Pulse's digest")]
    present = [v2["streams"][s] for grp in order for s in grp[:2]
               if s in v2["streams"]]
    dom = domain([(r["skill"], r["lo"], r["hi"]) for r in present])

    out = []
    for a, b, head in order:
        rows = [v2["streams"][s] for s in (a, b) if s in v2["streams"]]
        if not rows:
            continue
        body = []
        for r in rows:
            body.append(f"""<div class="cmprow">
        <div class="cmplab"><b>{html.escape(r['label'].split(',')[0])}</b>,
          {html.escape(r['label'].split(',')[1].strip())}
          <span class="cmpn">n={r['n']}</span></div>
        {scale(r['skill'], r['lo'], r['hi'], pp,
               f"{r['label']}: {pp(r['skill'])}, "
               f"interval {pp(r['lo'])} to {pp(r['hi'])}",
               dom=dom, ends=False)}
        <div class="cmpval">{pp(r['skill'])}</div>
      </div>""")
        out.append(f"""<div class="cmpgroup">
      <p class="cmphead">{head}</p>
      {''.join(body)}
    </div>""")

    d0, d1 = dom
    out.append(f'<div class="cmpaxis"><span>{pp(d0)}</span>'
               f'<span>0</span><span>{pp(d1)}</span></div>')
    return "".join(out)


def in_percent(a: dict) -> str:
    """Section 06. Two different questions that both answer in percent, kept
    together because the second is what stops the first being misread."""
    p = a["pct"]
    if not p:
        return ""
    pc = lambda v: f"{v*100:+.1f}%"          # noqa: E731
    chance = a["chance"]
    one_in = round(1 / chance) if chance else None

    return f"""
<hr class="srule">
<section class="rise" style="animation-delay:.08s">
  <div class="shead"><span class="snum">06</span>
    <div><h2 class="stitle">The same result in percent</h2>
      <p class="snote">Study&nbsp;I could report dollars because it traded one
        contract. Fifteen markets cannot be added that way &mdash; a point of
        gold is not a dollar of Nasdaq &mdash; so each call is measured as a
        percent of the position it took.</p>
    </div></div>

  <span class="value small">{pc(p['total'])}</span>
  <p class="interval"><b>95% interval</b>{pc(p['lo'])} &nbsp;to&nbsp; {pc(p['hi'])}</p>
  {scale(p['total'], p['lo'], p['hi'], pc,
         f"Total {pc(p['total'])}, 95 percent interval "
         f"{pc(p['lo'])} to {pc(p['hi'])}")}

  <dl class="stats">
    <div class="stat"><dt>Per trade</dt><dd>{p['mean']*100:+.2f}%</dd></div>
    <div class="stat"><dt>Median</dt><dd>{p['median']*100:+.2f}%</dd></div>
    <div class="stat"><dt>&sigma; / trade</dt><dd>{p['std']*100:.2f}%</dd></div>
    <div class="stat"><dt>Win rate</dt><dd>{p['win_rate']*100:.0f}%</dd></div>
  </dl>
  <p class="snote" style="margin-top:22px">Best day {pc(p['best'])}, worst
    {pc(p['worst'])}, across {p['n']} trades held from the open to the close.
    Equal weight each, no stop, and <b>gross &mdash; no costs or slippage are
    deducted</b>.</p>
  <p class="snote">Spread across {p['n']} trades, the whole figure is worth
    {p['mean']*100:.2f}% per round trip. That is the break-even: a cost above
    {p['mean']*100:.2f}% per trade erases it entirely. Liquid ETFs cost well
    under that, but several of these markets are not directly tradeable at all
    &mdash; the Nasdaq&nbsp;100 is an index and the 10-year is a yield &mdash;
    so treat the figure as the shape of the calls, not a return anyone could
    have collected. The interval contains zero either way.</p>

  <div class="aside">
    <p class="asidehead">How often luck does this well</p>
    <span class="value small">{chance*100:.0f}%</span>
    <p class="snote" style="margin-top:12px">Holding every market, date and
      outcome fixed and reshuffling only the up-and-down decisions,
      {chance*100:.0f}% of those random histories scored at least as well as the
      real one &mdash; about one in {one_in}. That is the plainest reading of
      why the interval contains zero: a coin flip lands here often enough that
      this sample cannot rule it out.</p>
    <p class="snote">It is also the number that would have to fall a long way
      before any of this counted as evidence. Exploratory, and not one of the
      pre-registered tests.</p>
  </div>
</section>"""


def forward_block(v2: dict) -> str:
    """Section 09. The forward record, and whether the job that fills it is
    still alive.

    Two rules here. Below min_scored no skill figure is shown at all — a
    number on nine calls is not an estimate, it is an invitation to misread
    one. And if the job stops, the page says so rather than continuing to
    display a stale record as though it were current; silent failure on an
    unattended cron is the realistic way this study dies."""
    f = v2.get("forward")
    if not f:
        return ""
    st = f["status"]
    days, scored = f.get("days", 0), f.get("scored", 0)
    minimum = f.get("min_scored", 20)

    if st.get("healthy") is False:
        note = (f'<p class="warn"><b>Not recording.</b> The last forward call '
                f'was {html.escape(st["last_date"] or "never")}, '
                f'{st["stale_weekdays"]} weekdays ago. The morning job has '
                f'stopped and the record is frozen until it is fixed.</p>')
    elif not days:
        note = ('<p class="snote">Recording begins with the next weekday '
                'morning. Nothing has been written yet.</p>')
    else:
        note = (f'<p class="snote">Last written '
                f'{html.escape(st["last_date"])}. The job runs every weekday '
                f'before the open and writes one call per stream.</p>')

    rows = ""
    for s in sorted(f.get("streams", {})):
        b = f["streams"][s]
        if "skill" in b:
            verdict = (f"{b['skill']*100:+.1f}pp "
                       f"[{b['lo']*100:+.1f}, {b['hi']*100:+.1f}]")
        else:
            verdict = f"{b['scored']} of {minimum} needed"
        rows += (f"<tr><td>{s}</td><td>{b['calls']}</td><td>{b['scored']}</td>"
                 f"<td>{b['unscoreable']}</td><td>{verdict}</td></tr>")

    pct = min(100.0, scored / minimum * 100) if minimum else 0

    return f"""
<hr class="srule">
<section class="rise" style="animation-delay:.16s">
  <div class="shead"><span class="snum">09</span>
    <div><h2 class="stitle">Forward record</h2>
      <p class="snote">Everything above is retrospective: the calls were made
        against news from months ago. These were written before the session
        they name, which is the only version of this evidence nobody can argue
        with.</p>
    </div></div>
  {note}
  <div class="meter" style="margin-top:22px"><i style="width:{pct:.1f}%"></i></div>
  <div class="mlabels"><span>{scored} scored of {minimum} needed</span>
    <span>{days} days</span></div>
  {f'''<div class="scroll" style="margin-top:22px"><table>
    <thead><tr><th>Stream</th><th>Calls</th><th>Scored</th>
      <th>Unscoreable</th><th>Skill</th></tr></thead>
    <tbody>{rows}</tbody></table></div>''' if rows else ''}
  <p class="snote" style="margin-top:16px">No skill figure is shown until a
    stream reaches {minimum} scored calls. Below that the interval is wider
    than any effect it could contain, and printing one would invite exactly
    the misreading this page exists to avoid.</p>
</section>"""


def ledger(a: dict) -> str:
    """Section 07. Study I could only ever log one instrument, so its log is a
    list of dates. Here the instrument is the variable, so the ledger leads
    with the per-market breakdown and keeps the chronology underneath it.

    Nothing is coloured by outcome. A hit reads in ink and a miss in muted,
    which separates them without claiming a 55% hit rate is green."""
    mk = "".join(
        f"""<tr><td>{html.escape(m['name'])}</td>
        <td class="tick">{html.escape(m['ticker'])}</td>
        <td>{m['n']}</td>
        <td>{m['hit_rate']*100:.0f}%</td>
        <td>{m['ret']*100:+.1f}%</td>
        <td class="tick">{m['ret_per']*100:+.2f}%</td></tr>"""
        for m in a["by_market"])

    log = "".join(
        f"""<tr><td class="tick">{html.escape(c['date'])}</td>
        <td>{html.escape(c['name'])}</td>
        <td>{c['call']}</td>
        <td class="{'hit' if c['hit'] else 'miss'}">
          {'hit' if c['hit'] else 'miss'}</td>
        <td>{'' if c['ret'] is None else f"{c['ret']*100:+.2f}%"}</td></tr>"""
        for c in reversed(a["calls"]))

    n_mk = len(a["by_market"])
    biggest = a["by_market"][0]                       # most-called
    # Best earner among markets with more than one call: a single lucky call
    # is not a contrast worth drawing in prose.
    multi = [m for m in a["by_market"] if m["n"] > 1]
    best_ret = max(multi or a["by_market"], key=lambda m: m["ret"])
    return f"""
<hr class="srule">
<section class="rise" style="animation-delay:.12s">
  <div class="shead"><span class="snum">08</span>
    <div><h2 class="stitle">Ledger</h2>
      <p class="snote">Free to pick any market each morning, it settled on
        {n_mk} of them &mdash; and {biggest['n']} of {a['n']} calls went to
        {html.escape(biggest['name'])} alone. That concentration is a finding
        about the system rather than a limit of the study.</p>
    </div></div>

  <div class="scroll"><table>
    <thead><tr><th>Market</th><th>Ticker</th><th>Calls</th><th>Hit rate</th>
      <th>Return</th><th>Per call</th></tr></thead>
    <tbody>{mk}</tbody>
  </table></div>
  <p class="snote" style="margin-top:16px">Return is the sum of each call's
    open-to-close move in its own direction, so the column adds to the
    {a['pct']['total']*100:+.1f}% in section&nbsp;06. Hit rate and return are
    not the same question: {html.escape(best_ret['name'])} produced
    {best_ret['ret']*100:+.1f}% from {best_ret['n']} calls, while
    {html.escape(biggest['name'])} needed {biggest['n']} to make
    {biggest['ret']*100:+.1f}%. Per-market rows sit on tiny samples and are
    exploratory; several are a single call.</p>

  <details style="margin-top:26px"><summary>All {a['n']} calls, most recent
    first</summary>
    <div class="scroll tall"><table>
      <thead><tr><th>Date</th><th>Market</th><th>Call</th><th>Result</th>
        <th>Return</th></tr></thead>
      <tbody>{log}</tbody></table></div>
  </details>
</section>"""


def study_two(v2: dict, pp) -> str:
    """Sections 05-06. A second instrument on the same certificate: same rules,
    same bands, a wider question."""
    a = v2["streams"][v2["primary"]]
    lo, hi = a["lo"], a["hi"]
    inconclusive = lo < 0 < hi
    w0, w1 = v2["window"]
    return f"""
<div class="stamp rise">{"Inconclusive" if inconclusive else "Signal detected"}</div>

<section class="rise" style="animation-delay:.06s">
  <div class="shead"><span class="snum">05</span>
    <div><h2 class="stitle">One trade, any market</h2>
      <p class="snote">The same news, read without a fixed instrument list. Each
        morning the system names its single best trade in any market it likes,
        and is scored against that market&rsquo;s own drift rather than against
        a coin flip. {a['n']} calls over {v2['days']} days.</p>
    </div></div>
  <span class="value small">{pp(a['skill'])}</span>
  <p class="interval"><b>95% interval</b>{pp(lo)} &nbsp;to&nbsp; {pp(hi)}</p>
  {scale(a['skill'], lo, hi, pp,
         f"Skill {pp(a['skill'])}, 95 percent interval {pp(lo)} to {pp(hi)}")}
  <p class="snote">It was right {a['hit_rate']*100:.0f}% of the time. The band
    {"still contains zero, so this sample cannot yet tell a real edge from noise"
     if inconclusive else "excludes zero"}. Study&nbsp;I sits at {pp(-0.028)};
    this one sits {pp(a['skill'])}, so the estimate moved but the verdict did
    not.</p>
</section>
{in_percent(a)}

<hr class="srule">
<section class="rise" style="animation-delay:.12s">
  <div class="shead"><span class="snum">07</span>
    <div><h2 class="stitle">What it read</h2>
      <p class="snote">Same prompt, same days, two different inputs. All four
        bands are drawn on one shared scale, so their positions can be compared
        directly.</p>
    </div></div>
  {comparison(v2, pp)}
  <p class="snote" style="margin-top:20px">Reading the newspapers lands to the
    right of reading the summary of them. Every band is far too wide to call
    that a result, but it is the direction the design predicted: the digest was
    written to brief one instrument, and it carries that shape into every call
    made from it.</p>
</section>
{ledger(a)}
{forward_block(v2)}

<footer class="rise" style="animation-delay:.16s">
  <p><strong>Retrospective.</strong> This study reads news old enough that the
    model may recall what followed, which biases the estimate upward by an
    unknown amount. A positive figure here is suggestive only; the forward
    record is the evidence.</p>
  <dl class="prov">
    <dt>Window</dt><dd>{w0} &ndash; {w1}</dd>
    <dt>Sample</dt><dd>{a['n']} scored calls, {a['unscoreable_pct']*100:.0f}%
      of calls named a market with no priceable equivalent</dd>
    <dt>Prompt</dt><dd>{v2['prompt_sha'][:24]}&hellip;</dd>
    <dt>Baseline</dt><dd>each market&rsquo;s own drift in the called direction</dd>
  </dl>
</footer>"""


def page(*, now, days_all, fwd_days, retro_days, trades, total, t_lo, t_hi,
         wins, net_std, cd, c_lo, c_hi, n_calls, inconclusive,
         checkpoint, checkpoint_2, table_rows, money, pp, prompt_sha,
         dollars_per_point, cost_points, n_boot, seed, v2=None,
         pct_total=None, pct_mean=None) -> str:
    n_tr = len(trades)
    ratio = abs((t_hi - t_lo) / total) if total else 0
    pct_ck = min(100.0, len(days_all) / checkpoint * 100)
    per_trade = money(total / n_tr) if n_tr else money(0)
    v2_block = study_two(v2, pp) if v2 else ""
    # Same trades expressed as percent of the index, so Study I and Study II
    # can be compared without converting between contracts and instruments.
    pct_line = "" if pct_total is None else (
        f'<p class="snote">As a percent of the index rather than in contract '
        f'dollars, the same {n_tr} trades come to {pct_total*100:+.1f}% in '
        f'total, {pct_mean*100:+.2f}% each. Study&nbsp;II is reported the same '
        f'way in section&nbsp;06.</p>')

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Forecast scorecard &middot; forward test</title>
<style>{CSS}</style></head><body><main>

<header class="rise">
  <p class="eyebrow">Pulse &middot; pre-registered forward test{" &middot; two studies" if v2 else ""}</p>
  <h1>Forecast<br><em>scorecard</em></h1>
  <p class="dek">Each morning's news is turned into a directional call, then
    scored against what the market actually did. Every rule was frozen before
    any price was seen.</p>
</header>

<input type="radio" name="study" id="t1" class="tabin" checked>
<input type="radio" name="study" id="t2" class="tabin"{"" if v2 else " disabled"}>
<div class="tabs rise" role="tablist" aria-label="Studies">
  <label for="t1"><em class="tnum">I</em>Fixed instruments</label>
  <label for="t2"><em class="tnum">II</em>Any market</label>
</div>

<div class="panel p1">
<div class="stamp rise">{"Inconclusive" if inconclusive else "Signal detected"}</div>

<hr class="srule">
<section class="rise" style="animation-delay:.08s">
  <div class="shead"><span class="snum">01</span>
    <div><h2 class="stitle">Simulated result</h2>
      <p class="snote">One NQ contract, long on an up call and short on a down
        call, entered at the open and flat by the close. {n_tr} trades, no stop.</p>
    </div></div>
  <span class="value">{money(total)}</span>
  <p class="interval"><b>95% interval</b>{money(t_lo)} &nbsp;to&nbsp; {money(t_hi)}</p>
  {scale(total, t_lo, t_hi, money,
         f"Result {money(total)}, 95 percent interval {money(t_lo)} to {money(t_hi)}")}
  <p class="snote">The shaded band is the interval. It is about
    {ratio:.0f}&times; wider than the figure above it and it contains zero, so
    the figure is a draw from noise rather than a finding.</p>
  {pct_line}
</section>

<hr class="srule">
<section class="rise" style="animation-delay:.16s">
  <div class="shead"><span class="snum">02</span>
    <div><h2 class="stitle">Primary test</h2>
      <p class="snote">Does an up call actually beat a down call? The frozen
        statistic is P(up&nbsp;|&nbsp;said&nbsp;up) minus
        P(up&nbsp;|&nbsp;said&nbsp;down), pooled across instruments.</p>
    </div></div>
  <span class="value small">{pp(cd)}</span>
  <p class="interval"><b>95% interval</b>{pp(c_lo)} &nbsp;to&nbsp; {pp(c_hi)}</p>
  {scale(cd, c_lo, c_hi, pp,
         f"Difference {pp(cd)}, 95 percent interval {pp(c_lo)} to {pp(c_hi)}")}
</section>

<hr class="srule">
<section class="rise" style="animation-delay:.24s">
  <div class="shead"><span class="snum">03</span>
    <div><h2 class="stitle">Sample</h2></div></div>
  <dl class="stats">
    <div class="stat"><dt>Days</dt><dd>{len(days_all)}</dd></div>
    <div class="stat"><dt>Calls</dt><dd>{n_calls}</dd></div>
    <div class="stat"><dt>Win rate</dt>
      <dd>{wins/n_tr*100 if n_tr else 0:.0f}%</dd></div>
    <div class="stat"><dt>&sigma; / trade</dt><dd>{net_std}</dd></div>
  </dl>
  <p class="snote" style="margin-top:24px">Progress to the first pre-registered
    checkpoint, where the test can first resolve an effect of realistic size.</p>
  <div class="meter"><i style="width:{pct_ck:.1f}%"></i></div>
  <div class="mlabels"><span>{len(days_all)} of {checkpoint}</span>
    <span>then {checkpoint_2}</span></div>
  <p class="snote" style="margin-top:14px">{len(fwd_days)} forward &middot;
    {len(retro_days)} retrospective &middot; {per_trade} per trade</p>
  <p class="warn" style="margin-top:14px"><b>Forward collection closed
    2026-07-28.</b> The morning job for this study was stopped on cost grounds,
    not on results: it needed years to resolve an effect this small, and Study
    II's stream A tests the same question on a better input. The retrospective
    verdict above is final and the forward counter will not advance again. Said
    here because a frozen record that still looks live is the failure this page
    exists to prevent.</p>
</section>

<hr class="srule">
<section class="rise" style="animation-delay:.32s">
  <div class="shead"><span class="snum">04</span>
    <div><h2 class="stitle">Log</h2></div></div>
  <details><summary>Last 15 simulated trades</summary>
    <div class="scroll"><table>
      <thead><tr><th>Date</th><th>Call</th><th>Net</th><th>Return</th></tr></thead>
      <tbody>{table_rows}</tbody></table></div>
  </details>
</section>

<footer class="rise" style="animation-delay:.4s">
  <p><strong>Exploratory.</strong> The dollar view is not a claim that any of
    this is tradeable. A hit rate says nothing about magnitude, and the
    simulation carries no stop. Nothing here is coloured by sign, because a
    figure this uncertain has no sign worth colouring.</p>
  <dl class="prov">
    <dt>Window</dt><dd>{days_all[0]} &ndash; {days_all[-1]}</dd>
    <dt>Prompt</dt><dd>{prompt_sha[:24]}&hellip;</dd>
    <dt>Method</dt><dd>NQ via ^NDX points &times; ${dollars_per_point:.0f};
      {cost_points}pt round trip</dd>
  </dl>
</footer>
</div>

<div class="panel p2">{v2_block}</div>

<p class="colophon">Bootstrap {n_boot:,} draws, seed {seed}, resampled by day
  &middot; regenerated {now:%Y-%m-%d %H:%M %Z}</p>
</main></body></html>"""
