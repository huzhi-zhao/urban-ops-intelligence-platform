import {motion} from 'motion/react';
import {BentoGrid, BentoGridItem} from './components/ui/bento-grid';
import {Spotlight} from './components/ui/spotlight';
import {StickyScroll} from './components/ui/sticky-scroll-reveal';
import {TracingBeam} from './components/ui/tracing-beam';
import {
  DriftSlope, Displacement, EventTimeline, FactorSpread, KeywordFilterBar, ModelControls, OperationGrid,
  PanelSplit, ProfilePanels, ShiftLadder, ShiftLegend, SnowWindow, WardMatrix, WinterCategories, ZoneMap,
} from './charts';
import {useFigure} from './data';
import {GITHUB, cn} from './lib/utils';

const reveal = {
  initial: {opacity: 0, y: 24},
  whileInView: {opacity: 1, y: 0},
  viewport: {once: true, margin: '-12% 0px'},
  transition: {duration: 0.6, ease: 'easeOut'},
};

export function EvidenceLink({id, children = 'See the query and data'}) {
  return (
    <a href={`#/evidence/${id}`} className="inline-flex min-h-11 items-center gap-2 font-mono text-xs tracking-wide text-ice underline-offset-4 hover:underline">
      {children} <span aria-hidden="true">→</span> <span className="text-frost">{id}</span>
    </a>
  );
}

export function ExternalLink({href, children, className}) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className={className}>
      {children} <span aria-hidden="true">↗</span><span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

function Limit({children, title = 'How not to read this'}) {
  return (
    <aside className="rounded-xl border-l-2 border-sodium bg-sodium/[0.06] px-5 py-4 text-[15px] text-snow/90">
      <div className="mb-1 font-mono text-[11px] tracking-widest text-sodium uppercase">{title}</div>
      {children}
    </aside>
  );
}

function Act({id, label, title, lede, children}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-20 py-24 md:py-32">
      <motion.header {...reveal} className="max-w-3xl">
        <div className="font-mono text-xs tracking-[0.2em] text-ice uppercase">{label}</div>
        <h2 id={`${id}-title`} className="mt-4 font-display text-5xl leading-[0.92] font-black tracking-wide text-snow uppercase md:text-7xl">{title}</h2>
        {lede && <p className="mt-6 text-lg text-frost md:text-xl">{lede}</p>}
      </motion.header>
      <div className="mt-14">{children}</div>
    </section>
  );
}

function Stat({value, label, tone = 'ice'}) {
  return (
    <div>
      <div className={cn('font-display text-6xl leading-none font-black tabular md:text-7xl', tone === 'sodium' ? 'text-sodium' : 'text-ice')}>{value}</div>
      <div className="mt-2 max-w-[16rem] text-[15px] leading-snug text-frost">{label}</div>
    </div>
  );
}

function Panel({children, className}) {
  return <motion.div {...reveal} className={cn('rounded-2xl border border-rule bg-deep/70 p-5 md:p-7', className)}>{children}</motion.div>;
}

/* ------------------------------------------------------------------------ */

