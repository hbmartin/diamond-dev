# Two-commit mode

Instead of starting from a plan, `diamond-dev` can compare two commits that already
exist. Pass exactly two commit-ish refs and it skips the implementation agents,
builds the comparison bundle for those two inputs, and runs the rest of the normal
workflow — comparison judgment, acceptance, follow-up, review, and PR. This guide
covers invocation, how refs are resolved, and how clone/branch names are derived.
The logic lives in [`commit_pair.py`](../diamond_dev/commit_pair.py). Read the main
[README](../README.md) first.

## Invocation

```bash
diamond-dev abc123 def456
diamond-dev codex/feature claude/feature
```

Two-commit mode accepts SHAs, short SHAs, branches, tags, and remote refs. It
builds the comparison bundle for those two inputs, writes the comparison page to
the wiki, waits for the same acceptance checkbox, applies follow-up changes to the
accepted branch, runs review, and opens a PR. The PR title is
`Compare <selected branch>`.

## How the two refs are resolved

`resolve_commit_pair_inputs` clones `repository_url` into a temporary resolver and
resolves each argument there **first**. For each argument it tries the literal ref
and, unless the argument already starts with `origin/` or `refs/`, also
`origin/<arg>`.

If an argument is not reachable from the configured remote, `diamond-dev` falls
back to the **invocation repository** — but only when that repository's `origin`
URL matches `repository_url` after normalization (scheme/host/port/`.git`
differences are ignored). It then `git fetch`es the commit from your local clone.
If origin does not match, the run fails before comparison:

> Commit not reachable from configured remote and invocation repository origin
> does not match `repository_url`

The two arguments must resolve to **different** full SHAs; identical commits fail
with a clear error.

## Slug selection

`diamond-dev` needs a stable slug for the comparison artifacts. `choose_commit_pair_slug`:

1. **Reuse a stored slug.** It syncs the wiki and searches
   `diamond-dev-commit-comparisons.md` plus the hidden commit-pair markers in
   existing `*-comparison.md` pages for the ordered SHA pair. A match is reused so
   re-runs are stable.
2. **Generate a name.** With no stored slug, Codex is asked to produce a concise
   branch-style name from the two commit messages; the result is normalized with
   the same slug rules as plan filenames (`slugify` in
   [`naming.py`](../diamond_dev/naming.py)).
3. **Fallback.** If naming fails, the slug is `compare-<short-a>-vs-<short-b>`.
4. **Collision guard.** If the slug already belongs to a different commit pair (or
   a markerless legacy comparison owns it), the short SHA pair is appended.

## Labels and clone/branch names

Two-commit clone directories use `<label>-<slug>`. Labels come from
`infer_commit_labels`, which checks commit **messages** first, then ref/branch
names and the original argument:

- `codex` and `claude` are recognized as a pair when each side maps uniquely to
  one of them.
- If only one side matches, the other gets the opposite label.
- If a side matches both names ambiguously, both labels fall back to `a` and `b`.

Workflow branches come from `_branch_for_commit`:

- An explicit input branch/ref is used as the workflow branch when safe.
- If a SHA maps to exactly one containing branch, that branch is used.
- Otherwise — ambiguous, unbranched, duplicated, or matching the remote base
  branch — a generated branch `diamond-dev/<slug>/<label>` is used. Generated
  branches must be distinct or the run fails.

## Resume specifics

Two-commit runs auto-resume like plan runs (see
[Repositories & auto-resume](repositories-and-resume.md)), with one extra rule: a
local `comparison.md` is reused **only** when it carries the matching ordered
commit-pair marker; otherwise it is regenerated. The marker and the wiki index
entry are written from `CommitPairContext` in
[`workflow.py`](../diamond_dev/workflow.py):

```text
<!-- diamond-dev commit-pair: left=<sha> right=<sha> slug=<slug> left_label=<l> right_label=<r> -->
```

For the comparison bundle layout, acceptance checkbox, and review artifacts, see
[Acceptance & review artifacts](acceptance-and-review.md).
