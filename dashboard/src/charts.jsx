import {motion} from 'motion/react';
import {useData, useFigure} from './data';
import {cn, fmt} from './lib/utils';

const ICE = '#8fd6ff';
const GLACIER = '#3f8fc4';
const FROST = '#8d9db3';
const RULE = '#1f3149';
const SNOW = '#edf2f7';
const SODIUM = '#f4a940';
const NIGHT = '#07101c';
const DEEP_ZONE = '#15324d';

const inView = {once: true, margin: '-10% 0px'};

function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map(h => parseInt(h, 16));
  const pb = b.match(/\w\w/g).map(h => parseInt(h, 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(',')})`;
}

const titleCase = text => text.replace(/\b\w/g, c => c.toUpperCase());

export function ChartState({status, label = 'chart'}) {
  if (status === 'error' || status === 'ready') {
    return (
      <div role="status" className="rounded-xl border border-sodium/40 p-5 text-[15px] text-frost">
        The data for this {label} did not load. The numbers in the text come from the same frozen export — open the query on GitHub to check them.
      </div>
    );
  }
  return <div role="status" className="h-48 animate-pulse rounded-xl bg-rule/40"><span className="sr-only">Loading frozen data</span></div>;
}

function Svg({width, height, label, children, className}) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className={cn('block h-auto w-full', className)} style={{fontFamily: 'var(--font-mono)'}}>
      {children}
    </svg>
  );
}

/* ------------------------------------------------------------------------ */
/* Plow-zone map. Zones without a residential schedule are outlines only.     */
/* ------------------------------------------------------------------------ */

export function ZoneMap({mode = 'shift', animate = true, className, active = null, onActivate}) {
  const {zones, status} = useData();
  if (!zones) return <ChartState status={status} label="map" />;
  const interactive = Boolean(onActivate);
  const shifts = zones.zones.filter(z => z.mean_shift != null).map(z => z.mean_shift);
  const lo = Math.min(...shifts);
  const hi = Math.max(...shifts);
  const label = mode === 'shift'
    ? `Map of Winnipeg's 25 plow zones, shaded by average scheduled shift from ${lo} to ${hi}. Three zones without a residential schedule are outlined only.`
    : "Map of Winnipeg's 25 plow zones with zone V highlighted: it is made of several separate pieces spread across the city.";

  return (
    <Svg width={zones.width} height={zones.height} label={label} className={className}>
      {zones.zones.map(zone => {
        const t = zone.mean_shift == null ? null : (zone.mean_shift - lo) / (hi - lo);
        const isV = zone.zone === 'V';
        let fill = 'transparent';
        if (mode === 'shift' && t != null) fill = mix('8fd6ff', '15324d', t);
        if (mode === 'v' && zone.scheduled) fill = isV ? ICE : '#0f2236';
        const isActive = active === zone.zone;
        if (isActive) fill = zone.scheduled ? ICE : 'rgba(141,157,179,0.25)';
        const delay = mode === 'shift' && t != null ? 0.5 + t * 2.4 : 0;
        const describe = zone.scheduled
          ? `Zone ${zone.zone}: average scheduled shift ${zone.mean_shift}, ${zone.parts} separate pieces`
          : `Zone ${zone.zone}: no residential schedule data, ${zone.parts} separate pieces`;
        return (
          <motion.path
            key={zone.zone}
            d={zone.d}
            fill={fill}
            fillRule="evenodd"
            stroke={isActive ? SNOW : zone.scheduled ? NIGHT : FROST}
            strokeWidth={isActive ? 3 : zone.scheduled ? 2 : 1.6}
            strokeDasharray={zone.scheduled || isActive ? undefined : '7 6'}
            initial={animate ? {opacity: zone.scheduled ? 0 : 0.2} : false}
            animate={{opacity: active && !isActive ? 0.28 : 1}}
            transition={active || !animate ? {duration: 0.2} : {delay, duration: 0.7}}
            {...(interactive && {
              // Every piece of a zone lives in one path, so hovering any piece lights all of them.
              pointerEvents: 'visiblePainted',
              style: {cursor: 'pointer', outline: 'none'},
              tabIndex: 0,
              role: 'button',
              'aria-label': describe,
              'aria-pressed': isActive,
              onPointerEnter: () => onActivate(zone.zone),
              onFocus: () => onActivate(zone.zone),
              onClick: () => onActivate(zone.zone),
            })}
          />
        );
      })}
      {/* Keep the active zone's outline on top: later paths would otherwise paint over its border. */}
      {interactive && active && (() => {
        const zone = zones.zones.find(z => z.zone === active);
        return zone && <path d={zone.d} fill="none" fillRule="evenodd" stroke={SNOW} strokeWidth="3" pointerEvents="none" />;
      })()}
      {zones.zones.map(zone => {
        if (mode === 'v' && zone.zone !== 'V') return null;
        const t = zone.mean_shift == null ? 1 : (zone.mean_shift - lo) / (hi - lo);
        const [x, y] = zone.anchor;
        const isActive = active === zone.zone;
        let fill = mode === 'v' ? NIGHT : zone.scheduled ? (t < 0.45 ? NIGHT : SNOW) : FROST;
        if (isActive) fill = zone.scheduled ? NIGHT : SNOW;
        return (
          <text key={zone.zone} x={x} y={y} textAnchor="middle" dominantBaseline="central"
            fontSize={zone.zone.length > 2 ? 15 : 22} fontWeight="500" pointerEvents="none"
            fill={fill} opacity={active && !isActive ? 0.35 : 1}>
            {zone.zone}
          </text>
        );
      })}
    </Svg>
  );
}

