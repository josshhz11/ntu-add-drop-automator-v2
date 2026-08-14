"""CI smoke test for the packaged local executable.

Launches the built distributable, polls its health endpoint until it
responds (or times out), then shuts it down. Exits non-zero — with the
app's own output printed for diagnosis — on any failure, so a broken build
fails the CI job loudly instead of silently shipping.

Usage: python desktop/smoke_test.py
(expects desktop/dist/ to already contain a built executable)
"""

import glob
import os
import platform
import signal
import subprocess
import sys
import time
import urllib.request

DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
PORT = "5050"  # distinct from the default 5000, to avoid colliding with anything else on the runner
IS_WINDOWS = platform.system() == "Windows"


def find_executable() -> str:
    if IS_WINDOWS:
        candidates = glob.glob(os.path.join(DIST_DIR, "*.exe"))
    else:
        candidates = [
            p
            for p in glob.glob(os.path.join(DIST_DIR, "*"))
            if os.access(p, os.X_OK) and not os.path.isdir(p)
        ]
    if not candidates:
        raise RuntimeError(f"No built executable found in {DIST_DIR}")
    return candidates[0]


def launch(exe: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PORT"] = PORT
    # Launch into its own process group/job so the whole tree (the
    # PyInstaller bootloader plus the ChromeDriver instances it spawns) can
    # be killed together afterward, instead of just the immediate process.
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [exe], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs
    )


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it spawned.

    A plain proc.terminate() only signals the immediate process — on
    Windows especially, the ChromeDriver instances spawned underneath
    survive that and keep the stdout pipe held open, which can hang a
    later read() forever. Killing the whole tree avoids that.
    """
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    exe = find_executable()
    print(f"Launching {exe} ...")
    proc = launch(exe)

    try:
        deadline = time.time() + 30
        last_error = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/health", timeout=2
                ) as resp:
                    print(f"Health check OK: {resp.read().decode()}")
                    return
            except Exception as e:
                last_error = e
                time.sleep(1)
        raise RuntimeError(f"App never became healthy within 30s. Last error: {last_error}")
    finally:
        kill_process_tree(proc)
        # Bounded read: even after killing the tree, fall back to a timeout
        # here rather than trusting that nothing could still be holding the
        # pipe open — a hung CI step is worse than a missing diagnostic.
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            output = "(could not collect app output — pipe still held open by a child process)"
        if output:
            print("\n--- App output ---")
            print(output)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"SMOKE TEST FAILED: {e}")
        sys.exit(1)
    print("Smoke test passed.")
