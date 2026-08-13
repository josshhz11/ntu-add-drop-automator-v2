"""Centralized, environment-overridable configuration.

Every runtime tunable that used to be a hardcoded constant scattered through
app.py lives here instead, each overridable via an environment variable
(e.g. in a .env file) with no code changes needed. This matters for two very
different deployment shapes this app is meant to support:

- A shared hosted deployment (many NTU students, one server) wants
  conservative limits — e.g. a 2-hour session cap so nobody's browser tab
  monopolizes a shared driver pool forever.
- A local, single-user run (just you, on your own machine) has no reason to
  self-rate-limit — you might want SESSION_TIME_LIMIT_SECONDS set to cover
  an entire day, or a larger MAX_DRIVERS if your machine can take it.

Same code, different .env — nothing to edit in app.py either way.
"""

import os


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to `default` if unset."""
    value = os.environ.get(name)
    return int(value) if value else default


# How long a single swap session is allowed to keep retrying before it's
# marked "Timed Out" and the background thread stops. Default: 2 hours.
SESSION_TIME_LIMIT_SECONDS = _env_int("SESSION_TIME_LIMIT_SECONDS", 2 * 3600)

# How long to sleep between rounds of attempts once a round finds no
# vacancies. Default: 5 minutes.
RETRY_INTERVAL_SECONDS = _env_int("RETRY_INTERVAL_SECONDS", 5 * 60)

# How long a login session (and its swap state) survives in Redis before
# expiring, and the matching cookie session max-age. Independent of
# SESSION_TIME_LIMIT_SECONDS above — this is a security/storage TTL, not a
# "give up retrying" limit — but defaults to the same 2-hour value.
SESSION_TTL_SECONDS = _env_int("SESSION_TTL_SECONDS", 2 * 3600)

# Number of Chrome/Selenium driver instances kept in the shared pool. This is
# a hard cap on concurrent browser automation — see setup_driver_pool().
MAX_DRIVERS = _env_int("MAX_DRIVERS", 3)

# Redis auth password. Render's managed Redis (and most hosted Redis) requires
# one; local dev Redis typically doesn't, so this is None by default.
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD") or None

# Override the platform-detected Chrome/ChromeDriver paths (see
# CHROME_BINARY_PATH / CHROMEDRIVER_PATH in app.py) when set. Needed because
# render.yaml declares a CHROMEDRIVER_PATH env var for the deployed
# container — without reading it here, that env var was previously a no-op.
CHROME_BINARY_PATH_OVERRIDE = os.environ.get("CHROME_BINARY_PATH")
CHROMEDRIVER_PATH_OVERRIDE = os.environ.get("CHROMEDRIVER_PATH")

# Which storage backend holds session/swap state: "redis" (default, for the
# shared hosted deployment, where multiple concurrent users need state
# coordinated across one server) or "memory" (for a local single-user run,
# where there's no Redis process to install or manage). See storage.py.
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "redis").lower()
