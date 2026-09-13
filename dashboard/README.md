# Who Gets Plowed First — UOIP portfolio

English, read-only portfolio for the Winnipeg winter-operations story. The home
page is a poster-style narrative (five acts); `#/evidence` is the library of all
23 `sql/presentation` queries — 19 core findings and 4 explanatory views — each
with its chart or data view, SQL, freeze time and certification.

The browser never connects to Trino or MinIO. It reads frozen exports only.

## Build

From the repository root, package the frozen exports (the certified JSON under
`var/presentation/outputjson/`):

```sh
uv run python -m scripts.presentation.portfolio
```

This writes `dashboard/public/data/evidence.json` and `zones.json` (untracked).
A missing export stays visible as missing; an export marked SAMPLE is labelled as
sample, never as a production result.

```sh
cd dashboard
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build    # static files in dashboard/dist, deployable from any path
```

## Rules the page keeps

- Every public number is tied to a fig_id and links to its evidence entry.
  Captions and "How not to read this" notes are hand-checked English translations
  of each query's `caption:` / `must_not_say:` header — see
  `scripts/presentation/portfolio.py` and `render_html.ENGLISH_CAPTIONS`.
- Plow zones without a residential schedule are drawn as dashed outlines, never
  filled. The two score profiles never share an axis. The model is always shown
  with its no-month control.
- No CJK characters in `dashboard/src` or `index.html`
  (`tests/unit/test_portfolio.py`). SQL source views are the one exception.

## Credits

Spotlight, Tracing Beam, Sticky Scroll Reveal and Bento Grid are adapted from
[Aceternity UI](https://ui.aceternity.com) (JSX + Tailwind CSS v4 + Motion).
Charts are hand-written SVG. Type: Big Shoulders Display, IBM Plex Sans, IBM Plex Mono.