function Hero() {
  const words = ['Who', 'gets', 'plowed', 'first?'];
  return (
    <section id="top" className="relative isolate overflow-hidden street-grid">
      <Spotlight className="-top-40 left-0 md:-top-20 md:left-60" fill="#8fd6ff" />
      <div className="mx-auto grid min-h-[calc(100svh-4rem)] max-w-7xl items-center gap-10 px-5 py-14 md:px-8 lg:grid-cols-[1.05fr_1fr]">
        <div className="relative z-10">
          <motion.div initial={{opacity: 0}} animate={{opacity: 1}} transition={{duration: 0.6}}
            className="font-mono text-xs tracking-[0.22em] text-ice uppercase">
            Winnipeg · ten winters of open data
          </motion.div>
          <h1 className="mt-6 font-display text-[clamp(4.2rem,11vw,9.5rem)] leading-[0.82] font-black tracking-wide text-snow uppercase">
            {words.map((word, i) => (
              <motion.span key={word} className={cn('block', i === 3 && 'text-ice')}
                initial={{opacity: 0, y: 30, filter: 'blur(8px)'}} animate={{opacity: 1, y: 0, filter: 'blur(0px)'}}
                transition={{duration: 0.7, delay: 0.15 + i * 0.12}}>
                {word}
              </motion.span>
            ))}
          </h1>
          <motion.p initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 0.9, duration: 0.8}}
            className="mt-8 max-w-xl text-lg text-frost md:text-xl">
            Winnipeg publishes snowfall records, 311 service requests and residential plow schedules.
            We joined them in a self-hosted lakehouse — and the data kept changing the question.
          </motion.p>
          <motion.div initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 1.1, duration: 0.8}} className="mt-9 flex flex-wrap gap-3">
            <a href="#act-1" className="inline-flex min-h-12 items-center gap-2 rounded-full bg-ice px-6 font-medium text-night transition hover:bg-snow">
              Explore the story <span aria-hidden="true">↓</span>
            </a>
            <ExternalLink href={GITHUB} className="inline-flex min-h-12 items-center gap-2 rounded-full border border-rule px-6 text-snow transition hover:border-ice">
              View the source on GitHub
            </ExternalLink>
          </motion.div>
        </div>
        <figure className="relative z-10">
          <div className="mx-auto max-w-[560px]">
            <ZoneMap mode="shift" />
          </div>
          <figcaption className="mx-auto mt-4 max-w-[560px] space-y-3">
            <ShiftLegend />
            <p className="text-[15px] text-frost">
              Winnipeg&rsquo;s 25 plow zones, lit in order of their <span className="text-snow">average scheduled shift</span> across 19 city-wide residential operations.
            </p>
            <a href="#/map" className="inline-flex min-h-11 items-center gap-2 rounded-full border border-rule px-5 text-sm text-snow transition hover:border-ice">
              Open the zone map <span aria-hidden="true">↗</span>
            </a>
          </figcaption>
        </figure>
      </div>
      <motion.p initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 3.2, duration: 1}}
        className="relative z-10 mx-auto max-w-7xl px-5 pb-12 font-display text-2xl tracking-wide text-snow md:px-8 md:text-3xl">
        If every residential zone is scheduled, what does &ldquo;first&rdquo; actually mean?
      </motion.p>
    </section>
  );
}

/* ------------------------------------------------------------------------ */

function ActOne() {
  return (
    <Act id="act-1" label="Act I · The wrong question" title={<>Nobody is <span className="text-ice">skipped.</span></>}
      lede="The obvious question was who gets left out. The schedules said otherwise: in every one of 19 city-wide residential plow operations since December 2015, all 22 scheduled zones were on the list.">
      <div className="grid items-center gap-8 lg:grid-cols-[1fr_1.2fr]">
        <motion.div {...reveal} className="space-y-8">
          <Stat value="418 / 418" label="operation-by-zone schedule cells present · 19 operations × 22 zones · none missing" />
          <p className="text-frost">So the question the data can answer is narrower, and more interesting: <span className="text-snow">who is usually scheduled earlier?</span></p>
          <EvidenceLink id="FIG-BO2-03" />
        </motion.div>
        <Panel><OperationGrid /><p className="mt-3 font-mono text-xs text-frost">each square is one zone in one operation · bright = scheduled in the first shift · faded columns = the two operations that match no snowfall event</p></Panel>
      </div>

      <div className="mt-24 grid gap-10 lg:grid-cols-[0.8fr_1.2fr]">
        <motion.div {...reveal} className="space-y-8 lg:sticky lg:top-24 lg:self-start">
          <div className="grid grid-cols-2 gap-6">
            <Stat value="1.26" label="Zone S · average scheduled shift, the earliest" />
            <Stat value="3.47" label="Zone C · average scheduled shift, the latest" />
          </div>
          <p className="text-lg text-frost">
            Two ends of the schedule sit <span className="text-snow">2.21 shifts apart — about 26 hours of planned start offset</span>, with shifts 12 hours long.
          </p>
          <Limit>
            This is <b>scheduled position</b>: when a zone&rsquo;s shift was planned to start. It is not when a street was cleared, not how long anyone actually waited, and not a verdict on fairness.
          </Limit>
          <EvidenceLink id="FIG-BO2-01" />
        </motion.div>
        <Panel><ShiftLadder /></Panel>
      </div>

      <div className="mt-24 grid items-center gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <Panel className="order-2 lg:order-1"><DriftSlope /></Panel>
        <motion.div {...reveal} className="order-1 space-y-6 lg:order-2">
          <h3 className="font-display text-4xl leading-none font-bold tracking-wide text-snow md:text-5xl">Then the order moved.</h3>
          <p className="text-lg text-frost">
            21 of 22 zones have been in the first shift at least once — including C, the latest on average. Split the 19 operations into the first 9 and the last 10, and
            <span className="text-snow"> zone V moves 1.31 shifts later, zone M 1.02</span>.
          </p>
          <p className="text-frost">
            A long-run average difference and a moving order are both true. Neither says why: the data measures the outcome, not the rule behind it.
          </p>
          <p className="text-frost">
            One easy explanation fails. Zones with more addresses are not scheduled earlier; they tend to be scheduled later (r = +0.49).
          </p>
          <div className="flex flex-wrap gap-x-6">
            <EvidenceLink id="FIG-BO2-02" />
            <EvidenceLink id="FIG-BO2-04">Check the address counter-test</EvidenceLink>
          </div>
        </motion.div>
      </div>
    </Act>
  );
}

