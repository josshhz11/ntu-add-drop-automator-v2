import base64
import json
import os
import platform
import queue
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone

import redis
import redis.asyncio as redis_asyncio
import uvicorn
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from selenium import webdriver
from selenium.common.exceptions import (
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from starlette.middleware.sessions import SessionMiddleware

import config
import storage

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def check_chrome_paths():
    try:
        chrome_path = subprocess.getoutput("which google-chrome")
        chromedriver_path = subprocess.getoutput("which chromedriver")
        chrome_version = subprocess.getoutput("google-chrome --version")
        chromedriver_version = subprocess.getoutput("chromedriver --version")

        print(f"Chrome Path: {chrome_path}")
        print(f"Chrome Version: {chrome_version}")
        print(f"ChromeDriver Path: {chromedriver_path}")
        print(f"ChromeDriver Version: {chromedriver_version}")

    except Exception as e:
        print(f"Error checking paths: {e!s}")


def setup_storage_backends():
    """Create the shared storage clients used across the app.

    Two separate clients are created ONCE here and reused for the app's whole
    lifetime, instead of opening a brand-new connection on every call:

    - An async client for the FastAPI request handlers, so a round-trip to
      the backing store never blocks the single asyncio event loop while
      other requests (including every status poll, from every user) wait
      behind it.
    - A sync client for the background Selenium threads (`perform_swaps` and
      friends). Those run on plain OS threads, not inside the event loop, so
      there is nothing to gain from `await` there and no easy way to do it
      safely from a non-async thread anyway.

    Which backend actually gets used is controlled by `config.STORAGE_BACKEND`:
    "redis" (default) for the shared hosted deployment, or "memory" for a
    local single-user run with no Redis process to install or manage. Every
    caller elsewhere in the app talks to whichever one comes back through the
    exact same interface (`get`/`setex`/`ttl`/`delete`/`ping`/`set`), so
    nothing else in the codebase needs to know or care which is active.
    """
    if config.STORAGE_BACKEND == "memory":
        memory_store = storage.InMemoryStorage()
        return (lambda: storage.InMemoryStorageAsync(memory_store)), memory_store

    redis_config = {
        "host": os.environ.get("REDIS_HOST"),
        "port": int(os.environ.get("REDIS_PORT", "6379")),
        "password": config.REDIS_PASSWORD,
        "decode_responses": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    }

    sync_client = redis.Redis(**redis_config)
    async_client = redis_asyncio.Redis(**redis_config)

    def get_redis():
        """FastAPI dependency: returns the shared async client."""
        return async_client

    return get_redis, sync_client


# Configure ChromeDriver settings. CHROME_BINARY_PATH/CHROMEDRIVER_PATH env
# vars (e.g. from render.yaml) take priority when set; otherwise fall back to
# a platform-based guess (Linux for containers, this dev machine's path for
# Windows).
CHROME_BINARY_PATH = config.CHROME_BINARY_PATH_OVERRIDE or (
    "/usr/bin/google-chrome"
    if platform.system() == "Linux"
    else "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
)
CHROMEDRIVER_PATH = config.CHROMEDRIVER_PATH_OVERRIDE or (
    "/usr/local/bin/chromedriver"
    if platform.system() == "Linux"
    else "C:\\Users\\joshua\\Downloads\\chromedriver-win64\\chromedriver.exe"
)


def setup_chrome_options():
    """Configure and return Chrome options."""
    chrome_options = Options()
    chrome_options.binary_location = CHROME_BINARY_PATH
    chrome_options.add_argument("--headless")  # Headless mode
    chrome_options.add_argument("--disable-gpu")  # Fixes rendering issues
    chrome_options.add_argument("--no-sandbox")  # Required for running as root
    chrome_options.add_argument("--disable-dev-shm-usage")  # Fix shared memory issues
    chrome_options.add_argument("--disable-software-rasterizer")  # Prevents crashes
    chrome_options.add_argument("--window-size=1920x1080")  # Ensures proper rendering
    return chrome_options


def create_driver(chrome_options):
    """Create and return a new Selenium WebDriver instance."""
    try:
        print("Starting ChromeDriver...")
        service = Service(CHROMEDRIVER_PATH)  # Ensure correct path
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print("ChromeDriver started successfully!")
        return driver
    except Exception as e:
        print(f"Error creating WebDriver: {e!s}")
        raise


def setup_driver_pool(chrome_options, max_drivers=3):
    """Set up a bounded pool of `max_drivers` Chrome driver instances.

    This is a genuine hard cap, not just a warm-start count: `get_driver()`
    blocks the calling thread when all instances are checked out, instead of
    spawning an unbounded number of extra Chrome processes. Sessions that call
    it while the pool is empty queue up for free — `queue.Queue` blocks
    waiting threads on an internal condition variable and wakes them in the
    order they started waiting, so no separate wait-list needs to be tracked
    by hand. Each session's own background thread IS its queue entry while
    it's blocked here.
    """
    driver_pool: queue.Queue = queue.Queue(maxsize=max_drivers)

    # Preload ChromeDriver instances
    for _ in range(max_drivers):
        driver_pool.put(create_driver(chrome_options))

    def get_driver():
        """Check out a driver, blocking until one is available if the pool is empty."""
        return driver_pool.get(block=True)

    def release_driver(driver):
        """Return a driver to the pool, unblocking the longest-waiting caller (if any)."""
        driver_pool.put(driver)

    return get_driver, release_driver


# ============================================================================
# SECURITY AND ENCRYPTION FUNCTIONS
# ============================================================================


def get_encryption_key():
    """Get encryption key from environment variables."""
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable is required.")
    return key.encode()


def encrypt_password(password: str) -> str:
    """Encrypt password for secure storage in Redis."""
    key = get_encryption_key()
    f = Fernet(key)
    encrypted_password = f.encrypt(password.encode())
    return base64.urlsafe_b64encode(encrypted_password).decode()


def decrypt_password(encrypted_password: str) -> str:
    """Decrypt password for login during swap attempt."""
    key = get_encryption_key()
    f = Fernet(key)
    encrypted_bytes = base64.urlsafe_b64decode(encrypted_password.encode())
    decrypted_password = f.decrypt(encrypted_bytes)
    return decrypted_password.decode()


# ----------------------------------------------------------------------------
# Sync session helpers — used by the background Selenium thread (perform_swaps
# and everything it calls), via the shared sync Redis client. Kept sync
# because that thread is a plain OS thread, not a coroutine.
# ----------------------------------------------------------------------------


def create_secure_session(redis_db, username: str, password: str) -> str:
    """Create secure session with encrypted credentials in Redis"""
    session_id = secrets.token_urlsafe(32)

    # Encrypt password before storing
    encrypted_password = encrypt_password(password)

    # Store in Redis with a configurable TTL (defaults to 2 hours)
    session_data = {
        "username": username,
        "encrypted_password": encrypted_password,
        "authenticated": True,
        "created_at": time.time(),
        "expires_at": time.time() + config.SESSION_TTL_SECONDS,
        # Swap status info (initially empty)
        "swap_status": "Idle",  # Idle, Processing, Completed, Error, Stopped, Timed Out
        "swap_message": None,
        "swap_started_at": None,
        "swap_phase": "done",  # nothing to poll for until a swap is submitted
        # Module data (initially empty, populated when swap starts)
        "modules": [],
    }

    redis_db.setex(
        f"session:{session_id}", config.SESSION_TTL_SECONDS, json.dumps(session_data)
    )
    return session_id


def get_secure_session(redis_db, session_id: str) -> dict:
    """Retrieve and validate secure session."""
    if not session_id:
        raise HTTPException(status_code=401, detail="No session ID provided")

    session_data_json = redis_db.get(f"session:{session_id}")
    if not session_data_json:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    session_data = json.loads(session_data_json)

    # Additional session expiration check
    if time.time() > session_data.get("expires_at", 0):
        redis_db.delete(f"session:{session_id}")
        raise HTTPException(status_code=401, detail="Session expired.")

    return session_data


def update_session_data(redis_db, session_id: str, updates: dict) -> None:
    """Update session data in Redis"""
    session_data = get_secure_session(redis_db, session_id)
    session_data.update(updates)

    # Maintain TTL
    ttl = redis_db.ttl(f"session:{session_id}")
    if ttl > 0:
        redis_db.setex(f"session:{session_id}", ttl, json.dumps(session_data))


def get_decrypted_credentials(redis_db, session_id: str) -> tuple:
    """Get username and decrypted password from secure session."""
    session_data = get_secure_session(redis_db, session_id)

    username = session_data["username"]
    encrypted_password = session_data["encrypted_password"]
    password = decrypt_password(encrypted_password)

    return username, password


# ----------------------------------------------------------------------------
# Async session helpers — used by the FastAPI request handlers, via the
# shared async Redis client, so a Redis round-trip never blocks the event
# loop while other requests (e.g. every other user's status poll) wait on it.
# Mirror the sync versions above one-for-one; kept separate rather than
# shared because the background thread cannot safely `await`.
# ----------------------------------------------------------------------------


async def create_secure_session_async(redis_db, username: str, password: str) -> str:
    """Async counterpart of create_secure_session, for use in request handlers."""
    session_id = secrets.token_urlsafe(32)

    encrypted_password = encrypt_password(password)

    session_data = {
        "username": username,
        "encrypted_password": encrypted_password,
        "authenticated": True,
        "created_at": time.time(),
        "expires_at": time.time() + config.SESSION_TTL_SECONDS,
        "swap_status": "Idle",  # Idle, Processing, Completed, Error, Stopped, Timed Out
        "swap_message": None,
        "swap_started_at": None,
        "swap_phase": "done",  # nothing to poll for until a swap is submitted
        "modules": [],
    }

    await redis_db.setex(
        f"session:{session_id}", config.SESSION_TTL_SECONDS, json.dumps(session_data)
    )
    return session_id


async def get_secure_session_async(redis_db, session_id: str) -> dict:
    """Async counterpart of get_secure_session, for use in request handlers."""
    if not session_id:
        raise HTTPException(status_code=401, detail="No session ID provided")

    session_data_json = await redis_db.get(f"session:{session_id}")
    if not session_data_json:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    session_data = json.loads(session_data_json)

    if time.time() > session_data.get("expires_at", 0):
        await redis_db.delete(f"session:{session_id}")
        raise HTTPException(status_code=401, detail="Session expired.")

    return session_data


async def update_session_data_async(redis_db, session_id: str, updates: dict) -> None:
    """Async counterpart of update_session_data, for use in request handlers."""
    session_data = await get_secure_session_async(redis_db, session_id)
    session_data.update(updates)

    ttl = await redis_db.ttl(f"session:{session_id}")
    if ttl > 0:
        await redis_db.setex(f"session:{session_id}", ttl, json.dumps(session_data))


async def get_decrypted_credentials_async(redis_db, session_id: str) -> tuple:
    """Async counterpart of get_decrypted_credentials, for use in request handlers."""
    session_data = await get_secure_session_async(redis_db, session_id)

    username = session_data["username"]
    encrypted_password = session_data["encrypted_password"]
    password = decrypt_password(encrypted_password)

    return username, password


async def update_overall_swap_status_async(
    redis_db, session_id: str, status: str, message: str, phase: str = "active"
) -> None:
    """Async counterpart of update_overall_swap_status, for use in request handlers."""
    updates = {"swap_status": status, "swap_message": message, "swap_phase": phase}
    await update_session_data_async(redis_db, session_id, updates)


async def initialize_swap_in_session_async(
    redis_db, session_id: str, swap_items: list
) -> None:
    """Async counterpart of initialize_swap_in_session, for use in request handlers."""
    updates = {
        "swap_status": "Processing",
        "swap_message": "Your swap request is being processed",
        "swap_started_at": time.time(),
        "swap_phase": "active",
        "attempt_count": 0,
        "last_attempt_at": None,
        "modules": [
            {
                "old_index": item["old_index"],
                "new_indexes": item["new_indexes"],
                "swapped": False,
                "message": "Pending...",
            }
            for item in swap_items
        ],
    }
    await update_session_data_async(redis_db, session_id, updates)


# ============================================================================
# GLOBAL VARIABLES (Will be initialized in main())
# ============================================================================

get_redis = None
redis_sync_client = None
get_driver = None
release_driver = None


def initialize_components() -> None:
    """Initialize all components needed by the app."""
    global get_redis, redis_sync_client, get_driver, release_driver

    print("Loading environment variables...")
    load_dotenv()

    print("Checking Chrome installations...")
    check_chrome_paths()

    print(f"Setting up storage backend ({config.STORAGE_BACKEND})...")
    get_redis, redis_sync_client = setup_storage_backends()

    print("Setting up Chrome WebDriver pool...")
    chrome_options = setup_chrome_options()
    get_driver, release_driver = setup_driver_pool(
        chrome_options, max_drivers=config.MAX_DRIVERS
    )


# ============================================================================
# UTILITY FUNCTIONS FOR REDIS AND STATUS
# ============================================================================


def initialize_swap_in_session(redis_db, session_id: str, swap_items: list) -> None:
    """Initialize swap data in existing session"""
    updates = {
        "swap_status": "Processing",
        "swap_message": "Your swap request is being processed",
        "swap_started_at": time.time(),
        "swap_phase": "active",
        "attempt_count": 0,
        "last_attempt_at": None,
        "modules": [
            {
                "old_index": item["old_index"],
                "new_indexes": item["new_indexes"],
                "swapped": False,
                "message": "Pending...",
            }
            for item in swap_items
        ],
    }
    update_session_data(redis_db, session_id, updates)


def update_module_status(
    redis_db, session_id: str, module_idx: int, message: str, success: bool = False
) -> None:
    """Update status of specific module in session"""
    session_data = get_secure_session(redis_db, session_id)

    if module_idx < len(session_data["modules"]):
        session_data["modules"][module_idx]["message"] = message
        if success:
            session_data["modules"][module_idx]["swapped"] = True

        # Update in Redis
        ttl = redis_db.ttl(f"session:{session_id}")
        if ttl > 0:
            redis_db.setex(f"session:{session_id}", ttl, json.dumps(session_data))


def update_overall_swap_status(
    redis_db, session_id: str, status: str, message: str, phase: str = "active"
) -> None:
    """Update overall swap status in session.

    `phase` drives the frontend's polling cadence, since a fixed interval
    can't be both fast enough to show live per-index progress and slow
    enough to not waste requests during a 5-minute idle gap:
      - "active": the browser is doing something right now (waiting for a
        driver, logging in, working through indexes) — poll frequently.
      - "idle": between attempt rounds — nothing will change until the next
        round starts, so poll infrequently.
      - "done": a terminal state (Completed/Error/Timed Out/Stopped) —
        nothing will ever change again, so the frontend can stop polling.
    """
    updates = {"swap_status": status, "swap_message": message, "swap_phase": phase}
    update_session_data(redis_db, session_id, updates)


def record_swap_attempt(redis_db, session_id: str, attempt_number: int) -> None:
    """Persist the attempt number and timestamp for a completed round of swap attempts."""
    updates = {"attempt_count": attempt_number, "last_attempt_at": time.time()}
    update_session_data(redis_db, session_id, updates)


def log_swap_event(session_id: str, username: str, message: str) -> None:
    """Print a swap-related log line tagged with the user and session for traceability."""
    print(f"[{username}] [{session_id[:8]}] {message}")


# ============================================================================
# VALIDATION AND HELPER FUNCTIONS
# ============================================================================


# Login Validation: No session, direct request-based validation
def validate_login(username: str, password: str) -> bool:
    return bool(username and password)


# ============================================================================
# SELENIUM AUTOMATION FUNCTIONS
# ============================================================================


def login_to_portal(
    driver, username: str, password: str, session_id: str, redis_db
) -> bool:
    """
    Log in to the NTU portal.
    """
    url = "https://wish.wis.ntu.edu.sg/pls/webexe/ldap_login.login?w_url=https://wish.wis.ntu.edu.sg/pls/webexe/aus_stars_planner.main"
    driver.get(url)

    username_field = driver.find_element(By.ID, "UID")
    username_field.send_keys(username)
    ok_button = driver.find_element(By.XPATH, "//input[@value='OK']")
    ok_button.click()

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "PW")))

    password_field = driver.find_element(By.ID, "PW")
    password_field.send_keys(password)
    ok_button = driver.find_element(By.XPATH, "//input[@value='OK']")
    ok_button.click()

    # Check if login is successful or redirected to a different page
    try:
        # Wait for the URL to either be the expected URL or the alternate URL
        WebDriverWait(driver, 10).until(
            lambda d: (
                d.current_url
                in [
                    "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.planner",
                    "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.time_table",
                ]
            )
        )

        # Check if redirected to the time_table URL
        if (
            driver.current_url
            == "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.time_table"
        ):
            # Check for the "Plan/ Registration" button
            try:
                plan_button = driver.find_element(
                    By.XPATH, "//input[@value='Plan/ Registration']"
                )
                plan_button.click()
            except Exception:
                error_message = (
                    "Unable to find or click the 'Plan/ Registration' button."
                )
                update_overall_swap_status(
                    redis_db,
                    session_id,
                    status="Error",
                    message=error_message,
                    phase="done",
                )
                return False

        # Proceed to wait for the table if on the planner page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//table[@bordercolor='#E0E0E0']")
            )
        )

        return True
    # If login fails, print exception
    except Exception:
        # If login fails, update status and exit
        error_message = "Incorrect username/password. Please try again."
        update_overall_swap_status(
            redis_db, session_id, status="Error", message=error_message, phase="done"
        )
        return False


