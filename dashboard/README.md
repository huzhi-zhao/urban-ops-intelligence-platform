# Who Gets Plowed First — UOIP portfolio

English, read-only portfolio for the Winnipeg winter-operations story. Three
pages: the home page is a poster-style narrative (five acts); `#/zone` lets a
reader pick a plow zone and read the two answers the evidence supports; and
`#/evidence` is the library of every `sql/presentation` query — 19 core
findings, 4 explanatory views and the zone-lookup queries — each with its chart
or data view, SQL, freeze time and certification. `#/zone` also carries a section
that puts the demand model's estimate next to the published plan for one past
snowfall (ADR 0015); its estimates stay hidden until their ranges exist.

The browser never connects to Trino or MinIO. It reads frozen exports only.

## Build

From the repository root, package the frozen exports (the certified JSON under
`var/presentation/`, where `make eda-export` writes them):

```sh
uv run python -m scripts.presentation.portfolio
```

or `make portfolio` from the root. When the demand-and-plan export
(`FIG-BO8-03`) holds more than one model version, name the one to serve —
`make portfolio FORECAST_VERSION=<model_version>` — or the build refuses: one of
the stored versions is a deliberately degraded control. This writes `dashboard/public/data/evidence.json`,
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

## Deploy

The site is static files served by nginx on the compute node, behind a
Cloudflare proxy. There is no deploy pipeline; this is the manual procedure.

1. **Freeze on the node.** Exports are taken where Trino is reachable, into the
   node checkout's `var/presentation/`, e.g.
   `TRINO_HOST=localhost TRINO_PORT=8090 make eda-export ONLY=FIG-BO8-03`.
2. **Copy the exports to the build machine** (`var/presentation/FIG-*.json`) and
   package them: `make portfolio IN=<exports> FORECAST_VERSION=<model_version>`.
   Check the summary line reads `missing: 0`.
3. **Build.** `cd dashboard && npm run build`. The frozen data is copied into
   `dist/data/`, so the directory is self-contained.
4. **Upload** `dist/` to a dated directory on the node, e.g.
   `rsync -a dist/ <node>:uoip-portfolio-site-YYYYMMDD/`.
5. **Swap, keeping the old one.** The nginx root is
   `/opt/uoip/uoip-portfolio-site`:

   ```sh
   sudo mv /opt/uoip/uoip-portfolio-site /opt/uoip/uoip-portfolio-site.bak-<old date>
   sudo mv ~/uoip-portfolio-site-YYYYMMDD /opt/uoip/uoip-portfolio-site
   ```

   No nginx reload is needed. Rolling back is the same two moves in reverse.
6. **Verify live**, not locally: the asset hash in
   `curl -s https://uoip.huzhi.dev` matches `dist/index.html`,
   `https://uoip.huzhi.dev/data/evidence.json` has the expected number of
   queries, and the pages open without console errors.

nginx serves `index.html` and `/data/` with `no-cache` and `/assets/` as
immutable (the file names carry a content hash), so a swap shows up on the next
load without purging Cloudflare.

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