/* ------------------------------------------------------------------------ */

function ActTwo() {
  return (
    <Act id="act-2" label="Act II · The map changes the meaning" title={<>A work zone <span className="text-ice">is not a ward.</span></>}
      lede="Plowing is organised by plow zone. Public conversation — and council — runs by ward. It is tempting to translate one into the other. The requests residents file show why that doesn't work.">
      <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr]">
        <motion.div {...reveal} className="space-y-10">
          <div className="grid grid-cols-2 gap-6">
            <Stat value="2 of 25" label="plow zones whose winter requests all fall in a single ward" />
            <Stat value="54%" label="median share held by a zone's largest ward" />
          </div>
          <figure>
            <div className="mx-auto max-w-[420px]"><ZoneMap mode="v" animate={false} /></div>
            <figcaption className="mt-3 text-[15px] text-frost">
              <span className="text-snow">Zone V</span> is several separate pieces spread across the city. Its winter requests carry
              <span className="text-snow"> 10 different ward labels</span>; the largest, St. Vital, holds only 26%.
            </figcaption>
          </figure>
          <Limit>
            Shares count winter service requests by their ward label, November 2023 to May 2026. They are not land area, population or geometric overlap — no ward boundaries are drawn here. Neither map is wrong: one follows voters, the other follows plow routes.
          </Limit>
          <div className="flex flex-wrap gap-x-6">
            <EvidenceLink id="FIG-BO4-01" />
            <EvidenceLink id="FIG-BO4-02">Dominant-share data</EvidenceLink>
          </div>
        </motion.div>
        <Panel className="overflow-x-auto">
          <div className="min-w-[520px]"><WardMatrix /></div>
          <p className="mt-3 font-mono text-xs text-frost">rows: plow zones, most scattered first · columns: wards · brighter = larger share · dot = largest ward · * no residential schedule</p>
        </Panel>
      </div>
      <motion.p {...reveal} className="mt-16 max-w-3xl font-display text-3xl leading-tight tracking-wide text-snow md:text-4xl">
        Score by ward, and one plow route&rsquo;s workload is split across up to ten wards. So the platform scores by plow zone — and keeps the ward as a label.
      </motion.p>
    </Act>
  );
}

/* ------------------------------------------------------------------------ */

function ActThree() {
  const content = [
    {
      eyebrow: 'Assumption 1 · a keyword finds winter requests',
      title: '99.8% of “ice” wasn’t ice.',
      description: (
        <>
          <p>A loose <code className="font-mono text-snow">%ICE%</code> match looked reasonable. It caught 1,439,574 rows — and 1,437,362 of them were Police inquiries, Animal Services, Quality of Service…</p>
          <p className="text-snow">Fix: an explicit, auditable map from request type to six winter categories.</p>
          <div className="pt-2"><WinterCategories /></div>
          <EvidenceLink id="FIG-BO1-04" />
        </>
      ),
      visual: <KeywordFilterBar />,
    },
    {
      eyebrow: 'Assumption 2 · a snowfall is a day over 3 cm',
      title: 'Snow that never crossed the line.',
      description: (
        <>
          <p>Fourteen days in December 2022: no single day reached 3 cm, yet 12.8 cm fell. A daily threshold alone sees no snowfall there at all.</p>
          <p className="text-snow">Fix: an event is a day of 3 cm or more, <i>or</i> a 10-day total of 10 cm or more. 8 of 99 events exist only because of the second rule.</p>
          <div className="pt-2"><EventTimeline /></div>
          <div className="flex flex-wrap gap-x-6"><EvidenceLink id="FIG-BO3-00" /><EvidenceLink id="FIG-BO3-01">All 99 events</EvidenceLink></div>
        </>
      ),
      visual: <SnowWindow />,
    },
    {
      eyebrow: 'Assumption 3 · weights say what matters',
      title: 'A 0.30 weight isn’t 0.30 of influence.',
      description: (
        <>
          <p>The load score weights requests 0.40, scheduled position 0.30 and weather 0.30. In the 374 fully scored cells, scheduled position mostly contributes 0.06–0.18, and weather is one value per event: it sets how high a score is, not the order inside an event.</p>
          <p className="text-snow">Fix: publish each factor&rsquo;s observed range next to its nominal weight.</p>
          <EvidenceLink id="FIG-BO6-02" />
        </>
      ),
      visual: <FactorSpread />,
    },
  ];
  return (
    <Act id="act-3" label="Act III · The data kept rewriting the question" title={<>Three things we <span className="text-ice">got wrong first.</span></>}
      lede="Each of these assumptions produced plausible numbers. Each was wrong in a way that would only show up in the data.">
      <StickyScroll content={content} />
    </Act>
  );
}

