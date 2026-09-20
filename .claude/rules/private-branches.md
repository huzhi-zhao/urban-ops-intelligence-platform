# Private branches — what this repo must not carry

> Binding, same status as [backfill.md](backfill.md) and [gold-sql.md](gold-sql.md).

This repository is public. Personal research notes live on separate `private/*`
branches that push to a different remote, and everything such a branch adds
sits under a top-level `private/` directory.

**Nothing here may reference what is in there** — not a filename, not a title,
not a link. The test is CLAUDE.md's own: could a reader holding only this
repository verify the reference? A path under `private/` never can. Declaring
the path forbidden is fine; pointing at anything inside it is not.

---

## R1 · `private/` is a forbidden path on every public branch

```bash
git ls-tree -r --name-only HEAD -- private/
```

Must be empty. If it is not, a private file has been committed here and the
history needs fixing **before** anything is pushed.

## R2 · Private branches are additive

A private branch is `origin/main` plus files under `private/` — never a
modification of a tracked file. That confinement is what makes the sync in R3
conflict-free, and what makes R1 mechanically checkable.

```bash
git diff origin/main...private/<topic> --name-only | grep -v '^private/'
```

Empty output means compliant. A stray path is a hygiene problem, not a leak —
fix it, but it does not block a push.

## R3 · Sync is one-way

`git merge origin/main` into a private branch. Never the reverse, never a PR
from one. Main is the sole authority for code; a private branch is an
annotated snapshot of it and is always level with main or behind it, never
ahead.

Order of work follows from this: land the change here first, sync, *then*
update the notes. Notes cite `file:line`, and writing them before the code
settles leaves citations that drift with nothing to report the drift.

---

## The guard does not travel with a clone

Enforcement is a `pre-push` hook, and `.git/hooks/` is not versioned — **a
fresh clone has no protection at all until one is reinstalled.** It must
refuse two things:

1. a `private/*` branch pushed to a remote not flagged
   `git config remote.<name>.uoipPrivate true` (fail closed: unflagged means
   public, so a newly added remote never silently opens a path)
2. a push to an unflagged remote whose tip tree contains a `private/` path

🔴 **The second check is the load-bearing one.** The first reads branch
*names*, and is blind to the accident that actually happens: a `git add -A`
run while private files sit in the working tree commits them to a public
branch, under a perfectly public name.
