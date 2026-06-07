# Observability

`diamond-dev` logs through [Loguru](https://github.com/Delgan/loguru) to three
sinks at once — stderr, a human-readable text file, and a serialized JSONL file —
and enriches every record with OpenTelemetry trace context when OpenTelemetry is
installed. This guide covers configuring those sinks, the JSONL schema, the
structured fields emitted at phase boundaries, and how to feed the data into a
dashboard or tracing backend.

For the run-report JSON (`logs/run.json`) and notification webhooks, see
[Automation & CI integration](automation-and-ci.md).

## Configuration

Logging is set up by `configure_logging` in
[`logging_setup.py`](../diamond_dev/logging_setup.py) and controlled entirely with
environment variables:

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `DIAMOND_DEV_LOG_LEVEL` | `INFO` | Level applied to all three sinks (console, text file, JSONL). Case-insensitive. |
| `DIAMOND_DEV_LOG_FILE` | `logs/diamond-dev.log` | Path to the readable text log. |
| `DIAMOND_DEV_JSON_LOG_FILE` | `logs/diamond-dev.jsonl` | Path to the serialized JSONL log. |
| `DIAMOND_DEV_LOG_DIAGNOSE` | enabled | Whether exception tracebacks include local-variable values. Disable with `0`, `false`, `no`, or `off`. |

`DIAMOND_DEV_LOG_DIAGNOSE` is a **security-relevant** toggle: with it enabled,
Loguru's extended tracebacks capture local variable values, which can include
secrets. Disable it when logs may be retained or shipped off-box. (See the
README's Security section.)

Parent directories for both log files are created automatically. Both file sinks:

- rotate at **10 MB**, retain rotated files for **30 days**, and compress rotated
  logs as **zip**;
- are written with `enqueue=True` (safe across the subprocesses `diamond-dev`
  spawns);
- use UTF-8 with `backslashreplace` for un-encodable bytes;
- are created with owner-only `0o600` permissions.

## Sinks and formats

**Console (stderr)** — concise, for humans watching a run:

```
2026-06-07 10:21:33 | INFO     | Phase started: preflight
```

**Text file** — adds source location and the OpenTelemetry fields:

```
2026-06-07 10:21:33.412 | INFO     | diamond_dev.orchestrator:run:157 | trace_id=… span_id=… trace_sampled=False service=… | Phase started: preflight
```

**JSONL file** — Loguru's `serialize=True` output: one JSON object per line,
suitable for ingestion into Elasticsearch, Loki, BigQuery, etc.

## The JSONL record schema

Each line is a standard Loguru serialized record. The fields you will rely on:

```jsonc
{
  "text": "2026-06-07 ... | INFO | ... | Phase succeeded: preflight\n",
  "record": {
    "time": { "repr": "2026-06-07 10:21:36.622...", "timestamp": 1781000496.622 },
    "level": { "name": "INFO", "no": 20 },
    "name": "diamond_dev.orchestrator",
    "function": "run",
    "line": 157,
    "message": "Phase succeeded: preflight",
    "extra": {
      "phase": "preflight",
      "phase_status": "succeeded",
      "duration_seconds": 3.21,
      "otelTraceID": "0",
      "otelSpanID": "0",
      "otelTraceSampled": false,
      "otelServiceName": ""
    },
    "exception": null
  }
}
```

The `record.extra` object is where structured, queryable context lives. The
`otel*` keys are always present (defaulting to `"0"`/`false`/`""` when
OpenTelemetry is not active — see below). Phase-related keys appear only on
phase-boundary records.

## Structured phase fields

The orchestrator wraps each phase in `_timed_phase`
([`orchestrator.py`](../diamond_dev/orchestrator.py)) and binds structured fields
via `logger.bind(...)`, so every phase emits start/end records carrying:

- `phase` — the phase name (e.g. `preflight`, `prepare comparison`,
  `poll acceptance`, `run review phases`, `finalize pull request`).
- `phase_status` — `started`, `succeeded`, or `failed`.
- `duration_seconds` — wall-clock time, present on the `succeeded` and `failed`
  records.

This makes per-phase dashboards straightforward without parsing message strings.
For example, to chart phase durations from the JSONL log:

```bash
# Completed phases with their durations.
jq -r 'select(.record.extra.phase_status=="succeeded")
       | "\(.record.extra.phase)\t\(.record.extra.duration_seconds)"' \
   logs/diamond-dev.jsonl

# Any phase that failed, with the message.
jq -r 'select(.record.extra.phase_status=="failed")
       | "\(.record.extra.phase): \(.record.message)"' \
   logs/diamond-dev.jsonl
```

> For an authoritative, end-of-run summary of phase timings and statuses, prefer
> the run report (`logs/run.json` → `phase_timings`) described in
> [Automation & CI integration](automation-and-ci.md). The JSONL phase records
> are the live stream; the run report is the consolidated result.

## OpenTelemetry trace enrichment

If the `opentelemetry` packages are importable, `configure_logging` installs a
patcher that stamps each record with the active span's context:

- `otelTraceID` — 32-hex-char trace id (or `"0"` when no active span).
- `otelSpanID` — 16-hex-char span id (or `"0"`).
- `otelTraceSampled` — boolean sampled flag.
- `otelServiceName` — the configured tracer provider's `service.name` resource
  attribute (or `""`).

When OpenTelemetry is **not** installed, the same keys are present with default
values, so downstream parsers can treat the schema as stable either way.

`diamond-dev` does not configure a tracer provider, exporter, or instrumentation
for you — it only *reads* the ambient OpenTelemetry context. To get real trace
ids and a service name into the logs, set up OpenTelemetry in the environment
before/around the run. A minimal local setup:

```bash
uv pip install opentelemetry-sdk opentelemetry-exporter-otlp

export OTEL_SERVICE_NAME="diamond-dev"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
# Auto-instrument the process so a root span (and service.name) exist:
opentelemetry-instrument diamond-dev my-plan.md
```

With a provider active, log records gain non-zero `otelTraceID`/`otelSpanID` and
the configured `otelServiceName`, letting you correlate `diamond-dev` log lines
with spans in your tracing backend (Jaeger, Tempo, Honeycomb, etc.).

## Per-command and agent logs

Beyond the three aggregate sinks, every external command `diamond-dev` runs — git
operations, agent CLIs, `gh`, the review provider — streams to its own file under
`logs/`. The index of these is the run report's `command_logs` array (`label`,
`command`, `cwd`, `log_path`); failed phases also point at the relevant
`log_path`. Start from `logs/run.json` to find the exact per-command log for a
failure rather than grepping the aggregate logs.

## Quick recipes

```bash
# Verbose run for debugging, secrets stripped from tracebacks.
DIAMOND_DEV_LOG_LEVEL=DEBUG DIAMOND_DEV_LOG_DIAGNOSE=0 diamond-dev my-plan.md

# Send logs somewhere other than ./logs (e.g. a mounted CI artifacts dir).
DIAMOND_DEV_LOG_FILE=/artifacts/diamond-dev.log \
DIAMOND_DEV_JSON_LOG_FILE=/artifacts/diamond-dev.jsonl \
  diamond-dev my-plan.md

# Tail structured progress live.
tail -f logs/diamond-dev.jsonl | jq -r '.record.message'
```