/* ------------------------------------------------------------------------ */

const FLOW = [
  {name: 'Sources', tech: 'City of Winnipeg Open Data · Open-Meteo', body: '311 requests, plow schedules, parking bans, zone boundaries, weather'},
  {name: 'Bronze', tech: 'MinIO', body: 'raw pulls, immutable, one file per day'},
  {name: 'Silver', tech: 'Spark · Airflow', body: 'typed, deduplicated, UTC, zone-assigned'},
  {name: 'Gold', tech: 'Hive Metastore · Trino', body: 'events, zones, panels, scores'},
  {name: 'Findings', tech: 'SQL · frozen exports', body: 'one query per figure, certified'},
];

function ActFour() {
  const hit = useFigure('FIG-BO4-03');
  const latest = hit.rows?.[0];
  return (
    <Act id="act-4" label="Act IV · From pipeline to evidence" title={<>Why it took a <span className="text-ice">whole platform.</span></>}
      lede="Demand, weather, schedules and boundaries live on different endpoints, at different grains, in different time zones and on different maps. Every finding above needs all of them lined up — and needs to be rerun when upstream changes.">
      <motion.ol {...reveal} className="grid gap-3 md:grid-cols-5" aria-label="Data flow from public sources to findings">
        {FLOW.map((step, i) => (
          <li key={step.name} className="relative rounded-2xl border border-rule bg-deep/70 p-5">
            <div className="font-display text-3xl font-black tracking-wide text-snow uppercase">{step.name}</div>
            <div className="mt-2 text-[15px] leading-snug text-frost">{step.body}</div>
            <div className="mt-4 font-mono text-[11px] text-ice">{step.tech}</div>
            {i < FLOW.length - 1 && <span aria-hidden="true" className="absolute top-1/2 -right-3 z-10 hidden font-mono text-ice md:block">→</span>}
          </li>
        ))}
      </motion.ol>
      <p className="mt-4 font-mono text-xs text-frost">Self-hosted in Docker across a storage node and a compute node. No managed cloud services.</p>

      <BentoGrid className="mt-14">
        <BentoGridItem className="md:col-span-2" eyebrow="Replayable ingestion" title="12.47 million service requests"
          description="Bronze is never overwritten. A pagination bug that silently repeated and dropped rows was caught, repaired and written up — duplicates and gaps had cancelled out in the row count."
          header={<div className="font-display text-7xl font-black text-ice/90">12.47M</div>} />
        <BentoGridItem eyebrow="Spatial assignment" title="Rates with denominators"
          description={latest ? `Latest audit: ${latest.hit_rate_pct}% of ${latest.has_geo_denominator.toLocaleString('en-CA')} geocoded requests fall inside a plow zone. Most requests upstream carry no coordinates, so the denominator is always shown.` : 'Every hit rate is published with its denominator: most upstream requests carry no coordinates.'}
          header={<EvidenceLink id="FIG-BO4-03">Audit history</EvidenceLink>} />
        <BentoGridItem eyebrow="Event segmentation" title="99 snowfall events"
          description="Two rules, one daily and one accumulated, turn 18 winters of hourly weather into events every other table can join to." />
        <BentoGridItem eyebrow="Quality audit" title="Certified before it's shown"
          description="Every figure on this page was exported from a single build certified by the out-of-pipeline audit, with 0 errors and 0 warnings." />
        <BentoGridItem eyebrow="Executable evidence" title="23 public SQL queries"
          description="One query per figure, each with its caption and the sentences it must not be used to support."
          header={<a href="#/evidence" className="inline-flex min-h-11 items-center font-mono text-xs text-ice hover:underline">Open the evidence library →</a>} />
      </BentoGrid>
    </Act>
  );
}

