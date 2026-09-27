import {useMemo, useState} from 'react';
import {ChartState} from './charts';
import {useData} from './data';
import {cn, fmt} from './lib/utils';
import {ExternalLink} from './story';
import {GITHUB} from './lib/utils';

/*
 * The one page on this site a reader drives. Everything else reports a
 * finding; here someone picks their own plow zone and gets two answers.
 *
 * Every number shown is folded in `scripts/presentation/zone_lookup.py` at
 * build time and arrives in `data/lookup.json`. This file chooses which
 * pre-computed record to display and does no arithmetic of its own — in
 * particular it never averages the per-zone hit rates, which is the fold
 * gold-sql.md R3 forbids and which a reader could not catch by eye.
 *
 * The line this page must not cross: the City already publishes Know Your
 * Zone and a near-real-time clearing map, which answer current state for one
 * address. This page answers neither. It reports where a zone has sat across
 * 19 completed operations, and how often the previous position repeated. The
 * wording has to keep saying so, or a retrospective page gets read as a status
 * board by the first person who opens it in a snowstorm.
 */

const SHIFTS = [1, 2, 3, 4, 5];
const CITY_TOOL = 'https://www.winnipeg.ca/public-works/streets-transportation/snow-clearing-ice-control';

const pct = value => (value === null || value === undefined ? '—' : `${value}%`);
const num = (value, digits = 2) =>
  value === null || value === undefined ? '—' : Number(value).toFixed(digits);

// The numbers on this page do not refresh themselves: they are frozen at build
// time and stay put until someone re-runs the export. So the page has to say
// when that was, near the answers rather than in a footnote — a stale figure and
// a fresh one look identical, and only the timestamp separates them.
function Freshness({freshness}) {
  if (!freshness) return null;
  const clean = freshness.certification === 'certified' && freshness.same_freeze;
  const stamp = freshness.frozen_at ? String(freshness.frozen_at).slice(0, 10) : 'an unrecorded date';
  return (
    <p className={cn('mt-4 font-mono text-xs', clean ? 'text-frost' : 'text-amber-300')}>
      Frozen {stamp} from {freshness.certification === 'certified'
        ? 'a certified build'
        : <>a build whose certification is <b>{freshness.certification}</b></>}.
      {!freshness.same_freeze && ' The two queries were frozen by different runs, so they may not describe the same Gold build.'}
      {' '}These numbers change only when the export is re-run.
    </p>
  );
}

function Stat({label, value, note}) {
  return (
    <div>
      <div className="font-mono text-[11px] tracking-widest text-frost uppercase">{label}</div>
      <div className="mt-1 font-display text-4xl leading-none font-black text-snow tabular">{value}</div>
      {note && <div className="mt-1 text-[13px] text-frost">{note}</div>}
    </div>
  );
}

