# diamond-dev

## Logging

diamond-dev uses Loguru for console and file logging. Logs are written to stderr
and to `logs/diamond-dev.log` by default.

Configure logging with environment variables:

- `DIAMOND_DEV_LOG_LEVEL`: Log level for both console and file output. Defaults to
  `INFO`.
- `DIAMOND_DEV_LOG_FILE`: File path for persistent logs. Defaults to
  `logs/diamond-dev.log`.

File logs rotate at 10 MB, retain rotated files for 30 days, and compress rotated
logs as zip files.