/* ------------------------------------------------------------------------ */

function ActFive() {
  return (
    <Act id="act-5" label="Act V · An honest AI layer" title={<>Useful. <span className="text-sodium">Not proven.</span></>}
      lede="On top of the evidence sits a request forecast, a load score and ranked recommendations. The point is not that it works — it is that every assumption is visible and every result can be checked against a control.">
      <div className="grid gap-8 lg:grid-cols-2">
        <Panel>
          <h3 className="font-display text-3xl font-bold tracking-wide text-snow">Most of the panel lacks one kind of evidence.</h3>
          <p className="mt-3 text-frost">1,298 event-by-zone cells. Only 374 have requests, weather and a matching schedule. The other 924 are scored on two factors — a different scale, so they are never drawn on the same axis.</p>
          <div className="mt-6"><PanelSplit /></div>
          <div className="mt-6"><ProfilePanels /></div>
          <div className="mt-4 flex flex-wrap gap-x-6"><EvidenceLink id="FIG-BO6-01" /><EvidenceLink id="FIG-BO6-03">Both profiles</EvidenceLink></div>
        </Panel>
        <div className="space-y-8">
          <Panel>
            <h3 className="font-display text-3xl font-bold tracking-wide text-snow">The model beats a weak baseline — so does its broken twin.</h3>
            <p className="mt-3 text-frost">A holdout of 7 events. We deliberately trained a second model with the month removed. It lands within 0.57 of the real one; both sit about 16 below the baseline.</p>
            <div className="mt-6"><ModelControls /></div>
            <div className="mt-2"><EvidenceLink id="FIG-BO1-03" /></div>
          </Panel>
          <Panel>
            <h3 className="font-display text-3xl font-bold tracking-wide text-snow">Moving up is not getting better.</h3>
            <p className="mt-3 text-frost">Re-ranking zones with the forecast moves 188 cells up — for both versions. Inside each event the ranks are a permutation of 1 to 22, so every move up is paid for by a move down.</p>
            <div className="mt-6"><Displacement /></div>
            <div className="mt-2"><EvidenceLink id="FIG-BO8-01" /></div>
          </Panel>
        </div>
      </div>
      <motion.div {...reveal} className="mt-10">
        <Limit title="What this layer does not claim">
          It does not show the model beating a proper operational baseline, and a backtest on archived weather is not a forecast made before a storm. It is an auditable experiment with its assumptions in the open.
        </Limit>
      </motion.div>
    </Act>
  );
}

/* ------------------------------------------------------------------------ */

function Closing() {
  return (
    <section id="closing" className="relative overflow-hidden border-t border-rule street-grid">
      <div className="mx-auto max-w-5xl px-5 py-28 text-center md:py-36">
        <motion.h2 {...reveal} className="font-display text-5xl leading-[0.9] font-black tracking-wide text-snow uppercase md:text-8xl">
          Everything is public data <span className="text-ice">and open code.</span>
        </motion.h2>
        <motion.p {...reveal} className="mx-auto mt-8 max-w-2xl text-lg text-frost md:text-xl">
          Inspect the assumptions, rerun the queries, and disagree with the project using its own inputs.
        </motion.p>
        <div className="mt-10 flex flex-wrap justify-center gap-3">
          <a href="#/evidence" className="inline-flex min-h-12 items-center rounded-full bg-ice px-6 font-medium text-night transition hover:bg-snow">Explore all evidence</a>
          <ExternalLink href={GITHUB} className="inline-flex min-h-12 items-center gap-2 rounded-full border border-rule px-6 text-snow transition hover:border-ice">View the GitHub repository</ExternalLink>
        </div>
        <ul className="mx-auto mt-20 grid max-w-3xl gap-3 text-left text-[15px] text-frost md:grid-cols-2">
          {[
            'Scheduled position is not clearing time.',
            'A difference in order is not a fairness verdict.',
            'Request shares are not ward area or population.',
            'Rank displacement is not model improvement.',
          ].map(line => <li key={line} className="border-l border-sodium/60 pl-4">{line}</li>)}
        </ul>
      </div>
    </section>
  );
}

export function Story() {
  return (
    <main>
      <Hero />
      <div className="mx-auto max-w-7xl px-5 md:px-8">
        <TracingBeam className="xl:pl-16">
          <ActOne />
          <ActTwo />
          <ActThree />
          <ActFour />
          <ActFive />
        </TracingBeam>
      </div>
      <Closing />
    </main>
  );
}