def perform_swaps(
    username: str, password: str, swap_items: list, session_id: str, redis_db
):
    # `driver` is only ever held while a round of attempts is actually in
    # progress. It is checked out fresh from the pool at the top of each
    # round and released back before the 5-minute sleep, instead of being
    # held for the entire (up to 2-hour) session lifetime — this is what lets
    # a small driver pool serve far more concurrent sessions than its size,
    # since most of a session's lifetime is spent asleep, not driving Chrome.
    driver = None

    try:
        start_time = time.time()
        attempt_number = 0
        while True:
            # Check if session still exists (user might have stopped it)
            try:
                session_data = get_secure_session(redis_db, session_id)
                if session_data.get("swap_status") == "Stopped":
                    break
            except HTTPException:
                break  # Session expired or deleted

            attempt_number += 1

            # Let the frontend know this session is waiting its turn for a
            # browser if the pool is fully checked out right now — get_driver()
            # blocks below until one frees up.
            update_overall_swap_status(
                redis_db,
                session_id,
                status="Processing",
                message="Waiting for an available browser slot...",
            )
            driver = get_driver()

            # A driver from the shared pool has no memory of this session's
            # login (it may have just served a different session entirely),
            # so log in fresh every round rather than assuming it's still valid.
            update_overall_swap_status(
                redis_db,
                session_id,
                status="Processing",
                message=f"Attempting Swap {attempt_number}...",
            )
            login_success = login_to_portal(
                driver, username, password, session_id, redis_db
            )
            if not login_success:
                return

            # Attempt swaps
            for idx, item in enumerate(swap_items):
                if not item["swapped"]:
                    failed_indexes = []
                    for new_index in item["new_indexes"]:
                        try:
                            success, _message = attempt_swap(
                                old_index=item["old_index"],
                                new_index=new_index,
                                idx=idx,
                                driver=driver,
                                session_id=session_id,
                                redis_db=redis_db,
                                username=username,
                                attempt_number=attempt_number,
                            )
                            if success:
                                item["swapped"] = True
                                update_module_status(
                                    redis_db,
                                    session_id,
                                    idx,
                                    f"Successfully swapped {item['old_index']} → {new_index}",
                                    success=True,
                                )
                                break
                            else:
                                failed_indexes.append(new_index)
                        except WebDriverException as e:
                            log_swap_event(
                                session_id, username, f"WebDriver error: {e}"
                            )
                            release_driver(driver)  # Release current driver
                            driver = get_driver()  # Get a new driver
                            login_to_portal(
                                driver, username, password, session_id, redis_db
                            )
                            failed_indexes.append(new_index)
                        except Exception as e:
                            error_message = f"Error during swap attempt: {e}"
                            update_module_status(
                                redis_db, session_id, idx, message=error_message
                            )
                            failed_indexes.append(new_index)

                    if not item["swapped"] and failed_indexes:
                        update_module_status(
                            redis_db,
                            session_id,
                            idx,
                            f"Indexes {', '.join(failed_indexes)} have no vacancies.",
                        )

            # One full round of attempts across all modules has completed.
            # Hand the driver back to the pool now, before the 5-minute sleep,
            # so other waiting sessions can use it in the meantime.
            release_driver(driver)
            driver = None

            record_swap_attempt(redis_db, session_id, attempt_number)
            swapped_count = sum(1 for item in swap_items if item["swapped"])
            log_swap_event(
                session_id,
                username,
                f"Attempt {attempt_number}: {swapped_count}/{len(swap_items)} modules swapped so far",
            )

            # Check if all items are swapped
            all_swapped = all(item["swapped"] for item in swap_items)
            if all_swapped:
                update_overall_swap_status(
                    redis_db,
                    session_id,
                    status="Completed",
                    message="All modules have been successfully swapped.",
                    phase="done",
                )
                break

            if time.time() - start_time >= config.SESSION_TIME_LIMIT_SECONDS:
                update_overall_swap_status(
                    redis_db,
                    session_id,
                    status="Timed Out",
                    message="Time limit reached before completing the swap.",
                    phase="done",
                )
                log_swap_event(
                    session_id,
                    username,
                    "Time limit reached before completing the swap.",
                )
                break

            retry_minutes = config.RETRY_INTERVAL_SECONDS / 60
            update_overall_swap_status(
                redis_db,
                session_id,
                status="Processing",
                message=(
                    f"Attempt {attempt_number}: no vacancies found yet. "
                    f"Retrying every {retry_minutes:g} minutes."
                ),
                phase="idle",
            )

            time.sleep(config.RETRY_INTERVAL_SECONDS)
    except Exception as e:
        update_overall_swap_status(
            redis_db,
            session_id,
            status="Error",
            message=f"An error occurred: {e!s}",
            phase="done",
        )
        log_swap_event(session_id, username, f"An error occurred: {e!s}")
    finally:
        if driver:
            release_driver(driver)  # Ensure driver is released back to the pool


