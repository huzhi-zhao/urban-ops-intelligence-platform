import {useState} from 'react';
import {ShiftLegend, ZoneMap} from './charts';
import {useData} from './data';

function ZoneDetail({zone}) {
  if (!zone) {
    return <p className="text-[15px] text-frost">Hover or tab to a zone to light up every piece of it.</p>;
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-[15px]">
      <dt className="font-mono text-xs tracking-wide text-frost">ZONE</dt>
      <dd className="font-display text-3xl leading-none text-snow">{zone.zone}</dd>
      <dt className="font-mono text-xs tracking-wide text-frost">PIECES</dt>
      <dd className="text-snow tabular">{zone.parts}</dd>
      <dt className="font-mono text-xs tracking-wide text-frost">AVG SHIFT</dt>
      <dd className="text-snow tabular">{zone.scheduled ? zone.mean_shift : 'no residential schedule data'}</dd>
    </dl>
  );
}

export function ZoneMapPage() {
  const {zones} = useData();
  const [active, setActive] = useState(null);
  const zone = zones?.zones.find(z => z.zone === active);

  return (
    <main className="mx-auto max-w-7xl px-5 py-10 md:px-8">
      <a href="#top" className="inline-flex min-h-11 items-center gap-2 font-mono text-xs tracking-wide text-ice hover:underline">
        <span aria-hidden="true">←</span> Back to the story
      </a>
      <h1 className="mt-4 font-display text-4xl tracking-wide text-snow md:text-5xl">Plow zones</h1>
      <div className="mt-8 grid items-start gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="mx-auto w-full max-w-[760px]" onPointerLeave={() => setActive(null)}>
          <ZoneMap mode="shift" animate={false} active={active} onActivate={setActive} />
        </div>
        <aside className="space-y-6 lg:sticky lg:top-24" aria-live="polite">
          <ZoneDetail zone={zone} />
          <ShiftLegend />
        </aside>
      </div>
    </main>
  );
}
