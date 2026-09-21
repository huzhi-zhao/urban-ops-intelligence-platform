# Who Gets Plowed First — UOIP portfolio

English, read-only portfolio for the Winnipeg winter-operations story. Three
pages: the home page is a poster-style narrative (five acts); `#/zone` lets a
reader pick a plow zone and read the two answers the evidence supports; and
`#/evidence` is the library of all 25 `sql/presentation` queries — 19 core
findings, 4 explanatory views and 2 zone-lookup queries — each with its chart or
data view, SQL, freeze time and certification.

The browser never connects to Trino or MinIO. It reads frozen exports only.

## Build

From the repository root, package the frozen exports (the certified JSON under
`var/presentation/`, where `make eda-export` writes them):

```sh
uv run python -m scripts.presentation.portfolio
```

or `make portfolio` from the root. This writes `dashboard/public/data/evidence.json`,
`zones.json` and `lookup.json` (all untracked). `zones.json` and `lookup.json`
are removed rather than left stale when their exports are absent — in a browser
last week's copy is indistinguishable from today's.
A missing export stays visible as missing; an export marked SAMPLE is labelled as
sample, never as a production result.

```sh
cd dashboard
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build    # static files in dashboard/dist, deployable from any path
```

## Presenting on two screens

**Ctrl+Shift+Cmd+P** (Ctrl+Shift+Alt+P off a Mac) opens a projector window that
follows this one: same page, same place on the page. Put it on the extended
screen and drive from the laptop. It drops the header and the footer, which are
of no use on a screen nobody can click.

There is deliberately no button for it. The header faces the room during a talk,
and a control there is both an invitation to click it mid-sentence and a word
the audience reads and wonders about.

What crosses the channel is `{hash, p}` — the hash plus scroll progress 0..1,
never `scrollTop`: the two screens are different sizes, so pixels land the
projector a few hundred px off, which is the hardest error to notice on stage.
The sync is one-directional; scrolling the projector does not move the laptop.

Chromium only, and it needs a secure context. `npm run dev` on 127.0.0.1
qualifies; opening `dist/index.html` over `file://` does **not** — a file:// page
has an opaque origin, so BroadcastChannel silently delivers nothing and the
projector simply never moves. Serve the build with `npm run preview` instead.

## Rules the page keeps

- Every public number is tied to a fig_id and links to its evidence entry.
  Captions and "How not to read this" notes are hand-checked English translations
  of each query's `caption:` / `must_not_say:` header — see
  `scripts/presentation/portfolio.py` and `render_html.ENGLISH_CAPTIONS`.
- `#/zone` does no arithmetic of its own: every ratio is summed in
  `scripts/presentation/zone_lookup.py`, where a unit test can see it, because a
  mean of per-zone rates weights an 18-transition zone like the whole city
  (`.claude/rules/gold-sql.md` R3). It is also not the City's clearing-status
  map and says so on the page (business-objectives §0.1).
- Plow zones without a residential schedule are drawn as dashed outlines, never
  filled, and get an answer on `#/zone` rather than an empty card. The two score profiles never share an axis. The model is always shown
  with its no-month control.
- No CJK characters in `dashboard/src` or `index.html`
  (`tests/unit/test_portfolio.py`). SQL source views are the one exception.

## Credits

Spotlight, Tracing Beam, Sticky Scroll Reveal and Bento Grid are adapted from
[Aceternity UI](https://ui.aceternity.com) (JSX + Tailwind CSS v4 + Motion).
Charts are hand-written SVG. Type: Big Shoulders Display, IBM Plex Sans, IBM Plex Mono.
