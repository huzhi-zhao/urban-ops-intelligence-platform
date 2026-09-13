import {useMemo, useState} from 'react';
import {CHART_FOR, ChartState} from './charts';
import {useData, useFigure} from './data';
import {cn} from './lib/utils';
import {ExternalLink} from './story';

const CHAPTERS = ['All', 'Scheduled order', 'Work zones and wards', 'Snowfall events', 'Demand and model', 'Load score', 'Recommendation experiment'];
const ROW_LIMIT = 100;

function Badge({children, tone = 'frost'}) {
  const tones = {
    frost: 'border-rule text-frost',
    ice: 'border-glacier/60 text-ice',
    sodium: 'border-sodium/60 text-sodium',
  };
  return <span className={cn('rounded-full border px-2.5 py-0.5 font-mono text-[11px] tracking-wide', tones[tone])}>{children}</span>;
}

function DataTable({figure}) {
  const rows = figure.rows.slice(0, ROW_LIMIT);
  return (
    <div>
      <div className="max-h-[480px] overflow-auto rounded-xl border border-rule">
        <table className="w-full border-collapse font-mono text-xs">
          <caption className="sr-only">{figure.copy.title} — frozen query result</caption>
          <thead className="sticky top-0 bg-deep">
            <tr>{figure.columns.map(c => <th key={c} scope="col" className="border-b border-rule px-3 py-2 text-left font-medium whitespace-nowrap text-snow">{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="odd:bg-deep/40">
                {row.map((cell, j) => <td key={j} className="px-3 py-1.5 whitespace-nowrap text-frost tabular">{cell === null ? '—' : String(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 font-mono text-[11px] text-frost">
        {figure.rows.length > ROW_LIMIT ? `Showing the first ${ROW_LIMIT} of ${figure.rows.length.toLocaleString('en-CA')} rows.` : `${figure.rows.length} rows.`}
        {figure.id === 'FIG-BO4-00' && ' Geometry is drawn in the chart view rather than printed here.'}
      </p>
    </div>
  );
}

function Detail({id}) {
  const {figure, status} = useFigure(id);
  const [tab, setTab] = useState('chart');
  if (!figure) return <ChartState status={status} label="evidence entry" />;
  const Chart = CHART_FOR[id];
  const hasData = figure.state !== 'missing';
  const tabs = [Chart && hasData && ['chart', 'Chart'], hasData && ['data', 'Data view'], ['sql', 'SQL']].filter(Boolean);
  const current = tabs.some(([key]) => key === tab) ? tab : tabs[0][0];
  const cert = figure.certification ?? {};

  return (
    <article aria-labelledby="evidence-title" className="min-w-0">
      <div className="flex flex-wrap gap-2">
        <Badge tone="ice">{figure.chapter}</Badge>
        <Badge>{figure.role === 'core' ? 'Core evidence' : 'Explanatory view'}</Badge>
        {figure.state === 'sample' && <Badge tone="sodium">Sample data — not a production result</Badge>}
        {figure.state === 'missing' && <Badge tone="sodium">No frozen export</Badge>}
      </div>
      <h1 id="evidence-title" className="mt-4 font-display text-4xl leading-none font-black tracking-wide text-snow md:text-5xl">{figure.copy.title}</h1>
      <p className="mt-5 max-w-3xl text-frost">{figure.copy.caption}</p>
      <aside className="mt-5 max-w-3xl rounded-xl border-l-2 border-sodium bg-sodium/[0.06] px-5 py-4 text-[15px] text-snow/90">
        <div className="mb-1 font-mono text-[11px] tracking-widest text-sodium uppercase">How not to read this</div>
        {figure.copy.must_not_say}
      </aside>

      <div role="tablist" aria-label="Evidence views" className="mt-8 flex gap-1 border-b border-rule">
        {tabs.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={current === key} onClick={() => setTab(key)}
            className={cn('min-h-11 px-4 font-mono text-xs tracking-wide transition', current === key ? 'border-b-2 border-ice text-snow' : 'text-frost hover:text-snow')}>
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="mt-6">
        {current === 'chart' && <div className="max-w-3xl rounded-2xl border border-rule bg-deep/70 p-5 overflow-x-auto"><div className={id === 'FIG-BO4-01' ? 'min-w-[520px]' : ''}><Chart /></div></div>}
        {current === 'data' && <DataTable figure={figure} />}
        {current === 'sql' && (
          <div>
            <p className="mb-3 text-[15px] text-frost">The query header in the repository is written in Chinese; the caption and reading limits above are its hand-checked English translation.</p>
            <pre className="max-h-[560px] overflow-auto rounded-xl border border-rule bg-deep p-4 font-mono text-xs leading-relaxed text-snow/90"><code>{figure.sql}</code></pre>
          </div>
        )}
      </div>

      <dl className="mt-8 grid max-w-3xl gap-x-8 gap-y-3 font-mono text-xs sm:grid-cols-2">
        <div><dt className="text-frost">Query</dt><dd><ExternalLink href={figure.source_url} className="text-ice hover:underline">{figure.source_sql}</ExternalLink></dd></div>
        <div><dt className="text-frost">Frozen at</dt><dd className="text-snow">{figure.frozen_at ?? '—'}</dd></div>
        <div><dt className="text-frost">Certification</dt><dd className="text-snow">{cert.status ?? '—'}{cert.run_id ? ` · ${cert.run_id}` : ''}</dd></div>
        <div><dt className="text-frost">Audit result</dt><dd className="text-snow">{cert.error_count ?? '—'} errors · {cert.warn_count ?? '—'} warnings</dd></div>
        <div><dt className="text-frost">Figure ID</dt><dd className="text-snow">{figure.id} · {figure.bo}</dd></div>
        <div><dt className="text-frost">Layer</dt><dd className="text-snow">{figure.schema}</dd></div>
      </dl>
    </article>
  );
}

export function Evidence({selected}) {
  const {figures, status} = useData();
  const [chapter, setChapter] = useState('All');
  const [query, setQuery] = useState('');
  const list = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return Object.values(figures)
      .filter(f => chapter === 'All' || f.chapter === chapter)
      .filter(f => !needle || `${f.id} ${f.copy.title} ${f.copy.caption}`.toLowerCase().includes(needle))
      .sort((a, b) => (a.role === b.role ? a.id.localeCompare(b.id) : a.role === 'core' ? -1 : 1));
  }, [figures, chapter, query]);
  const current = selected && figures[selected] ? selected : list[0]?.id;

  return (
    <main className="mx-auto max-w-7xl px-5 py-12 md:px-8">
      <header className="max-w-3xl">
        <div className="font-mono text-xs tracking-[0.2em] text-ice uppercase">Evidence library</div>
        <p className="mt-3 text-lg text-frost">
          Every figure in the story comes from one of these 23 queries: 19 core findings and 4 explanatory views. Each shows its chart or data, the SQL, when it was frozen and how it was certified.
        </p>
      </header>

      <div className="mt-8 flex flex-wrap gap-2" role="group" aria-label="Filter by chapter">
        {CHAPTERS.map(name => (
          <button key={name} onClick={() => setChapter(name)} aria-pressed={chapter === name}
            className={cn('min-h-11 rounded-full border px-4 text-sm transition', chapter === name ? 'border-ice bg-ice text-night' : 'border-rule text-frost hover:border-glacier hover:text-snow')}>
            {name}
          </button>
        ))}
      </div>

      <div className="mt-8 grid gap-10 lg:grid-cols-[300px_minmax(0,1fr)]">
        <nav aria-label="Queries">
          <label className="block">
            <span className="sr-only">Search queries</span>
            <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search titles and captions"
              className="min-h-11 w-full rounded-xl border border-rule bg-deep px-4 text-[15px] text-snow placeholder:text-frost/70 focus:border-ice focus:outline-none" />
          </label>
          {status === 'error' && <p className="mt-4 text-[15px] text-sodium">The evidence catalogue did not load. Rebuild the site data, or read the queries directly in sql/presentation on GitHub.</p>}
          {status === 'ready' && list.length === 0 && <p className="mt-4 text-[15px] text-frost">No query matches “{query}”. Clear the search or pick another chapter.</p>}
          <ul className="mt-4 space-y-1 lg:max-h-[70vh] lg:overflow-auto lg:pr-2">
            {list.map(f => (
              <li key={f.id}>
                <a href={`#/evidence/${f.id}`} aria-current={current === f.id ? 'page' : undefined}
                  className={cn('block rounded-xl px-3 py-2.5 transition', current === f.id ? 'bg-deep ring-1 ring-glacier' : 'hover:bg-deep/60')}>
                  <div className="text-[15px] leading-snug text-snow">{f.copy.title}</div>
                  <div className="mt-0.5 font-mono text-[11px] text-frost">{f.id} · {f.role === 'core' ? 'core' : 'explanatory'}</div>
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {current ? <Detail key={current} id={current} /> : <ChartState status={status} label="evidence library" />}
      </div>
    </main>
  );
}
