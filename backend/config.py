"""Centralized, environment-overridable configuration.

Every runtime tunable that used to be a hardcoded constant scattered through
app.py lives here instead, each overridable via an environment variable
(e.g. in a .env file) with no code changes needed. This matters for three
different ways this app can run:

- A shared hosted deployment (many NTU students, one server) wants
  conservative limits — e.g. a 2-hour session cap so nobody's browser tab
  monopolizes a shared driver pool forever.
- A local, single-user dev run (RUN_MODE unset) behaves like the hosted
  shape unless you override it — you might want SESSION_TIME_LIMIT_SECONDS
  set to cover an entire day, or a larger MAX_DRIVERS if your machine can
  take it.
- A packaged desktop build (PyInstaller sets sys.frozen) automatically
  switches its defaults to the local-desktop shape below, with no
  environment variables required — a non-technical user downloads it and
  it just works. A power user can still override anything by dropping a
  `config.env` file next to the executable.

Same code, different .env — nothing to edit in app.py either way.
"""

import os
import sys

from dotenv import load_dotenv

# Load .env as early as possible, before reading any of the settings below.
# app.py's own load_dotenv() call (in initialize_components()) runs too late
# to affect the module-level constants here, since `import config` happens
# before that call ever executes — so config.py has to load its own .env
# independently to actually see values set only in a .env file.
#
# When packaged with PyInstaller, look for an optional `config.env` sitting
# next to the executable instead of a source-tree `.env` — this is the file
# a power user can edit to customize settings without touching OS
# environment variables; nobody else needs to know it exists.
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    load_dotenv(os.path.join(os.path.dirname(sys.executable), "config.env"))
else:
    load_dotenv()


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
# Not used at all in RUN_MODE=local, which finds Chrome/ChromeDriver via
# webdriver-manager instead.
CHROME_BINARY_PATH_OVERRIDE = os.environ.get("CHROME_BINARY_PATH")
CHROMEDRIVER_PATH_OVERRIDE = os.environ.get("CHROMEDRIVER_PATH")

# Which storage backend holds session/swap state: "redis" (shared hosted
# deployment, where multiple concurrent users need state coordinated across
# one server) or "memory" (local single-user run, no Redis process to
# install or manage). See storage.py. Defaults to "memory" automatically for
# a packaged desktop build; "redis" otherwise.
STORAGE_BACKEND = os.environ.get(
    "STORAGE_BACKEND", "memory" if _IS_FROZEN else "redis"
).lower()

# "server" (default when running from source): the shared hosted deployment
# — Chrome/ChromeDriver paths come from the overrides above (or a platform
# guess), and the frontend is hosted separately (Vercel).
#
# "local": a packaged desktop build — ChromeDriver is located automatically
# via webdriver-manager (whatever Chrome the user has installed, wherever it
# is), the built frontend is served from this same process, and the default
# browser opens automatically on startup. Defaults to "local" automatically
# when running as a PyInstaller-frozen executable. See app.py for each of
# these behaviors.
RUN_MODE = os.environ.get("RUN_MODE", "local" if _IS_FROZEN else "server").lower()
