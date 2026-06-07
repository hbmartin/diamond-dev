# Documentation

These guides go deeper than the project [README](../README.md), which covers
installation, the quickstart, basic usage, the minimal config, and security. Reach
for these when you are configuring, customizing, operating, or extending
`diamond-dev`. They assume you have read the README.

## Understand

- [Architecture](architecture.md) — the diamond concept, the phases a run
  executes, which module owns each phase, and the core data structures.

## Configure & customize

- [Configuration reference](configuration.md) — every table and key in
  `.diamond-dev.toml`, defaults, validation, and legacy/migration notes.
- [Agents & custom adapters](agents.md) — built-in agents, the capability matrix,
  the commands each runs, defining custom agents, and per-agent auth.
- [Custom prompts](custom-prompts.md) — replace the built-in task instructions for
  any phase, and the required-context wrapper you cannot override.

## Reference

- [Two-commit mode](two-commit-mode.md) — compare two existing commits: ref
  resolution, slug/label inference, and clone/branch naming.
- [Repositories & auto-resume](repositories-and-resume.md) — generated clones and
  branches, lockfile install, plan immutability, and the full resume rules.
- [Acceptance & review artifacts](acceptance-and-review.md) — the comparison bundle
  format, the acceptance checkbox, and the review-judgment sidecar schema.

## Operate

- [Automation & CI integration](automation-and-ci.md) — run reports, exit codes,
  run statuses, and notification webhooks for unattended and pipeline use.
- [Observability](observability.md) — logging configuration, the JSONL log schema,
  structured phase fields, and OpenTelemetry trace enrichment.
- [Troubleshooting](troubleshooting.md) — preflight failures, plan drift, resume
  problems, warnings, and locating the right log for a failure.
