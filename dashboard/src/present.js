// Two-window presenting: a laptop window drives a second window on the extended
// screen. What crosses the channel is a *position*, never pixels — the two
// screens are different sizes, so the payload is `{hash, p}` where `p` is the
// scroll progress 0..1. Broadcasting `scrollTop` would land the projector a few
// hundred pixels off, which is the hardest kind of error to notice on stage.
import {useEffect, useRef} from 'react';

const CHANNEL = 'uoip-present';

// 🔴 One-directional on purpose. A mirror where both ends both send and apply
// echoes forever unless every apply is distinguishable from a user action, and
// in React the obvious `applying = true / setState / applying = false` guard
// does not work: setState is async, so the flag is already back to false by the
// time the state-watching effect runs. Presenter sends, projector listens.
export function readRole() {
  return new URLSearchParams(window.location.search).get('role') === 'projector'
    ? 'projector'
    : 'presenter';
}

function progress() {
  const doc = document.documentElement;
  const travel = doc.scrollHeight - doc.clientHeight;
  return travel > 0 ? doc.scrollTop / travel : 0;
}

/** Broadcast this window's hash + scroll progress as the presenter. */
function usePresenter(enabled) {
  useEffect(() => {
    if (!enabled) return undefined;
    const channel = new BroadcastChannel(CHANNEL);
    let queued = false;
    const send = () => {
      queued = false;
      channel.postMessage({hash: window.location.hash, p: progress()});
    };
    // Coalesce to one message per frame: scroll fires far more often than the
    // projector can usefully repaint, and the newest position is the only one
    // that matters.
    const onScroll = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(send);
    };
    window.addEventListener('scroll', onScroll, {passive: true});
    window.addEventListener('hashchange', send);
    send();
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('hashchange', send);
      channel.close();
    };
  }, [enabled]);
}

/** Follow the presenter: same page, same place on the page. */
function useProjector(enabled) {
  const pending = useRef(null);
  useEffect(() => {
    if (!enabled) return undefined;
    const channel = new BroadcastChannel(CHANNEL);
    let queued = false;
    const apply = () => {
      queued = false;
      const state = pending.current;
      if (!state) return;
      if (state.hash !== window.location.hash) {
        // Route change re-renders the page; the new height is only known after
        // that paint, so the position is applied on the next frame.
        window.location.hash = state.hash;
        requestAnimationFrame(() => {
          const doc = document.documentElement;
          window.scrollTo({top: state.p * (doc.scrollHeight - doc.clientHeight)});
        });
        return;
      }
      const doc = document.documentElement;
      window.scrollTo({top: state.p * (doc.scrollHeight - doc.clientHeight)});
    };
    channel.onmessage = event => {
      pending.current = event.data;
      if (queued) return;
      queued = true;
      requestAnimationFrame(apply);
    };
    return () => channel.close();
  }, [enabled]);
}

export function usePresentSync(role) {
  // Both hooks always run — the role is passed *into* the effect rather than
  // deciding whether the hook is called, because a conditional hook call breaks
  // React's hook ordering.
  usePresenter(role === 'presenter');
  useProjector(role === 'projector');
}

/**
 * Open the projector window on the extended screen.
 *
 * Chromium only, and it needs a secure context — `npm run dev` on 127.0.0.1
 * counts, opening `dist/index.html` over `file://` does not: a file:// page has
 * an opaque origin, so BroadcastChannel silently delivers nothing at all.
 * Serve the build (`npm run preview`) instead.
 */
export async function openProjector() {
  const url = `${window.location.pathname}?role=projector${window.location.hash || '#/'}`;
  let box = 'popup,width=1280,height=800';
  if (window.getScreenDetails) {
    try {
      const details = await window.getScreenDetails();
      const screen = details.screens.find(s => !s.isPrimary) ?? details.currentScreen;
      box = `popup,left=${screen.availLeft},top=${screen.availTop},`
        + `width=${screen.availWidth},height=${screen.availHeight}`;
    } catch {
      // Permission declined, or a single-screen machine. The popup still opens;
      // it just lands on this screen and gets dragged across by hand.
    }
  }
  return window.open(url, 'uoip-projector', box);
}

// The projector is opened by a hotkey rather than a visible control: the header
// faces the room during the talk, and a "Projector" button there is both an
// invitation to click it mid-sentence and a word the audience reads and
// wonders about.
//
// Four modifiers, because every three-key combination is already someone
// else's: Ctrl+Shift+P is Firefox's private window, Cmd+Shift+P and Ctrl+Shift+P
// are the DevTools command menu, and Cmd+Shift+N / Ctrl+Shift+N are Chrome's
// incognito window. Ctrl+Shift+Cmd+P is unclaimed by macOS and by Chrome; the
// neighbouring Ctrl+Shift+Cmd+3/4 (screenshot to clipboard) are the closest
// things to it that exist.
const HOTKEY_CODE = 'KeyP';

// 🔴 Either identifier is accepted, because each one is empty or wrong on some
// path that reaches a stage. `code` is the physical key and is the reliable one
// on a laptop keyboard — `key` reads 'P' under Shift and 'π' under Alt on a
// Mac, so matching `key` alone drops the Alt variant. But `code` is *not*
// always populated: a synthesised keydown (remote-desktop software, a
// presentation clicker's driver, a key remapper, automation) commonly carries
// `key` and leaves `code` an empty string. Measured here, 2026-09-21: a
// dispatched Ctrl+Shift+Cmd+P arrived with the three modifiers correct,
// `key: 'p'` and `code: ''`. Matching on `code` alone is silently dead on those
// paths, and silently dead in front of a room is the whole cost.
function isHotkey(event) {
  return event.code === HOTKEY_CODE || event.key?.toLowerCase() === 'p';
}

/** Ctrl+Shift+Cmd+P — or Ctrl+Shift+Alt+P off a Mac — opens the projector. */
export function useProjectorHotkey(enabled) {
  useEffect(() => {
    if (!enabled) return undefined;
    const onKeyDown = event => {
      if (!isHotkey(event)) return;
      // Either fourth modifier is accepted, so one combination works on every
      // platform without sniffing the user agent for a Mac — `navigator.platform`
      // is deprecated and lies under iPadOS's desktop mode.
      if (!event.ctrlKey || !event.shiftKey || !(event.metaKey || event.altKey)) return;
      event.preventDefault();
      openProjector();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled]);
}
