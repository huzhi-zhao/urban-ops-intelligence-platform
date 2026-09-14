import {clsx} from 'clsx';
import {twMerge} from 'tailwind-merge';

// Aceternity's components are written against this helper: clsx resolves the
// conditional arguments, tailwind-merge then drops earlier classes a later one
// overrides, so a caller's `className` wins over a component's default instead
// of the two both landing in the attribute and the cascade deciding.
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export const GITHUB = 'https://github.com/huzhi-zhao/urban-ops-intelligence-platform';

// Thousands separators, matching the en-CA grouping used elsewhere on the site.
// null/undefined render as an em dash rather than "NaN": a missing number and a
// zero must not look alike on a page whose whole claim is that it shows gaps.
export function fmt(value) {
  return value === null || value === undefined ? '—' : Number(value).toLocaleString('en-CA');
}