export function ShiftLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 font-mono text-xs text-frost">
      <span className="flex items-center gap-2">
        <span className="h-2.5 w-28 rounded-full" style={{background: `linear-gradient(90deg, ${ICE}, ${DEEP_ZONE})`}} />
        earlier → later average scheduled shift
      </span>
      <span className="flex items-center gap-2">
        <span className="h-3 w-5 rounded-sm border border-dashed border-frost" /> no residential schedule data
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO2-03 — 19 operations × 22 zones, every cell present.                */
/* ------------------------------------------------------------------------ */

export function OperationGrid() {
  const {rows, status} = useFigure('FIG-BO2-03');
  if (!rows) return <ChartState status={status} label="panel" />;
  const events = [...new Set(rows.map(r => r.first_shift_date))].sort();
  const zones = [...new Set(rows.map(r => r.plow_zone))].sort();
  const cell = 16;
  const left = 22;
  const top = 8;
  const width = left + events.length * cell;
  const height = top + zones.length * cell + 26;
  const byKey = new Map(rows.map(r => [`${r.first_shift_date}|${r.plow_zone}`, r]));
  return (
    <Svg width={width} height={height} label={`${events.length} plow operations by ${zones.length} zones: ${rows.length} scheduled cells, none missing.`}>
      {zones.map((zone, zi) => (
        <text key={zone} x={left - 6} y={top + zi * cell + cell / 2} fontSize="9" fill={FROST} textAnchor="end" dominantBaseline="central">{zone}</text>
      ))}
      {events.map((date, ei) => zones.map((zone, zi) => {
        const row = byKey.get(`${date}|${zone}`);
        return (
          <motion.rect key={`${date}${zone}`} x={left + ei * cell + 1.5} y={top + zi * cell + 1.5} width={cell - 3} height={cell - 3} rx="2"
            fill={row ? (row.shift_number === 1 ? ICE : GLACIER) : 'transparent'} stroke={row ? 'none' : SODIUM}
            fillOpacity={row && !row.is_aligned ? 0.45 : 1}
            initial={{opacity: 0}} whileInView={{opacity: 1}} viewport={inView} transition={{delay: ei * 0.04}} />
        );
      }))}
      <text x={left} y={height - 8} fontSize="9" fill={FROST}>{events[0].slice(0, 4)}</text>
      <text x={width} y={height - 8} fontSize="9" fill={FROST} textAnchor="end">{events.at(-1).slice(0, 4)}</text>
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO2-01 — average scheduled shift with fastest / slowest whiskers.     */
/* ------------------------------------------------------------------------ */

export function ShiftLadder({highlight = ['S', 'C']}) {
  const {rows, status} = useFigure('FIG-BO2-01');
  if (!rows) return <ChartState status={status} />;
  const sorted = [...rows].sort((a, b) => a.mean_shift - b.mean_shift);
  const W = 640, left = 34, right = 96, top = 52, rowH = 23;
  const H = top + sorted.length * rowH + 8;
  const x = s => left + ((s - 1) / 4) * (W - left - right);
  return (
    <Svg width={W} height={H} label="Average scheduled shift for 22 plow zones, from zone S at 1.26 to zone C at 3.47, with each zone's earliest and latest shift.">
      {[1, 2, 3, 4, 5].map(s => (
        <g key={s}>
          <line x1={x(s)} x2={x(s)} y1={top - 8} y2={H - 4} stroke={RULE} />
          <text x={x(s)} y={16} fontSize="11" fill={SNOW} textAnchor="middle">Shift {s}</text>
          <text x={x(s)} y={32} fontSize="10" fill={FROST} textAnchor="middle">+{(s - 1) * 12} h</text>
        </g>
      ))}
      <text x={W - right + 12} y={32} fontSize="10" fill={FROST}>mean · offset</text>
      {sorted.map((row, i) => {
        const y = top + i * rowH + rowH / 2;
        const on = highlight.includes(row.plow_zone);
        return (
          <g key={row.plow_zone}>
            {on && <rect x={0} y={y - rowH / 2 + 1} width={W} height={rowH - 2} rx="4" fill={ICE} fillOpacity="0.08" />}
            <text x={left - 12} y={y} fontSize="12" fill={on ? SNOW : FROST} textAnchor="end" dominantBaseline="central">{row.plow_zone}</text>
            <line x1={x(row.min_shift)} x2={x(row.max_shift)} y1={y} y2={y} stroke={on ? GLACIER : '#2c4462'} strokeWidth="2" strokeLinecap="round" />
            <motion.circle cy={y} r={on ? 6 : 4.5} fill={on ? ICE : GLACIER}
              initial={{cx: x(1)}} whileInView={{cx: x(row.mean_shift)}} viewport={inView} transition={{duration: 0.9, delay: i * 0.03}} />
            <text x={W - right + 12} y={y} fontSize="11" fill={on ? SNOW : FROST} dominantBaseline="central" className="tabular">
              {row.mean_shift.toFixed(2)} · +{Math.round(row.mean_wait_hours)} h
            </text>
          </g>
        );
      })}
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO2-02 — first 9 vs last 10 operations.                               */
/* ------------------------------------------------------------------------ */

export function DriftSlope({highlight = ['V', 'M']}) {
  const {rows, status} = useFigure('FIG-BO2-02');
  if (!rows) return <ChartState status={status} />;
  const W = 520, H = 400, top = 56, bottom = 24, x1 = 150, x2 = 370;
  const y = s => top + ((s - 1) / 2.6) * (H - top - bottom);
  const ordered = [...rows].sort((a, b) => highlight.includes(a.plow_zone) - highlight.includes(b.plow_zone));
  return (
    <Svg width={W} height={H} label="Average scheduled shift in the first 9 and last 10 operations. Zone V moved 1.31 shifts later and zone M 1.02 shifts later.">
      <text x={x1} y={20} fontSize="11" fill={SNOW} textAnchor="middle">First 9 operations</text>
      <text x={x2} y={20} fontSize="11" fill={SNOW} textAnchor="middle">Last 10 operations</text>
      {[1, 2, 3].map(s => (
        <g key={s}>
          <line x1={60} x2={W - 60} y1={y(s)} y2={y(s)} stroke={RULE} strokeDasharray="2 4" />
          <text x={52} y={y(s)} fontSize="10" fill={FROST} textAnchor="end" dominantBaseline="central">shift {s}</text>
        </g>
      ))}
      {ordered.map(row => {
        const on = highlight.includes(row.plow_zone);
        return (
          <g key={row.plow_zone}>
            <motion.line x1={x1} y1={y(row.mean_early)} y2={y(row.mean_late)} stroke={on ? ICE : '#2c4462'} strokeWidth={on ? 3 : 1.4}
              initial={{x2: x1}} whileInView={{x2}} viewport={inView} transition={{duration: 0.8}} />
            <circle cx={x1} cy={y(row.mean_early)} r={on ? 5 : 3} fill={on ? ICE : '#2c4462'} />
            <circle cx={x2} cy={y(row.mean_late)} r={on ? 5 : 3} fill={on ? ICE : '#2c4462'} />
            {on && (
              <>
                <text x={x1 - 12} y={y(row.mean_early)} fontSize="12" fill={SNOW} textAnchor="end" dominantBaseline="central">{row.plow_zone} {row.mean_early.toFixed(2)}</text>
                <text x={x2 + 12} y={y(row.mean_late)} fontSize="12" fill={SNOW} dominantBaseline="central">{row.plow_zone} {row.mean_late.toFixed(2)} (+{row.drift.toFixed(2)})</text>
              </>
            )}
          </g>
        );
      })}
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO2-04 — addresses vs average shift.                                  */
/* ------------------------------------------------------------------------ */

export function AddressScatter() {
  const {rows, status} = useFigure('FIG-BO2-04');
  if (!rows) return <ChartState status={status} />;
  const W = 560, H = 340, left = 56, bottom = 44, top = 16, right = 20;
  const maxA = Math.max(...rows.map(r => r.address_count));
  const x = a => left + (a / maxA) * (W - left - right);
  const y = s => top + ((3.6 - s) / 2.6) * (H - top - bottom);
  return (
    <Svg width={W} height={H} label="Address count against average scheduled shift for 22 zones; zones with more addresses tend to be scheduled later.">
      {[1, 2, 3].map(s => <g key={s}><line x1={left} x2={W - right} y1={y(s)} y2={y(s)} stroke={RULE} /><text x={left - 8} y={y(s)} fontSize="10" fill={FROST} textAnchor="end" dominantBaseline="central">shift {s}</text></g>)}
      {[0, 10000, 20000].map(a => <text key={a} x={x(a)} y={H - 22} fontSize="10" fill={FROST} textAnchor="middle">{fmt(a)}</text>)}
      <text x={W - right} y={H - 4} fontSize="10" fill={FROST} textAnchor="end">addresses in zone</text>
      {rows.map(r => (
        <g key={r.plow_zone}>
          <circle cx={x(r.address_count)} cy={y(r.mean_shift_all)} r="5" fill={GLACIER} />
          <text x={x(r.address_count) + 8} y={y(r.mean_shift_all)} fontSize="10" fill={FROST} dominantBaseline="central">{r.plow_zone}</text>
        </g>
      ))}
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO4-01 / FIG-BO4-02 — zone × ward request shares.                     */
/* ------------------------------------------------------------------------ */

export function WardMatrix({highlight = 'V'}) {
  const matrix = useFigure('FIG-BO4-01');
  const dominant = useFigure('FIG-BO4-02');
  const zonesMap = useData().zones;
  if (!matrix.rows || !dominant.rows) return <ChartState status={matrix.status} />;
  const unscheduled = new Set((zonesMap?.zones ?? []).filter(z => !z.scheduled).map(z => z.zone));
  const zones = [...dominant.rows].sort((a, b) => a.dominant_share - b.dominant_share);
  const totals = {};
  matrix.rows.forEach(r => { totals[r.ward] = (totals[r.ward] ?? 0) + r.request_share; });
  const wards = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
  const cell = 26, left = 84, top = 170, right = 128;
  const W = left + wards.length * cell + right;
  const H = top + zones.length * cell + 10;
  const share = new Map(matrix.rows.map(r => [`${r.plow_zone}|${r.ward}`, r]));
  return (
    <Svg width={W} height={H} label="Matrix of 25 plow zones by 15 wards. Each cell is the share of a zone's winter service requests labelled with that ward. Only zones T and N fall entirely in one ward.">
      {wards.map((ward, wi) => (
        <text key={ward} transform={`translate(${left + wi * cell + cell / 2 + 4}, ${top - 10}) rotate(-58)`} fontSize="10.5" fill={FROST}>{titleCase(ward)}</text>
      ))}
      {zones.map((zone, zi) => {
        const yy = top + zi * cell;
        const on = zone.plow_zone === highlight;
        return (
          <g key={zone.plow_zone}>
            {on && <rect x={2} y={yy} width={W - 4} height={cell} rx="5" fill="none" stroke={ICE} strokeWidth="1.5" />}
            <text x={left - 10} y={yy + cell / 2} fontSize="11" fill={on ? SNOW : FROST} textAnchor="end" dominantBaseline="central">
              {zone.plow_zone}{unscheduled.has(zone.plow_zone) ? '*' : ''}
            </text>
            {wards.map((ward, wi) => {
              const r = share.get(`${zone.plow_zone}|${ward}`);
              return (
                <g key={ward}>
                  <rect x={left + wi * cell + 1.5} y={yy + 1.5} width={cell - 3} height={cell - 3} rx="3" fill={r ? ICE : RULE}
                    fillOpacity={r ? 0.12 + r.request_share * 0.88 : 0.25}>
                    {r && <title>{`Zone ${zone.plow_zone} · ${titleCase(ward)}: ${(r.request_share * 100).toFixed(1)}% of requests`}</title>}
                  </rect>
                  {r?.is_dominant && <circle cx={left + wi * cell + cell / 2} cy={yy + cell / 2} r="2.5" fill={NIGHT} />}
                </g>
              );
            })}
            <text x={left + wards.length * cell + 10} y={yy + cell / 2} fontSize="10.5" fill={on ? SNOW : FROST} dominantBaseline="central" className="tabular">
              {(zone.dominant_share * 100).toFixed(0)}% · {zone.wards_touched} {zone.wards_touched === 1 ? 'ward' : 'wards'}
            </text>
          </g>
        );
      })}
      <text x={left + wards.length * cell + 10} y={top - 12} fontSize="10" fill={FROST}>largest ward · spread</text>
    </Svg>
  );
}

export function DominantStrip() {
  const {rows, status} = useFigure('FIG-BO4-02');
  if (!rows) return <ChartState status={status} />;
  const sorted = [...rows].sort((a, b) => a.dominant_share - b.dominant_share);
  const median = sorted[Math.floor(sorted.length / 2)].dominant_share;
  const W = 560, H = 120, left = 16, right = 16;
  const x = v => left + v * (W - left - right);
  return (
    <Svg width={W} height={H} label={`Largest single-ward share of each zone's winter requests. Median ${(median * 100).toFixed(0)} percent.`}>
      <line x1={x(0)} x2={x(1)} y1={70} y2={70} stroke={RULE} />
      {[0, 0.5, 1].map(v => <text key={v} x={x(v)} y={100} fontSize="10" fill={FROST} textAnchor="middle">{v * 100}%</text>)}
      <line x1={x(median)} x2={x(median)} y1={14} y2={80} stroke={SNOW} strokeDasharray="3 3" />
      <text x={x(median) + 6} y={20} fontSize="11" fill={SNOW}>median {(median * 100).toFixed(0)}%</text>
      {sorted.map((r, i) => (
        <circle key={r.plow_zone} cx={x(r.dominant_share)} cy={70 - (i % 3) * 9} r="4.5" fill={r.dominant_share === 1 ? ICE : GLACIER}>
          <title>{`Zone ${r.plow_zone}: ${(r.dominant_share * 100).toFixed(1)}%`}</title>
        </circle>
      ))}
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* Generic horizontal bars.                                                   */
/* ------------------------------------------------------------------------ */

export function HBars({items, max, label, width = 560, unit = ''}) {
  const rowH = 36, left = 130, right = 150;
  const H = items.length * rowH + 6;
  const top = max ?? Math.max(...items.map(i => i.value));
  const x = v => ((v / top) * (width - left - right));
  return (
    <Svg width={width} height={H} label={label}>
      {items.map((item, i) => {
        const y = i * rowH + 6;
        return (
          <g key={item.label}>
            <text x={left - 12} y={y + 12} fontSize="12" fill={item.muted ? FROST : SNOW} textAnchor="end" dominantBaseline="central">{item.label}</text>
            <rect x={left} y={y + 2} width={width - left - right} height={20} rx="4" fill={RULE} fillOpacity="0.35" />
            <motion.rect x={left} y={y + 2} height={20} rx="4" fill={item.color ?? GLACIER}
              initial={{width: 0}} whileInView={{width: Math.max(item.value > 0 ? 2 : 0, x(item.value))}} viewport={inView} transition={{duration: 0.8, delay: i * 0.06}} />
            <text x={width - right + 12} y={y + 12} fontSize="12" fill={item.muted ? FROST : SNOW} dominantBaseline="central" className="tabular">{item.text ?? `${item.value}${unit}`}</text>
          </g>
        );
      })}
    </Svg>
  );
}

export function WinterCategories() {
  const {rows, status} = useFigure('FIG-BO1-04');
  if (!rows) return <ChartState status={status} />;
  const items = rows.map(r => ({
    label: r.winter_category.replace('_', ' '),
    value: Number(r.pct_of_all_requests),
    text: r.requests === 0 ? '0 · measured zero' : `${r.pct_of_all_requests}% · ${fmt(r.requests)}`,
    muted: r.requests === 0,
  }));
  return <HBars items={items} max={80} label="Winter requests during snowfall events by category. Snow accounts for 73.2 percent; windrow has a measured zero." />;
}

export function SeasonTotals() {
  const {rows, status} = useFigure('FIG-BO3-02');
  if (!rows) return <ChartState status={status} />;
  const items = rows.map(r => ({label: r.snow_season, value: r.season_snowfall_cm, text: `${r.season_snowfall_cm} cm · ${r.event_count} ev`}));
  return <HBars items={items} label="Snowfall per season across 18 winters; 2021–2022 is the heaviest at 106.2 cm." />;
}

export function AttributionRules() {
  const {rows, status} = useFigure('FIG-BO8-02');
  if (!rows) return <ChartState status={status} />;
  const items = rows.map(r => ({label: r.attribution_rule_id.replace('RULE-', '').replace('-', ' '), value: Number(r.pct_of_all_cells), text: `${r.pct_of_all_cells}% · ${r.cells}`}));
  return <HBars items={items} max={70} label="Recommendation attribution rules: 57.6 percent balanced." />;
}

/* ------------------------------------------------------------------------ */
/* FIG-BO3-00 — fourteen days that never crossed the daily threshold.        */
/* ------------------------------------------------------------------------ */

export function SnowWindow({kind = 'accumulation_only'}) {
  const {rows, status} = useFigure('FIG-BO3-00');
  if (!rows) return <ChartState status={status} />;
  const days = rows.filter(r => r.window_kind === kind && r.window_rank === 1).sort((a, b) => a.day_index - b.day_index);
  const W = 560, left = 44, right = 16, bar = (W - left - right) / days.length;
  const daily = v => 150 - (v / 4) * 120;
  const total = v => 330 - (v / 14) * 130;
  const showThreshold = kind === 'accumulation_only';
  return (
    <Svg width={W} height={360} label={`Daily snowfall over fourteen days from ${days[0].weather_date}. No single day reaches 3 cm; the running total reaches ${days.at(-1).running_total_cm.toFixed(1)} cm.`}>
      <text x={left} y={14} fontSize="11" fill={SNOW}>Daily snowfall (cm)</text>
      {[0, 2, 4].map(v => <g key={v}><line x1={left} x2={W - right} y1={daily(v)} y2={daily(v)} stroke={RULE} /><text x={left - 8} y={daily(v)} fontSize="10" fill={FROST} textAnchor="end" dominantBaseline="central">{v}</text></g>)}
      {showThreshold && (
        <g>
          <line x1={left} x2={W - right} y1={daily(3)} y2={daily(3)} stroke={SODIUM} strokeDasharray="6 4" />
          <text x={W - right} y={daily(3) - 7} fontSize="10" fill={SODIUM} textAnchor="end">single-day rule · 3 cm</text>
        </g>
      )}
      {days.map((d, i) => (
        <motion.rect key={d.weather_date} x={left + i * bar + 3} width={bar - 6} rx="2" fill={ICE}
          initial={{y: daily(0), height: 0}} whileInView={{y: daily(d.snowfall_cm), height: daily(0) - daily(d.snowfall_cm)}}
          viewport={inView} transition={{duration: 0.5, delay: i * 0.05}}>
          <title>{`${d.weather_date}: ${d.snowfall_cm} cm`}</title>
        </motion.rect>
      ))}
      <text x={left} y={196} fontSize="11" fill={SNOW}>Running total over the fourteen days (cm)</text>
      {[0, 7, 14].map(v => <g key={v}><line x1={left} x2={W - right} y1={total(v)} y2={total(v)} stroke={RULE} /><text x={left - 8} y={total(v)} fontSize="10" fill={FROST} textAnchor="end" dominantBaseline="central">{v}</text></g>)}
      <motion.polyline fill="none" stroke={ICE} strokeWidth="2.5"
        points={days.map((d, i) => `${left + i * bar + bar / 2},${total(d.running_total_cm)}`).join(' ')}
        initial={{pathLength: 0}} whileInView={{pathLength: 1}} viewport={inView} transition={{duration: 1.2}} />
      <text x={left} y={352} fontSize="10" fill={FROST}>{days[0].weather_date}</text>
      <text x={W - right} y={352} fontSize="10" fill={FROST} textAnchor="end">{days.at(-1).weather_date}</text>
    </Svg>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO3-01 — 99 snowfall events.                                          */
/* ------------------------------------------------------------------------ */

export function EventTimeline() {
  const {rows, status} = useFigure('FIG-BO3-01');
  if (!rows) return <ChartState status={status} />;
  const W = 640, H = 260, left = 40, right = 12, top = 16, bottom = 40;
  const t0 = Date.parse('2008-09-01'), t1 = Date.parse('2026-06-01');
  const maxY = Math.ceil(Math.max(...rows.map(r => r.total_snowfall_cm)) / 10) * 10;
  const x = d => left + ((Date.parse(d) - t0) / (t1 - t0)) * (W - left - right);
  const y = v => top + (1 - v / maxY) * (H - top - bottom);
  return (
    <div>
      <Svg width={W} height={H} label="Timeline of 99 snowfall events from 2008 to 2026 by total snowfall. Eight exist only because of the accumulation rule; eleven had no winter service requests.">
        {[0, maxY / 2, maxY].map(v => <g key={v}><line x1={left} x2={W - right} y1={y(v)} y2={y(v)} stroke={RULE} /><text x={left - 8} y={y(v)} fontSize="10" fill={FROST} textAnchor="end" dominantBaseline="central">{v}</text></g>)}
        {[2010, 2014, 2018, 2022, 2026].map(yr => <text key={yr} x={x(`${yr}-01-01`)} y={H - 16} fontSize="10" fill={FROST} textAnchor="middle">{yr}</text>)}
        <text x={left} y={H - 2} fontSize="10" fill={FROST}>total snowfall per event (cm)</text>
        {rows.map(r => (
          <circle key={r.snowfall_event_id} cx={x(r.start_date)} cy={y(r.total_snowfall_cm)} r={r.accum_flag ? 5 : 4}
            fill={r.has_no_winter_request ? 'transparent' : r.accum_flag ? SNOW : GLACIER}
            stroke={r.has_no_winter_request ? FROST : r.accum_flag ? SODIUM : 'none'} strokeWidth="1.5">
            <title>{`${r.snowfall_event_id}: ${r.total_snowfall_cm.toFixed(1)} cm over ${r.duration_days} days`}</title>
          </circle>
        ))}
      </Svg>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-frost">
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-full bg-glacier" />single day ≥ 3 cm</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-full border border-sodium bg-snow" />only the 10-day total ≥ 10 cm</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-full border border-frost" />no winter requests (thin early 311 coverage)</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* The %ICE% keyword filter.                                                  */
/* ------------------------------------------------------------------------ */

export function KeywordFilterBar() {
  const wrong = 1437362, all = 1439574;
  return (
    <div role="img" aria-label="Of 1,439,574 rows matched by a loose ICE keyword, 1,437,362, or 99.8 percent, were not winter requests.">
      <div className="flex h-12 overflow-hidden rounded-lg">
        <motion.div className="bg-sodium/80" initial={{width: '0%'}} whileInView={{width: `${(wrong / all) * 100}%`}} viewport={inView} transition={{duration: 1.1}} />
        <div className="flex-1 bg-ice" />
      </div>
      <div className="mt-3 flex justify-between font-mono text-xs text-frost">
        <span><b className="text-sodium">{fmt(wrong)}</b> rows matched by accident: Pol<b className="text-snow">ice</b>, Serv<b className="text-snow">ice</b>s, Not<b className="text-snow">ice</b>…</span>
        <span className="text-ice">{fmt(all - wrong)} real</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO6-02 — observed factor contributions vs nominal weights.            */
/* ------------------------------------------------------------------------ */

export function FactorSpread() {
  const {rows, status} = useFigure('FIG-BO6-02');
  if (!rows) return <ChartState status={status} />;
  const names = {demand: 'Requests', rank: 'Scheduled position', weather: 'Weather'};
  const nominal = {demand: 0.4, rank: 0.3, weather: 0.3};
  const W = 560, left = 150, right = 20, rowH = 74, top = 36;
  const x = v => left + (v / 0.45) * (W - left - right);
  const H = top + rows.length * rowH;
  return (
    <div>
      <Svg width={W} height={H} label="Observed weighted contribution of each factor across 374 scored cells, against its nominal weight.">
        {[0, 0.1, 0.2, 0.3, 0.4].map(v => <g key={v}><line x1={x(v)} x2={x(v)} y1={top - 10} y2={H - 6} stroke={RULE} /><text x={x(v)} y={14} fontSize="10" fill={FROST} textAnchor="middle">{v.toFixed(1)}</text></g>)}
        {rows.map((r, i) => {
          const y = top + i * rowH + rowH / 2 - 8;
          return (
            <g key={r.factor}>
              <text x={left - 14} y={y} fontSize="12" fill={SNOW} textAnchor="end" dominantBaseline="central">{names[r.factor]}</text>
              <line x1={x(r.min_contribution)} x2={x(r.max_contribution)} y1={y} y2={y} stroke={GLACIER} strokeWidth="2" />
              <motion.rect y={y - 9} height="18" rx="3" fill={ICE} fillOpacity="0.85"
                initial={{x: x(r.median), width: 0}} whileInView={{x: x(r.p25), width: x(r.p75) - x(r.p25)}} viewport={inView} transition={{duration: 0.8}} />
              <line x1={x(r.median)} x2={x(r.median)} y1={y - 12} y2={y + 12} stroke={NIGHT} strokeWidth="2.5" />
              <path d={`M${x(nominal[r.factor])},${y - 20} l6,6 l-6,6 l-6,-6 Z`} fill="none" stroke={SODIUM} strokeWidth="1.5" transform="translate(0,-4)" />
              <text x={left} y={y + 26} fontSize="10" fill={FROST}>observed {r.min_contribution.toFixed(2)}–{r.max_contribution.toFixed(2)} · middle half {r.p25.toFixed(2)}–{r.p75.toFixed(2)}</text>
            </g>
          );
        })}
      </Svg>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-frost">
        <span><span className="mr-1.5 inline-block h-2.5 w-4 rounded-sm bg-ice" />middle half of cells</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rotate-45 border border-sodium" />nominal weight</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO6-01 / FIG-BO6-03 — the panel and its two profiles, never one axis. */
/* ------------------------------------------------------------------------ */

export function PanelSplit() {
  const {rows, status} = useFigure('FIG-BO6-01');
  if (!rows) return <ChartState status={status} />;
  const scored = rows.filter(r => r.score_status === 'scored').length;
  const partial = rows.length - scored;
  return (
    <div role="img" aria-label={`${fmt(rows.length)} event-zone cells: ${scored} with all three kinds of evidence, ${partial} without matching schedule evidence.`}>
      <div className="flex h-14 gap-1 overflow-hidden rounded-lg">
        <div className="flex items-center bg-ice px-3 font-mono text-sm font-medium text-night" style={{width: `${(scored / rows.length) * 100}%`}}>{scored}</div>
        <div className="flex flex-1 items-center justify-end border border-dashed border-sodium/70 px-3 font-mono text-sm text-sodium"
          style={{backgroundImage: 'repeating-linear-gradient(135deg, rgb(244 169 64 / 0.12) 0 6px, transparent 6px 12px)'}}>{partial}</div>
      </div>
      <div className="mt-3 flex justify-between gap-4 font-mono text-xs text-frost">
        <span className="text-ice">requests + weather + schedule</span>
        <span className="text-right text-sodium">no matching schedule · {((partial / rows.length) * 100).toFixed(1)}%</span>
      </div>
    </div>
  );
}

export function ProfilePanels() {
  const {rows, status} = useFigure('FIG-BO6-03');
  if (!rows) return <ChartState status={status} />;
  const profiles = [
    {id: 'full_3factor', name: 'Three-factor profile', note: 'CRITICAL starts at 75', color: ICE},
    {id: 'demand_weather_only', name: 'Two-factor profile', note: 'CRITICAL starts at 52.5 — no cell has reached it; the highest is 50.27', color: FROST},
  ];
  const levels = ['LOW', 'MED', 'HIGH', 'CRITICAL'];
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {profiles.map(p => {
        const byLevel = Object.fromEntries(rows.filter(r => r.score_weight_profile === p.id).map(r => [r.load_level, r]));
        return (
          <div key={p.id} className="rounded-xl border border-rule p-4">
            <div className="font-display text-xl font-bold tracking-wide" style={{color: p.color}}>{p.name}</div>
            <div className="mb-3 font-mono text-[11px] text-frost">own scale · {p.note}</div>
            <HBars width={420} max={100}
              label={`${p.name} load level distribution`}
              items={levels.map(level => ({label: level, value: Number(byLevel[level]?.pct_within_profile ?? 0), color: p.color,
                text: byLevel[level] ? `${byLevel[level].pct_within_profile}%` : '0 cells', muted: !byLevel[level]}))} />
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO1-03 — model, its deliberately weakened control, and the baseline.  */
/* ------------------------------------------------------------------------ */

export function ModelControls() {
  const {rows, status} = useFigure('FIG-BO1-03');
  if (!rows) return <ChartState status={status} />;
  const mae = (version, key) => {
    const subset = rows.filter(r => r.model_version === version);
    return subset.reduce((s, r) => s + r[key], 0) / subset.length;
  };
  const versions = [...new Set(rows.map(r => r.model_version))];
  const good = versions.find(v => !v.includes('nomonth'));
  const control = versions.find(v => v.includes('nomonth'));
  const cells = rows.filter(r => r.model_version === good).length;
  const events = new Set(rows.map(r => r.snowfall_event_id)).size;
  return (
    <div>
      <HBars max={25} label={`Mean absolute error on a holdout of ${events} events: model, no-month control, and baseline.`} items={[
        {label: 'Model (M1)', value: mae(good, 'model_abs_error'), text: mae(good, 'model_abs_error').toFixed(3), color: ICE},
        {label: 'No-month control', value: mae(control, 'model_abs_error'), text: mae(control, 'model_abs_error').toFixed(3), color: GLACIER},
        {label: 'Simple baseline', value: mae(good, 'baseline_abs_error'), text: mae(good, 'baseline_abs_error').toFixed(3), color: FROST},
      ]} />
      <p className="mt-2 font-mono text-xs text-frost">mean absolute error · holdout season 2025–2026 · {events} events · {cells} cells · lower is better</p>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* FIG-BO8-01 — rank displacement, one facet per model version.              */
/* ------------------------------------------------------------------------ */

export function Displacement() {
  const {rows, status} = useFigure('FIG-BO8-01');
  if (!rows) return <ChartState status={status} />;
  const versions = [...new Set(rows.map(r => r.model_version))].sort((a, b) => a.includes('nomonth') - b.includes('nomonth'));
  const W = 560, H = 130, left = 16, right = 16;
  const maxCells = Math.max(...rows.map(r => r.cells));
  const x = d => left + ((d + 21) / 42) * (W - left - right);
  return (
    <div className="space-y-5">
      {versions.map(version => {
        const subset = rows.filter(r => r.model_version === version);
        const up = subset.filter(r => r.rank_delta > 0).reduce((s, r) => s + r.cells, 0);
        const down = subset.filter(r => r.rank_delta < 0).reduce((s, r) => s + r.cells, 0);
        const sum = subset.reduce((s, r) => s + r.rank_delta * r.cells, 0);
        const name = version.includes('nomonth') ? 'No-month control' : 'Model (M1)';
        return (
          <div key={version}>
            <div className="mb-1 flex justify-between font-mono text-xs"><span className="text-snow">{name}</span><span className="text-frost">{up} up · {down} down · net {sum}</span></div>
            <Svg width={W} height={H} label={`${name}: ${up} cells moved up, ${down} moved down, net displacement ${sum}.`}>
              <line x1={x(0)} x2={x(0)} y1={0} y2={H - 20} stroke={FROST} strokeDasharray="2 3" />
              {subset.map(r => {
                const h = (r.cells / maxCells) * (H - 30);
                return <rect key={r.rank_delta} x={x(r.rank_delta) - 5} y={H - 20 - h} width="10" height={h} rx="1.5" fill={r.rank_delta > 0 ? ICE : r.rank_delta < 0 ? '#2c4462' : FROST} />;
              })}
              {[-20, -10, 0, 10, 20].map(d => <text key={d} x={x(d)} y={H - 4} fontSize="10" fill={FROST} textAnchor="middle">{d > 0 ? `+${d}` : d}</text>)}
            </Svg>
          </div>
        );
      })}
    </div>
  );
}

export const CHART_FOR = {
  'FIG-BO2-01': ShiftLadder,
  'FIG-BO2-02': DriftSlope,
  'FIG-BO2-03': OperationGrid,
  'FIG-BO2-04': AddressScatter,
  'FIG-BO3-00': SnowWindow,
  'FIG-BO3-01': EventTimeline,
  'FIG-BO3-02': SeasonTotals,
  'FIG-BO4-00': () => <div><ZoneMap animate={false} /><div className="mt-3"><ShiftLegend /></div></div>,
  'FIG-BO4-01': WardMatrix,
  'FIG-BO4-02': DominantStrip,
  'FIG-BO1-03': ModelControls,
  'FIG-BO1-04': WinterCategories,
  'FIG-BO6-01': PanelSplit,
  'FIG-BO6-02': FactorSpread,
  'FIG-BO6-03': ProfilePanels,
  'FIG-BO8-01': Displacement,
  'FIG-BO8-02': AttributionRules,
};