def attempt_swap(
    old_index: str,
    new_index: str,
    idx: int,
    driver,
    session_id: str,
    redis_db,
    username: str = "",
    attempt_number: int = 0,
) -> tuple:
    """
    Performs swap attempt, updates Redis status, and returns success status.

    Args:
        old_index (str): The current course index.
        new_index (str): The desired new course index.
        idx (int): The index in the swap list (for status tracking).
        driver (webdriver.Chrome): Selenium WebDriver instance.
        session_id (str): Unique session ID.
        redis_db: Redis connection (Injected via FastAPI).

    Returns:
        (bool, str): Tuple with success status and message.
    """
    try:
        update_module_status(
            redis_db, session_id, idx, f"Attempting to swap {old_index} -> {new_index}"
        )

        # 1) Wait for the table element to appear on the main page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//table[@bordercolor='#E0E0E0']")
            )
        )

        # 2) Locate the radio button for old_index by its value attribute and click it.
        try:
            # Wait for the radio button to be present
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, f"//input[@type='radio' and @value='{old_index}']")
                )
            )

            # Locate and click the radio button
            radio_button = driver.find_element(
                By.XPATH, f"//input[@type='radio' and @value='{old_index}']"
            )
            radio_button.click()

        except TimeoutException:
            # If the radio button is not found within the timeout period
            error_message = f"Old index  {old_index} not found. Swap cannot proceed."
            update_module_status(redis_db, session_id, idx, error_message)
            update_overall_swap_status(
                redis_db, session_id, status="Error", message=error_message
            )
            return False, error_message  # Return a value indicating failure

        except Exception as e:
            # Handle any unexpected errors
            error_message = (
                f"Unexpected error locating radio button for index {old_index}: {e!s}"
            )
            update_module_status(redis_db, session_id, idx, error_message)
            return False, error_message  # Return a value indicating failure

        # 3) Select the "Change Index" option from the dropdown
        dropdown = Select(driver.find_element(By.NAME, "opt"))
        dropdown.select_by_value("C")

        # 4) Click the 'Go' button
        header = driver.find_element(By.CLASS_NAME, "site-header__body")
        driver.execute_script(
            "arguments[0].style.visibility = 'hidden';", header
        )  # Hide the header
        go_button = driver.find_element(
            By.XPATH, "//input[@type='submit' and @value='Go']"
        )
        go_button.click()

        """
        Swap index page after choosing the mod and index you want to swap
        """

        # 5) Check for an alert, if portal is closed
        try:
            WebDriverWait(driver, 5).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()  # Close the alert
            error_message = (
                "Portal is closed now. Please try again from 10:30am - 10:00pm."
            )
            update_overall_swap_status(
                redis_db,
                session_id,
                status="Error",
                message=error_message,
            )
            return False, error_message
        except TimeoutException:
            pass  # If no alert, proceed to the swap index page

        # 6) Wait for the swap index page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "AUS_STARS_MENU"))
        )

        # 7) Check if the new index exists and has vacancies
        try:
            # Locate the dropdown for selecting the new index
            dropdown_element = driver.find_element(By.NAME, "new_index_nmbr")

            # Locate the option for the new index
            options = dropdown_element.find_elements(
                By.XPATH, f".//option[@value='{new_index}']"
            )

            if not options:
                # If the desired new index is not in the dropdown, handle the error
                error_message = f"New Index {new_index} was not found in the dropdown options. Swap cannot proceed."
                update_module_status(redis_db, session_id, idx, error_message)

                # Click the 'Back To Timetable' button
                back_button = driver.find_element(
                    By.XPATH, "//input[@type='submit' and @value='Back to Timetable']"
                )
                back_button.click()

                return False, error_message  # Return a value indicating failure

            # Parse the vacancies from the option text (e.g., "01172 / 9 / 1")
            option_text = options[0].text
            try:
                vacancies = int(
                    option_text.split(" / ")[1]
                )  # Parse out the middle number (vacancies)
                log_swap_event(
                    session_id,
                    username,
                    f"Attempt {attempt_number}: Vacancies for index {new_index} = {vacancies}",
                )
            except (IndexError, ValueError) as e:
                error_message = (
                    f"Failed to parse vacancies for index {new_index}: {e!s}"
                )
                update_module_status(redis_db, session_id, idx, error_message)

                # Click the 'Back To Timetable' button
                back_button = driver.find_element(
                    By.XPATH, "//input[@type='submit' and @value='Back to Timetable']"
                )
                back_button.click()

                return False, error_message

            # Select the new index in the dropdown
            select_dropdown = Select(dropdown_element)
            select_dropdown.select_by_value(new_index)

            if vacancies <= 0:
                # If there are no vacancies, handle it gracefully
                error_message = (
                    f"Index {new_index} has no vacancies. Swap cannot proceed."
                )
                update_module_status(redis_db, session_id, idx, error_message)

                # Click the 'Back To Timetable' button
                back_button = driver.find_element(
                    By.XPATH, "//input[@type='submit' and @value='Back to Timetable']"
                )
                back_button.click()

                return False, error_message

        except Exception as e:
            # Catch any unexpected errors
            error_message = (
                f"Unexpected error while checking new index {new_index}: {e!s}"
            )
            update_overall_swap_status(
                redis_db, session_id, status="Error", message=error_message
            )

            # Click the 'Back To Timetable' button
            back_button = driver.find_element(
                By.XPATH, "//input[@type='submit' and @value='Back to Timetable']"
            )
            back_button.click()

            return False, error_message

        # 8) Click 'OK'
        ok_button2 = driver.find_element(
            By.XPATH, "//input[@type='submit' and @value='OK']"
        )
        ok_button2.click()

        # Catch Module Clash error with other existing modules
        try:
            WebDriverWait(driver, 5).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()  # Close the alert
            update_overall_swap_status(
                redis_db, session_id, status="Error", message=alert_text
            )

            # Click the 'Back To Timetable' button
            back_button = driver.find_element(
                By.XPATH, "//input[@type='submit' and @value='Back to Timetable']"
            )
            back_button.click()

            return False, alert_text
        except TimeoutException:
            pass  # If no alert, proceed to the swap index page

        """
        Confirm Swap Index page after choosing the mod and index you want to swap
        """

        # 9) Wait for the confirm swap index page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[@id='top']/div/section[2]/div/div/form[1]")
            )
        )

        # 10) Click the 'Confirm to Change Index Number' button
        confirm_change_button = driver.find_element(
            By.XPATH,
            "//input[@type='submit' and @value='Confirm to Change Index Number']",
        )
        confirm_change_button.click()

        # 11) Wait for the official changed index alert to pop up and click OK
        WebDriverWait(driver, 10).until(EC.alert_is_present())

        alert = driver.switch_to.alert
        log_swap_event(
            session_id, username, f"Attempt {attempt_number}: Alert = {alert.text}"
        )
        alert.accept()  # Accept (click OK) on the alert

        update_module_status(
            redis_db,
            session_id,
            idx,
            f"Successfully swapped {old_index} -> {new_index}",
            success=True,
        )
        return True, ""  # Successful swap, no error message

    except SessionNotCreatedException:
        error_message = "Session expired. Re-logging in..."
        update_module_status(redis_db, session_id, idx, error_message)
        return False, error_message

    except Exception as e:
        error_message = (
            f"Error during swap attempt for {old_index} -> {new_index}: {e!s}"
        )
        update_module_status(redis_db, session_id, idx, error_message)
        return False, error_message

    finally:
        pass


