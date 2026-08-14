"""Build a standalone local desktop distributable.

This is a developer-only tool — never run by end users, and nothing it
touches ships inside the distributable itself. It:

  1. Builds the React frontend as a static bundle with
     REACT_APP_LOCAL_BUILD=true, so the built app calls its API via relative
     paths instead of the hardcoded Render URL used by the hosted build.
  2. Packages the FastAPI backend + that static bundle into a single
     executable with PyInstaller, so end users need nothing installed
     except Google Chrome — no Python, no Node, no Docker.

Usage (from the repo root):
    pip install -r backend/requirements.txt -r backend/requirements-build.txt
    cd frontend && npm ci && cd ..
    python desktop/build_local_release.py

Output lands in desktop/dist/.
"""

import os
import platform
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
FRONTEND_BUILD_DIR = os.path.join(FRONTEND_DIR, "build")
DESKTOP_DIR = os.path.join(REPO_ROOT, "desktop")
DIST_DIR = os.path.join(DESKTOP_DIR, "dist")
WORK_DIR = os.path.join(DESKTOP_DIR, "build")

APP_NAME = "NTU-AddDrop-Automator"


def run(cmd, cwd=None, env=None):
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def build_frontend():
    print("=== Step 1/2: building frontend (relative API paths) ===")
    # Deliberately not pre-deleting FRONTEND_BUILD_DIR: `npm run build`
    # already overwrites its output in place, and this repo living inside a
    # synced folder (OneDrive) means a fresh rmtree can hit a longer-lived
    # lock while the sync client indexes the just-written files — safer and
    # simpler to just let the build tool manage its own output directory.

    env = os.environ.copy()
    env["REACT_APP_LOCAL_BUILD"] = "true"
    npm = "npm.cmd" if platform.system() == "Windows" else "npm"
    run([npm, "run", "build"], cwd=FRONTEND_DIR, env=env)

    if not os.path.isdir(FRONTEND_BUILD_DIR):
        raise RuntimeError(
            f"Expected frontend build output at {FRONTEND_BUILD_DIR}, but it wasn't created."
        )


def build_backend():
    print("\n=== Step 2/2: packaging backend + frontend with PyInstaller ===")
    add_data_sep = ";" if platform.system() == "Windows" else ":"
    add_data = f"{FRONTEND_BUILD_DIR}{add_data_sep}frontend_build"

    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            os.path.join(BACKEND_DIR, "app.py"),
            "--name",
            APP_NAME,
            "--onefile",
            "--add-data",
            add_data,
            # Selenium and webdriver-manager both do some dynamic importing
            # PyInstaller's static analysis doesn't always catch; bundling
            # everything from each avoids "hidden import" failures at
            # runtime for the cost of a somewhat larger executable.
            "--collect-all",
            "selenium",
            "--collect-all",
            "webdriver_manager",
            "--distpath",
            DIST_DIR,
            "--workpath",
            WORK_DIR,
            "--specpath",
            DESKTOP_DIR,
            "--noconfirm",
        ],
        cwd=REPO_ROOT,
    )


def main():
    build_frontend()
    build_backend()
    print(f"\nDone. Distributable is in: {DIST_DIR}")


if __name__ == "__main__":
    main()
