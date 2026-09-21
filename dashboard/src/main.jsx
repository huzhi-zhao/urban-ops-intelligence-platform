import {StrictMode, useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MotionConfig} from 'motion/react';
import {DataProvider, useData} from './data';
import {Evidence} from './evidence';
import {ZoneMapPage} from './map';
import {ExternalLink, Story} from './story';
import {Zone} from './zone';
import {GITHUB, cn} from './lib/utils';
import {readRole, useProjectorHotkey, usePresentSync} from './present';
import './index.css';

// Hash routing keeps the build a folder of static files: `#/evidence/FIG-…` needs no server rewrite.
function readRoute() {
  if (/^#\/map\b/.test(window.location.hash)) return {page: 'map', id: null};
  const match = window.location.hash.match(/^#\/evidence(?:\/([\w-]+))?/);
  if (match) return {page: 'evidence', id: match[1]};
  if (window.location.hash.startsWith('#/zone')) return {page: 'zone', id: null};
  return {page: 'story', id: null};
}

function useRoute() {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const onChange = () => {
      const next = readRoute();
      setRoute(previous => {
        if (next.page !== 'story' && previous.page !== next.page) window.scrollTo({top: 0});
        return next;
      });
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  useEffect(() => {
    // Coming back from the library to an in-story anchor: wait for the story to render, then jump.
    if (route.page === 'story' && window.location.hash.length > 1) {
      requestAnimationFrame(() => document.querySelector(window.location.hash)?.scrollIntoView());
    }
  }, [route.page]);
  return route;
}

function Header({page}) {
  const link = 'inline-flex min-h-11 items-center px-3 text-sm transition hover:text-snow';
  return (
    <header className="sticky top-0 z-50 border-b border-rule/70 bg-night/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 md:px-8">
        <a href="#top" className="flex min-h-11 items-center gap-3" aria-label="UOIP — back to the story">
          <span className="font-display text-2xl font-black tracking-widest text-snow">UOIP</span>
          <span className="hidden font-mono text-[11px] tracking-wide text-frost sm:inline">Urban Operations Intelligence · Winnipeg</span>
        </a>
        <nav aria-label="Primary" className="flex items-center text-frost">
          <a href="#act-1" className={cn(link, page === 'story' && 'text-snow')}>Story</a>
          <a href="#/zone" className={cn(link, page === 'zone' && 'text-snow')}>Your zone</a>
          <a href="#/evidence" className={cn(link, page === 'evidence' && 'text-snow')}>Evidence</a>
          <ExternalLink href={GITHUB} className={link}>GitHub</ExternalLink>
        </nav>
      </div>
    </header>
  );
}

// 🔴 The freeze date was hand-written here as "8 September 2026", which is a
// claim about the data that nothing updates when the data is re-frozen. It is
// read off the exports instead: the oldest freeze, because the site is only as
// current as its stalest figure, and a range whenever they disagree rather than
// one date standing in for several.
function useFreezeDate() {
  const {figures} = useData();
  const days = useMemo(() => {
    const stamps = Object.values(figures)
      .map(figure => figure.frozen_at)
      .filter(Boolean)
      .map(stamp => String(stamp).slice(0, 10));
    return [...new Set(stamps)].sort();
  }, [figures]);
  if (!days.length) return null;
  return days.length === 1 ? days[0] : `${days[0]} – ${days[days.length - 1]}`;
}

function Footer() {
  const frozen = useFreezeDate();
  return (
    <footer className="border-t border-rule">
      <div className="mx-auto flex max-w-7xl flex-col gap-3 px-5 py-10 font-mono text-xs text-frost md:flex-row md:justify-between md:px-8">
        <p>
          Data: City of Winnipeg Open Data · Open-Meteo historical weather.
          {frozen ? ` Figures frozen ${frozen}.` : ' Figures not yet frozen.'}
        </p>
        <p>Urban Operations Intelligence Platform · <ExternalLink href={GITHUB} className="text-ice hover:underline">source</ExternalLink></p>
      </div>
    </footer>
  );
}

function App() {
  const route = useRoute();
  // The projector window shows the page and nothing else: no sticky header to
  // eat 4rem of a room-facing screen, no footer, and no navigation for an
  // audience that cannot click it.
  const role = useMemo(readRole, []);
  usePresentSync(role);
  // Only the presenter window listens: were the projector to answer the hotkey
  // too, one keystroke reaching both windows would open a second projector.
  useProjectorHotkey(role === 'presenter');
  const chrome = role === 'presenter';
  return (
    <MotionConfig reducedMotion="user">
      <DataProvider>
        <a href="#act-1" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[60] focus:rounded-lg focus:bg-ice focus:px-4 focus:py-2 focus:text-night">Skip to the story</a>
        {chrome && <Header page={route.page} />}
        {route.page === 'evidence' ? <Evidence selected={route.id} />
          : route.page === 'map' ? <ZoneMapPage />
          : route.page === 'zone' ? <Zone />
          : <Story />}
        {chrome && <Footer />}
      </DataProvider>
    </MotionConfig>
  );
}

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>);