function Distribution({zone}) {
  // The fold hands over one `distribution` array, already aligned with SHIFTS.
  // Reading `shift_N_count` off the record instead drew five empty bars without
  // raising — a chart of zeroes is a readable chart, so nothing looked wrong.
  const counts = SHIFTS.map((_, index) => zone.distribution?.[index] ?? 0);
  const peak = Math.max(...counts, 1);
  return (
    <div>
      <div className="font-mono text-[11px] tracking-widest text-frost uppercase">
        Which shift it fell in, across {zone.operations} operations
      </div>
      <div className="mt-3 flex items-end gap-3" role="img"
        aria-label={SHIFTS.map((s, i) => `shift ${s}: ${counts[i]} operations`).join(', ')}>
        {SHIFTS.map((shift, index) => (
          <div key={shift} className="flex-1">
            <div className="flex h-28 items-end">
              <div
                className={cn('w-full rounded-t', counts[index] ? 'bg-ice' : 'bg-rule')}
                style={{height: `${Math.max((counts[index] / peak) * 100, counts[index] ? 6 : 2)}%`}}
              />
            </div>
            <div className="mt-2 text-center font-mono text-xs text-frost">
              <div className="text-snow tabular">{counts[index]}</div>
              <div>shift {shift}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Unscheduled({zone, city}) {
  // 🔴 An answer, not an empty card. These three zones hold 6.02% of the city's
  // addresses; a lookup that returned nothing for them would read as a bug to
  // the one reader who most needs to be told the data does not cover them.
  return (
    <div className="rounded-2xl border border-sodium/50 bg-sodium/[0.06] p-6">
      <h3 className="font-display text-2xl font-black tracking-wide text-snow">
        Zone {zone.plow_zone} has no residential plowing schedule
      </h3>
      <p className="mt-3 max-w-2xl text-frost">
        It is one of {city.no_schedule_zones.length} zones ({city.no_schedule_zones.join(' · ')}) that carry no
        residential shift assignment in the City's published schedule, together about{' '}
        {pct(city.no_schedule_address_pct)} of Winnipeg addresses. Because there is no assigned
        position, this zone appears in none of the {city.zones_scheduled}-zone order, and neither
        question below has an answer for it. That is a property of the published schedule, not a
        gap in this data.
      </p>
      <p className="mt-3 max-w-2xl text-frost">
        {fmt(zone.address_count)} addresses sit in this zone.
      </p>
    </div>
  );
}

function Scheduled({zone, city}) {
  const rotation =
    zone.mean_early === null || zone.mean_late === null
      ? null
      : Number((zone.mean_late - zone.mean_early).toFixed(2));

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="rounded-2xl border border-rule bg-deep/70 p-6">
        <h3 className="font-display text-2xl font-black tracking-wide text-snow">
          Why is my street cleared later than my friend's?
        </h3>
        <p className="mt-3 text-frost">
          Across the {zone.operations} completed operations, zone {zone.plow_zone} was scheduled on
          average in shift <b className="text-snow tabular">{num(zone.mean_shift)}</b>, ranking{' '}
          <b className="text-snow tabular">{zone.position}</b> of {city.zones_scheduled} zones —
          earliest first. Its assigned shift ranged from {zone.min_shift} to {zone.max_shift}.
        </p>
        <div className="mt-6 grid grid-cols-3 gap-4">
          <Stat label="Mean shift" value={num(zone.mean_shift)} />
          <Stat label="Position" value={`${zone.position}/${city.zones_scheduled}`} />
          <Stat label="Last time" value={zone.last_shift ?? '—'} note="shift number" />
        </div>
        <div className="mt-6"><Distribution zone={zone} /></div>
        <p className="mt-6 text-[15px] text-frost">
          Between the earlier half of those operations and the later half, its mean position
          moved from{' '}
          <b className="text-snow tabular">{num(zone.mean_early)}</b> to{' '}
          <b className="text-snow tabular">{num(zone.mean_late)}</b>
          {rotation === null ? '.' : rotation === 0
            ? ' — no net movement.'
            : ` — ${Math.abs(rotation)} of a shift ${rotation > 0 ? 'later' : 'earlier'}.`}
        </p>
      </section>

      <section className="rounded-2xl border border-rule bg-deep/70 p-6">
        <h3 className="font-display text-2xl font-black tracking-wide text-snow">
          Will my zone be earlier next time?
        </h3>
        <p className="mt-3 text-frost">
          This measures one rule only: guess that the next operation repeats the last one. Over the
          {' '}{zone.transitions} consecutive pairs recorded for this zone it landed on the exact
          shift <b className="text-snow tabular">{pct(zone.exact_pct)}</b> of the time, and within
          one shift <b className="text-snow tabular">{pct(zone.within1_pct)}</b>.
        </p>
        <div className="mt-6 grid grid-cols-3 gap-4">
          <Stat label="Exact" value={pct(zone.exact_pct)} />
          <Stat label="Within 1" value={pct(zone.within1_pct)} />
          <Stat label="Pairs" value={zone.transitions} note="consecutive operations" />
        </div>
        <aside className="mt-6 rounded-xl border-l-2 border-sodium bg-sodium/[0.06] px-5 py-4 text-[15px] text-snow/90">
          <div className="mb-1 font-mono text-[11px] tracking-widest text-sodium uppercase">
            Read it against the control
          </div>
          Across the whole city the same rule is right {pct(city.exact_pct)} of the time exactly and{' '}
          {pct(city.within1_pct)} within one shift, over {fmt(city.transitions)} pairs. Ignoring the
          last operation and always guessing shift {city.best_fixed_shift} lands within one shift{' '}
          {pct(city.best_fixed_within1_pct)} of the time. A hit rate only means something next to
          that number.
        </aside>
      </section>
    </div>
  );
}

// ── A past snowfall: the plan next to reported demand (ADR 0015) ─────────────
//
// Read-only over `lookup.demand_plan`, folded in scripts/presentation/demand_plan.py.
// The two columns are never combined into a score. The model's estimate is not
// in the data at all until its range exists (dashboard spec §10.2 ②): the fold
// ships `estimate: null` with `estimate_status`, so this component cannot show a
// bare point value even by mistake. Review prompts arrive with the range, and a
// rule that flags nothing says so with its stability ceiling (PromptNote).

const FIT_ROLE_NOTE = {
  holdout: 'Backtest: the model was trained without this season, so its estimate did not see the answer.',
  in_sample: 'In-sample: the model was fitted on seasons that include this snowfall, so its estimate has seen the answer. Useful for reading, not as evidence of accuracy.',
};

// A frozen timestamp to the minute: the full ISO string wraps on a phone.
function minute(stamp) {
  if (!stamp) return 'an unrecorded time';
  const text = String(stamp);
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)
    ? `${text.slice(0, 10)} ${text.slice(11, 16)} UTC`
    : text;
}

const RULE_TEXT = (rule) =>
  `among the top ${rule.top_k} by estimated requests per 1,000 addresses, planned for shift `
  + `${rule.minimum_shift} or later, and holding that top-${rule.top_k} place in at least `
  + `${Math.round(rule.minimum_stability * 100)}% of replays of history`;

function PromptNote({section, snowfall}) {
  const rule = section.uncertainty?.review_prompt;
  if (!rule) {
    return 'A review prompt will mark a zone whose estimated demand per address is among the '
      + 'highest for the snowfall while its planned shift is late, and only if that holds across '
      + 'most replays of history. It is a reason to look, not a finding that the plan was wrong.';
  }
  const base = `A review prompt marks a zone ${RULE_TEXT(rule)}. The rule was registered before `
    + 'any result was seen. It is a reason to look, not a finding that the plan was wrong. ';
  if (snowfall.fit_role !== 'holdout') {
    return base + 'Prompts are never computed for in-sample snowfalls: the model has seen their answer.';
  }
  if (!snowfall.has_plan) {
    return base + 'This snowfall had no published operation, so there is no plan to prompt on.';
  }
  if (snowfall.prompt_count > 0) {
    return base + `${snowfall.prompt_count} zone${snowfall.prompt_count === 1 ? '' : 's'} qualify for this snowfall.`;
  }
  return base + 'No zone qualifies for this snowfall. The most stable zone held its top-'
    + `${rule.top_k} place in ${Math.round(snowfall.max_rank_stability * 100)}% of replays, `
    + 'below the registered threshold, so the model cannot separate the zones reliably enough '
    + 'to point at any of them. The threshold is not lowered after seeing this.';
}

function eventLabel(event) {
  return `${event.start_date} · ${event.total_snowfall_cm} cm`
    + (event.fit_role === 'holdout' ? ' · holdout season' : '');
}

function Estimate({cell}) {
  if (cell.estimate) {
    return (
      <>
        <span className="tabular">
          {fmt(cell.estimate.point)} ({fmt(cell.estimate.low)}–{fmt(cell.estimate.high)})
        </span>
        <span className="mt-1 block font-mono text-[11px] font-normal text-frost">
          {Math.round(cell.estimate.level * 100)}% request range · top {cell.estimate.top_k} in{' '}
          {Math.round(cell.estimate.rank_stability * 100)}% of history replays
        </span>
      </>
    );
  }
  return <span className="text-frost">range pending</span>;
}

function DemandPlan({section, zone}) {
  const [picked, setPicked] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const events = section.events;
  const snowfall = events.find(e => e.snowfall_event_id === picked)
    ?? events.find(e => e.snowfall_event_id === section.default_event)
    ?? events[events.length - 1];
  const withPlan = events.filter(e => e.has_plan);
  const withoutPlan = events.filter(e => !e.has_plan);
  const cells = section.zones[zone.plow_zone]?.cells;
  const cell = cells?.[snowfall.snowfall_event_id];
  const zoneIds = Object.keys(section.zones).sort();

  return (
    <section className="mt-10 rounded-2xl border border-rule bg-deep/70 p-6">
      <div className="font-mono text-[11px] tracking-widest text-ice uppercase">One past snowfall</div>
      <h2 className="mt-2 font-display text-3xl font-black tracking-wide text-snow">
        The published plan next to reported demand
      </h2>
      <p className="mt-3 max-w-3xl text-frost">
        Pick a past snowfall. For zone {zone.plow_zone} this shows where the City's published
        schedule placed it, what the demand model estimates residents would report, and what they
        did report. The plan and the estimate sit side by side and are never combined into a
        score. Retrospective only — none of this describes a snowfall happening now.
      </p>

      <label className="mt-6 block max-w-xl">
        <span className="font-mono text-[11px] tracking-widest text-frost uppercase">Snowfall event</span>
        <select
          value={snowfall.snowfall_event_id}
          onChange={e => setPicked(e.target.value)}
          className="mt-2 block min-h-11 w-full rounded-xl border border-rule bg-deep px-4 font-mono text-sm text-snow"
        >
          <optgroup label={`With a published city-wide operation (${withPlan.length})`}>
            {withPlan.map(e => <option key={e.snowfall_event_id} value={e.snowfall_event_id}>{eventLabel(e)}</option>)}
          </optgroup>
          <optgroup label={`No published operation (${withoutPlan.length})`}>
            {withoutPlan.map(e => <option key={e.snowfall_event_id} value={e.snowfall_event_id}>{eventLabel(e)}</option>)}
          </optgroup>
        </select>
      </label>

      {!cell ? (
        <p className="mt-6 max-w-3xl rounded-xl border border-sodium/50 bg-sodium/[0.06] p-5 text-frost">
          Zone {zone.plow_zone} has no published residential shifts, so there is no plan to show and
          the demand model, trained on the {zoneIds.length} scheduled zones, gives it no estimate.
        </p>
      ) : (
        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          <div className="rounded-xl border border-rule p-5">
            <div className="font-mono text-[11px] tracking-widest text-frost uppercase">Published plan</div>
            {snowfall.has_plan ? (
              <>
                <div className="mt-2 font-display text-4xl font-black text-snow tabular">
                  Shift {cell.shift_number} <span className="text-2xl text-frost">of {SHIFTS.length}</span>
                </div>
                <p className="mt-2 text-[14px] text-frost">
                  Operation began {snowfall.operation_start}. A planned position, not a record of when
                  any street was cleared.
                </p>
              </>
            ) : (
              <p className="mt-2 text-[14px] text-frost">
                No city-wide residential operation was published for this snowfall, so there is no
                plan to compare against. That does not mean no plowing happened.
              </p>
            )}
          </div>
          <div className="rounded-xl border border-rule p-5">
            <div className="font-mono text-[11px] tracking-widest text-frost uppercase">Estimated requests</div>
            <div className="mt-2 font-display text-2xl font-black text-snow"><Estimate cell={cell} /></div>
            <p className="mt-2 text-[14px] text-frost">
              {cell.estimate
                ? `${fmt(cell.rate_per_1000)} estimated requests per 1,000 addresses. ${FIT_ROLE_NOTE[snowfall.fit_role]}`
                : 'The estimate is shown only with its range, and the range is still being computed.'}
            </p>
          </div>
          <div className="rounded-xl border border-rule p-5">
            <div className="font-mono text-[11px] tracking-widest text-frost uppercase">Reported afterwards</div>
            <div className="mt-2 font-display text-4xl font-black text-snow tabular">{fmt(cell.actual_count)}</div>
            <p className="mt-2 text-[14px] text-frost">
              Winter requests residents filed in this zone during the snowfall window. Reporting
              habits shape this count as much as road conditions do.
            </p>
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowAll(v => !v)}
        className="mt-6 font-mono text-xs text-ice hover:underline"
      >
        {showAll ? 'Hide' : 'Show'} all {zoneIds.length} zones for this snowfall
      </button>
      {showAll && (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full font-mono text-xs">
            <thead className="text-frost">
              <tr>
                <th className="py-2 pr-6 text-left">Zone</th>
                <th className="py-2 pr-6 text-left">Planned shift</th>
                <th className="py-2 pr-6 text-left">Estimated requests</th>
                <th className="py-2 pr-6 text-left">Per 1,000 addresses</th>
                <th className="py-2 pr-6 text-left">Reported</th>
                <th className="py-2 text-left">Review prompt</th>
              </tr>
            </thead>
            <tbody className="text-snow">
              {zoneIds.map(id => {
                const row = section.zones[id].cells[snowfall.snowfall_event_id];
                return (
                  <tr key={id} className={cn('border-t border-rule', id === zone.plow_zone && 'bg-ice/10')}>
                    <td className="py-2 pr-6">{id}</td>
                    <td className="py-2 pr-6 tabular">{row.shift_number ?? '—'}</td>
                    <td className="py-2 pr-6"><Estimate cell={row} /></td>
                    <td className="py-2 pr-6 tabular">{row.rate_per_1000 == null ? '—' : fmt(row.rate_per_1000)}</td>
                    <td className="py-2 pr-6 tabular">{fmt(row.actual_count)}</td>
                    <td className="py-2">{row.prompt ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-3 max-w-3xl text-[13px] text-frost">
            <PromptNote section={section} snowfall={snowfall} />
          </p>
        </div>
      )}

      <p className="mt-6 max-w-3xl text-[13px] text-frost">{FIT_ROLE_NOTE[snowfall.fit_role]}</p>
      <dl className="mt-4 grid max-w-3xl gap-x-8 gap-y-2 font-mono text-xs sm:grid-cols-2">
        <div><dt className="text-frost">Query</dt><dd><a href="#/evidence/FIG-BO8-03" className="text-ice hover:underline">FIG-BO8-03</a> · frozen {minute(section.frozen_at)} · {section.certification}</dd></div>
        <div><dt className="text-frost">Model version</dt><dd className="text-snow">{section.model_version}</dd></div>
        <div><dt className="text-frost">Event rule</dt><dd className="text-snow">{section.event_rule_version}</dd></div>
        <div><dt className="text-frost">Data collected</dt><dd className="text-snow">{section.data_date} · addresses as of {section.address_count_snapshot_date}</dd></div>
        {section.uncertainty && <div><dt className="text-frost">Uncertainty</dt><dd className="text-snow">{section.uncertainty.replicate_count} event-cluster replays · holdout coverage {Math.round(section.uncertainty.coverage.rate * 100)}%</dd></div>}
      </dl>
    </section>
  );
}

export function Zone() {
  const {lookup, status} = useData();
  const [selected, setSelected] = useState(null);
  const zones = lookup?.zones ?? [];
  const zone = useMemo(
    () => zones.find(z => z.plow_zone === selected) ?? zones[0],
    [zones, selected],
  );

  if (!lookup || !zone) {
    return (
      <main className="mx-auto max-w-7xl px-5 py-12 md:px-8">
        <div className="font-mono text-xs tracking-[0.2em] text-ice uppercase">Look up a plow zone</div>
        <div className="mt-6 max-w-3xl"><ChartState status={status} label="zone lookup" /></div>
      </main>
    );
  }
  const city = lookup.city;

  return (
    <main className="mx-auto max-w-7xl px-5 py-12 md:px-8">
      <header className="max-w-3xl">
        <div className="font-mono text-xs tracking-[0.2em] text-ice uppercase">Look up a plow zone</div>
        <h1 className="mt-3 font-display text-4xl leading-none font-black tracking-wide text-snow md:text-5xl">
          Where your zone sits in the order
        </h1>
        <p className="mt-5 text-lg text-frost">
          Pick a zone to see where it has been placed across {city.operations_total} completed
          plow operations with a published schedule, and how often the previous position repeated. Retrospective only — this
          says nothing about the operation happening right now.
        </p>
        <Freshness freshness={lookup.freshness} />
      </header>

      <div className="mt-8 flex flex-wrap items-end gap-4">
        <label className="block">
          <span className="font-mono text-[11px] tracking-widest text-frost uppercase">Plow zone</span>
          <select
            value={zone.plow_zone}
            onChange={event => setSelected(event.target.value)}
            className="mt-2 block min-h-11 rounded-xl border border-rule bg-deep px-4 font-mono text-sm text-snow"
          >
            {zones.map(option => (
              <option key={option.plow_zone} value={option.plow_zone}>
                {option.plow_zone}
                {option.has_plow_schedule ? ` — mean shift ${num(option.mean_shift)}` : ' — no schedule'}
              </option>
            ))}
          </select>
        </label>
        <p className="font-mono text-[11px] text-frost">
          {city.zones_total} zones · {city.zones_scheduled} with a residential schedule
        </p>
      </div>

      <div className="mt-8">
        {zone.has_plow_schedule
          ? <Scheduled zone={zone} city={city} />
          : <Unscheduled zone={zone} city={city} />}
      </div>

      {lookup.demand_plan && <DemandPlan section={lookup.demand_plan} zone={zone} />}

      <section className="mt-10 max-w-3xl rounded-2xl border border-sodium/50 bg-sodium/[0.06] p-6">
        <h2 className="font-mono text-[11px] tracking-widest text-sodium uppercase">
          What this page is not
        </h2>
        <ul className="mt-3 space-y-2 text-[15px] text-snow/90">
          <li>
            <b>It does not tell you whether your street has been cleared.</b> For current status,
            the City publishes its own zone lookup and a near-real-time clearing map:{' '}
            <ExternalLink href={CITY_TOOL} className="text-ice hover:underline">
              winnipeg.ca snow clearing
            </ExternalLink>.
          </li>
          <li>
            <b>The unit is the zone, not the street.</b> There are {city.zones_total} of them. Every
            street inside one shares its scheduled position.
          </li>
          <li>
            <b>Residential streets only.</b> Priority routes are cleared on a different schedule
            that this data does not describe.
          </li>
          <li>
            <b>A schedule is a plan, not a record of work done.</b> The source is the published
            shift assignment; it carries no completion time.
          </li>
          <li>
            <b>Later is not unfair.</b> Some zone is always last. This page measures order, and says
            nothing about why an order was chosen.
          </li>
        </ul>
      </section>

      <dl className="mt-8 grid max-w-3xl gap-x-8 gap-y-3 font-mono text-xs sm:grid-cols-2">
        <div>
          <dt className="text-frost">Queries</dt>
          <dd>
            <a href="#/evidence/FIG-BO2-06" className="text-ice hover:underline">FIG-BO2-06</a>
            {' · '}
            <a href="#/evidence/FIG-BO2-07" className="text-ice hover:underline">FIG-BO2-07</a>
          </dd>
        </div>
        {/* Both exports, never just the first: they are frozen by separate runs,
            and showing one status would hide a disagreement between them. */}
        {['profile', 'transition'].map(part => (
          <div key={part}>
            <dt className="text-frost">{part === 'profile' ? 'FIG-BO2-06' : 'FIG-BO2-07'}</dt>
            <dd className="text-snow">
              frozen {minute(lookup.provenance[part].frozen_at)} · {lookup.provenance[part].certification}
            </dd>
          </div>
        ))}
        <div><dt className="text-frost">Source</dt><dd><ExternalLink href={GITHUB} className="text-ice hover:underline">repository</ExternalLink></dd></div>
      </dl>
    </main>
  );
}
