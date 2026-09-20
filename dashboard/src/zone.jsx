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
          plow operations, and how often the previous position repeated. Retrospective only — this
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
              frozen {lookup.provenance[part].frozen_at} · {lookup.provenance[part].certification}
            </dd>
          </div>
        ))}
        <div><dt className="text-frost">Source</dt><dd><ExternalLink href={GITHUB} className="text-ice hover:underline">repository</ExternalLink></dd></div>
      </dl>
    </main>
  );
}