# ============================================================================
# PYDANTIC MODELS FOR REQUEST VALIDATION
# ============================================================================


class LoginRequest(BaseModel):
    username: str
    password: str
    num_modules: int


class SwapRequest(BaseModel):
    old_index: str
    new_index: str
    session_id: str


# ============================================================================
# APPLICATION SETUP
# ============================================================================


def create_app():
    """Create and configure the FastAPI application."""
    # Initialize components first
    initialize_components()

    # Initialize FastAPI app
    app = FastAPI()

    # Generate a random secret key for session encryption
    SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    app.add_middleware(
        SessionMiddleware,
        secret_key=SECRET_KEY,
        same_site="none",
        https_only=True,
        max_age=config.SESSION_TTL_SECONDS,
    )

    # CORS configuration for React development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",  # React dev server (port 3000)
            "https://ntu-add-drop-automator.vercel.app",  # Vercel frontend domain
            "https://ntu-add-drop-automator-*.vercel.app",  # Allow all Vercel preview deployments
            # "https://ntu-add-drop-automator-v2-backend.onrender.com",  # Render backend domain
            # "https://www.ntu-add-drop-automator.site" # Custom domain name
        ],
        allow_credentials=True,  # Important for session cookies
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ========================================================================
    # ROUTE DEFINITIONS
    # ========================================================================

    # API route to check for which page to render on the frontend.
    """@app.get('/api/check-period')
    async def api_check_period():
        now = datetime.now()
        current_month = now.month
        is_active = current_month in (1, 8)

        if not is_active:
            eligible_month = "August" if current_month < 8 else "January"
            eligible_year = now.year if current_month < 8 else now.year + 1

            return {
                "is_active": False,
                "next_period": {
                    "month": eligible_month,
                    "year": eligible_year
                }
            }

        return {"is_active": True}"""

    @app.get("/")
    async def root():
        return {"message": "NTU Add-Drop Automator Backend API", "status": "running"}

    # API route to process first form submission page on the frontend (username, password, numModules)
    @app.post("/api/login")
    async def api_login(
        request: Request, login_data: LoginRequest, redis_db=Depends(get_redis)
    ):
        if not validate_login(login_data.username, login_data.password):
            raise HTTPException(status_code=400, detail="Invalid credentials")

        # Create secure session in Redis
        session_id = await create_secure_session_async(
            redis_db, login_data.username, login_data.password
        )

        # Store only session ID and authentication status in session
        request.session["session_id"] = session_id
        request.session["authenticated"] = True

        return {"success": True, "message": "Login successful"}

    # API route to process module indexes (second form submission page on the frontend - [old_index, new_indexes] for each module)
    @app.post("/api/submit-swap")
    async def api_submit_swap(request: Request, redis_db=Depends(get_redis)):
        try:
            form_data = await request.json()

            # Get session ID from session
            session_id = request.session.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="No active session")

            # Get decrypted credentials from Redis
            username, password = await get_decrypted_credentials_async(
                redis_db, session_id
            )

            # Validate credentials again for security
            if not validate_login(username, password):
                raise HTTPException(status_code=401, detail="Invalid credentials")

            # Parse module data
            num_modules = form_data.get("num_modules", 0)
            module_data = form_data.get("modules", [])

            if num_modules <= 0 or len(module_data) != num_modules:
                raise HTTPException(status_code=400, detail="Invalid module data")

            # Parse modules into swap_items
            swap_items = []  # List to store (old_index, new_indexes, swapped)
            for i, module in enumerate(module_data):
                old_index = module.get("old_index")
                new_indexes_raw = module.get("new_indexes")

                if not old_index or not new_indexes_raw:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Missing or invalid data for module {i + 1}",
                    )

                new_index_list = [
                    index.strip()
                    for index in new_indexes_raw.split(",")
                    if index.strip()
                ]

                swap_items.append(
                    {
                        "old_index": old_index,
                        "new_indexes": new_index_list,
                        "swapped": False,
                    }
                )

            # Initialize swap data in the existing session
            await initialize_swap_in_session_async(redis_db, session_id, swap_items)

            # Start background thread to execute the swap operation. It gets the
            # shared SYNC Redis client (not the async `redis_db` used above) —
            # the thread is a plain OS thread, not a coroutine, so it can't await.
            thread = threading.Thread(
                target=perform_swaps,
                args=(username, password, swap_items, session_id, redis_sync_client),
                daemon=True,
            )
            thread.start()

            return {
                "success": True,
                "session_id": session_id,
                "message": "Swap process started successfully",
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Swap initiation failed: {e!s}"
            )

    # API route for returning swap_status data to the frontend
    @app.get("/api/swap-status/{session_id}")
    async def api_get_swap_status(session_id: str, redis_db=Depends(get_redis)):
        """Get swap status from session data in Redis"""
        try:
            session_data = await get_secure_session_async(redis_db, session_id)

            # Return swap-specific data
            return {
                "status": session_data.get("swap_status", "Idle"),
                "message": session_data.get("swap_message"),
                "details": session_data.get("modules", []),
                "started_at": session_data.get("swap_started_at"),
                "attempt_count": session_data.get("attempt_count", 0),
                "last_attempt_at": session_data.get("last_attempt_at"),
                "phase": session_data.get("swap_phase", "active"),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error retrieving status: {e!s}"
            )

    # API route for stopping swap operation
    @app.post("/api/stop-swap/{session_id}")
    async def api_stop_swap(
        session_id: str, request: Request, redis_db=Depends(get_redis)
    ):
        """Stop swap and clean up session"""
        try:
            # Update swap status to stopped
            await update_overall_swap_status_async(
                redis_db, session_id, "Stopped", "Swap stopped by user", phase="done"
            )

            # Clean up session
            await redis_db.delete(f"session:{session_id}")
            request.session.clear()

            return {"success": True, "message": "Swap successfully stopped"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error stopping swap: {e!s}")

    # API route for logging out once swap is done
    @app.post("/api/logout/{session_id}")
    async def api_logout(
        session_id: str, request: Request, redis_db=Depends(get_redis)
    ):
        """Clean logout after swap is done"""
        try:
            # Clean up session
            await redis_db.delete(f"session:{session_id}")
            request.session.clear()

            return {"success": True, "message": "Logged out successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error stopping swap: {e!s}")

    # API route to test storage backend connectivity
    @app.get("/api/health")
    async def health_check(redis_db=Depends(get_redis)):
        try:
            # Test storage backend connection (Redis, or the in-memory
            # stand-in — see config.STORAGE_BACKEND)
            await redis_db.ping()
            return {
                "status": "healthy",
                "storage_backend": config.STORAGE_BACKEND,
                "redis": "connected",  # kept for backwards compatibility
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "storage_backend": config.STORAGE_BACKEND,
                "redis": f"disconnected: {e!s}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    # Testing redis route (for my own usage)
    @app.get("/test-redis")
    async def test_redis(redis_db=Depends(get_redis)):
        try:
            await redis_db.set("test_key", "Hello, Redis!")
            value = await redis_db.get("test_key")
            return {"message": "Redis is working!", "retrieved_value": value}
        except Exception as e:
            return {"error": f"Redis connection error: {e!s}"}

    # Move this to frontend
    """@app.get('/thumbnail')
    async def serve_thumbnail():
        # Serve the image from the "static" directory
        image_path = os.path.join("static", "thumbnail.jpg")

        # Ensure the file exists
        if not os.path.exists(image_path):
            raise HTTPException(status_code=404, detail="Thumbnail not found")

        return FileResponse(image_path, media_type="image/jpeg")"""

    return app


# ============================================================================
# MAIN FUNCTION
# ============================================================================

# Initialize app at the module level
app = create_app()


def main():
    """Start the server when running directly."""
    port = int(os.environ.get("PORT", "5000"))  # Use Render's PORT env var
    print(f"Starting server on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
