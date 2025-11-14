import json
import asyncio
import threading
import redis
import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, SessionNotCreatedException
from dotenv import load_dotenv
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import subprocess
from starlette.middleware.sessions import SessionMiddleware
import secrets
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import uuid
import platform

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
        print(f"Error checking paths: {str(e)}")

def setup_redis_connection():
    """Set up and return Redis connection."""
    def get_redis():
        """Dependency Injection: Returns a Redis connection"""
        return redis.StrictRedis(
            host=os.environ.get("REDIS_HOST", "red-cug9uopopnds7398r2kg"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD", None),
            decode_responses=True
        )
    return get_redis

# Configure ChromeDriver settings (Manual for the Windows path configurations)
CHROME_BINARY_PATH = "/usr/bin/google-chrome" if platform.system() == "Linux" else "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
CHROMEDRIVER_PATH = "/usr/local/bin/chromedriver" if platform.system() == "Linux" else "C:\\Users\\joshua\\Downloads\\chromedriver-win64\\chromedriver.exe"

def setup_chrome_options():
    """Configure and return Chrome options."""
    chrome_options = Options()
    chrome_options.binary_location = CHROME_BINARY_PATH
    chrome_options.add_argument("--headless")  # Headless mode
    chrome_options.add_argument("--disable-gpu")  # Fixes rendering issues
    chrome_options.add_argument("--no-sandbox")  # Required for running as root
    chrome_options.add_argument("--disable-dev-shm-usage")  # Fix shared memory issues
    chrome_options.add_argument("--remote-debugging-port=9222")  # Enables debugging
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
        print(f"Error creating WebDriver: {str(e)}")
        raise

def setup_driver_pool(chrome_options, max_drivers=1):
    """Set up and return driver pool functions."""
    driver_pool = []
    pool_lock = threading.Lock()

    # Preload ChromeDriver instances
    for _ in range(max_drivers):
        driver_pool.append(create_driver(chrome_options))


    def get_driver():
        with pool_lock:
            if driver_pool:
                return driver_pool.pop()
            else:
                print("Creating new ChromeDriver instance...")
                try:
                    driver = create_driver(chrome_options)
                    if driver:
                        print("ChromeDriver started successfully!")
                    else:
                        print("Failed to start ChromeDriver.")
                    return driver
                except Exception as e:
                    print(f"Error starting ChromeDriver: {str(e)}")
                    return None
            
    # Return driver to the pool
    def release_driver(driver):
        with pool_lock:
            driver_pool.append(driver)

    return get_driver, release_driver

# ============================================================================
# GLOBAL VARIABLES (Will be initialized in main())
# ============================================================================

get_redis = None
get_driver = None
release_driver = None

def initialize_components():
    """Initialize all components needed by the app."""
    global get_redis, get_driver, release_driver

    print("Loading environment variables...")
    load_dotenv()

    print("Checking Chrome installations...")
    check_chrome_paths()

    print("Setting up Redis connection...")
    get_redis = setup_redis_connection()

    print("Setting up Chrome WebDriver pool...")
    chrome_options = setup_chrome_options()
    get_driver, release_driver = setup_driver_pool(chrome_options, max_drivers=1)

# ============================================================================
# UTILITY FUNCTIONS FOR REDIS AND STATUS
# ============================================================================

def set_status_data(redis_db, swap_id, data):
    redis_db.set(swap_id, json.dumps(data))

def get_status_data(redis_db, swap_id):
    data = redis_db.get(swap_id)
    if data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            print(f"Error decoding JSON for swap_id: {swap_id}")
            return {"status": "error", "details": [], "message": "Error retrieving status data"}
    return {"status": "idle", "details": [], "message": None}

def update_status(redis_db, swap_id, idx, message, success=False):
    """
    Updates the status of a specific module swap in Redis.

    Args:
        redis_db: Redis database connection.
        swap_id (str): Unique swap session ID.
        idx (int): Index of the module in the details list.
        message (str): Message to update in the status.
        success (bool): Whether the swap was successful.
    """
    status_data = get_status_data(redis_db, swap_id)

    if idx < len(status_data["details"]):
        status_data["details"][idx]["message"] = message
        if success:
            status_data["details"][idx]["swapped"] = True
        redis_db.set(swap_id, json.dumps(status_data))

def update_overall_status(redis_db, swap_id, status, message):
    """
    Updates the overall status and message of the swap operation in Redis.

    Args:
        redis_db: Redis connection (injected via FastAPI).
        swap_id (str): Unique swap session ID.
        status (str): The overall status to set (e.g., "Error", "Completed").
        message (str): The overall message to set.
    """
    status_data = get_status_data(redis_db, swap_id)  # Fetch current status
    status_data["status"] = status  # Update overall status
    status_data["message"] = message  # Update overall message
    set_status_data(redis_db, swap_id, status_data)  # Save changes back to Redis

# ============================================================================
# VALIDATION AND HELPER FUNCTIONS
# ============================================================================

# Login Validation: No session, direct request-based validation
def validate_login(username: str, password: str):
    return bool(username and password)

def get_base_og_data(title_suffix=""):
    base_title = "NTU Add-Drop Automator"
    return {
        "base_title": base_title,
        "title": f"{base_title}{' - ' + title_suffix if title_suffix else ''}",
        "description": "Helping NTU students automate add-drop swapping.",
        "image": "https://ntu-add-drop-automator.site/static/thumbnail.jpg",
        "url": "https://ntu-add-drop-automator.site/",
        "type": "website"
    }

# ============================================================================
# SELENIUM AUTOMATION FUNCTIONS
# ============================================================================

def login_to_portal(driver, username, password, swap_id, redis_db):
    """
    Log in to the NTU portal.
    """
    url = 'https://wish.wis.ntu.edu.sg/pls/webexe/ldap_login.login?w_url=https://wish.wis.ntu.edu.sg/pls/webexe/aus_stars_planner.main'
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
        lambda d: d.current_url in [
            "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.planner",
            "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.time_table"
        ]
    )
        
        # Check if redirected to the time_table URL
        if driver.current_url == "https://wish.wis.ntu.edu.sg/pls/webexe/AUS_STARS_PLANNER.time_table":
            # Check for the "Plan/ Registration" button
            try:
                plan_button = driver.find_element(By.XPATH, "//input[@value='Plan/ Registration']")
                plan_button.click()
            except Exception:
                error_message = "Unable to find or click the 'Plan/ Registration' button."
                update_overall_status(redis_db, swap_id, status="Error", message=error_message)
                return False
          
        # Proceed to wait for the table if on the planner page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//table[@bordercolor='#E0E0E0']"))
        )
    # If login fails, print exception
    except Exception:
        # If login fails, update status and exit
        error_message = "Incorrect username/password. Please try again."
        update_overall_status(redis_db, swap_id, status="Error", message=error_message)
        return False

def perform_swaps(username, password, swap_items, swap_id, redis_db):
    driver = None

    try:
        # Browser setup
        driver = get_driver()
        login_to_portal(driver, username, password, swap_id, redis_db)

        start_time = time.time()
        while True:
            for idx, item in enumerate(swap_items):
                if not item["swapped"]:
                    failed_indexes = []
                    for new_index in item["new_indexes"]:
                        try:
                            success, message = attempt_swap(
                                old_index=item["old_index"],
                                new_index=new_index,
                                idx=idx,
                                driver=driver,
                                swap_id=swap_id,
                                redis_db=redis_db
                            )
                            if success:
                                item["swapped"] = True
                                update_status(
                                    redis_db,
                                    swap_id,
                                    idx,
                                    message=f"Successfully swapped index {item['old_index']} to {item['new_index']}.",
                                    success=True
                                )
                                break
                            else:
                                failed_indexes.append(new_index)
                        except WebDriverException as e:
                            print(f"WebDriver error: {e}")
                            release_driver(driver) # Release current driver
                            driver = get_driver() # Get a new driver
                            login_to_portal(driver, username, password, swap_id, redis_db)
                            failed_indexes.append(new_index)
                        except Exception as e:
                            error_message = f"Error during swap attempt: {e}"
                            update_status(redis_db, swap_id, idx, message=error_message)
                            failed_indexes.append(new_index)
                    if not item["swapped"] and failed_indexes:
                        update_status(
                            redis_db,
                            swap_id,
                            idx,
                            message=f"Index {', '.join(failed_indexes)} have no vacancies."
                        )
            # Check if all items are swapped
            all_swapped = all(item["swapped"] for item in swap_items)
            if all_swapped:
                update_overall_status(redis_db, swap_id, status="Completed", message=f"All modules have been successfully swapped.")
                break

            if time.time() - start_time >= 2 * 3600:
                update_overall_status(redis_db, swap_id, status="Timed Out", message="Time limit reached before completing the swap.")
                print("Time limit reached before completing the swap.")
                break

            time.sleep(5 * 60)
    except Exception as e:
        update_overall_status(redis_db, swap_id, status="Error", message=f"An error occurred: {str(e)}")
        print(f"An error occurred: {str(e)}")
    finally:
        if driver:
            release_driver(driver) # Ensure driver is released back to the pool

async def attempt_swap(old_index, new_index, idx, driver, swap_id, redis_db):
    """
    Performs swap attempt, updates Redis status, and returns success status.
    
    Args:
        old_index (str): The current course index.
        new_index (str): The desired new course index.
        idx (int): The index in the swap list (for status tracking).
        driver (webdriver.Chrome): Selenium WebDriver instance.
        swap_id (str): Unique swap session ID.
        redis_db: Redis connection (Injected via FastAPI).
    
    Returns:
        (bool, str): Tuple with success status and message.
    """    
    try:
        update_status(redis_db, swap_id, idx, f"Attempting to swap {old_index} -> {new_index}")

        # 1) Wait for the table element to appear on the main page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//table[@bordercolor='#E0E0E0']"))
        )

        # 2) Locate the radio button for old_index by its value attribute and click it.
        try:
            # Wait for the radio button to be present
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, f"//input[@type='radio' and @value='{old_index}']"))
            )
            
            # Locate and click the radio button
            radio_button = driver.find_element(By.XPATH, f"//input[@type='radio' and @value='{old_index}']")
            radio_button.click()

        except TimeoutException:
            # If the radio button is not found within the timeout period
            error_message = f"Old index  {old_index} not found. Swap cannot proceed."
            update_status(redis_db, swap_id, idx, error_message)
            update_overall_status(redis_db, swap_id, status="Error", message=error_message)
            return False, error_message  # Return a value indicating failure

        except Exception as e:
            # Handle any unexpected errors
            error_message = f"Unexpected error locating radio button for index {old_index}: {str(e)}"
            update_status(redis_db, swap_id, idx, error_message)
            return False, error_message  # Return a value indicating failure

        # 3) Select the "Change Index" option from the dropdown
        dropdown = Select(driver.find_element(By.NAME, "opt"))
        dropdown.select_by_value("C")

        # 4) Click the 'Go' button
        header = driver.find_element(By.CLASS_NAME, "site-header__body")
        driver.execute_script("arguments[0].style.visibility = 'hidden';", header)  # Hide the header
        go_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Go']")
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
            update_overall_status(redis_db, swap_id, status="Error", message="Portal is closed now. Please try again from 10:30am - 10:00pm.")
            return False
        except TimeoutException:
            pass # If no alert, proceed to the swap index page

        # 6) Wait for the swap index page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "AUS_STARS_MENU"))
        )

        # 7) Check if the new index exists and has vacancies
        try:
            # Locate the dropdown for selecting the new index
            dropdown_element = driver.find_element(By.NAME, "new_index_nmbr")
            
            # Locate the option for the new index
            options = dropdown_element.find_elements(By.XPATH, f".//option[@value='{new_index}']")
            
            if not options:
                # If the desired new index is not in the dropdown, handle the error
                error_message = f"New Index {new_index} was not found in the dropdown options. Swap cannot proceed."
                update_status(redis_db, swap_id, idx, error_message)
                
                # Click the 'Back To Timetable' button
                back_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Back to Timetable']")
                back_button.click()
                
                return False, error_message  # Return a value indicating failure

            # Parse the vacancies from the option text (e.g., "01172 / 9 / 1")
            option_text = options[0].text
            try:
                vacancies = int(option_text.split(" / ")[1])  # Parse out the middle number (vacancies)
                print(f"The number of vacancies for index {new_index} is {vacancies}.")
            except (IndexError, ValueError) as e:
                error_message = f"Failed to parse vacancies for index {new_index}: {str(e)}"
                update_status(redis_db, swap_id, idx, error_message)

                # Click the 'Back To Timetable' button
                back_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Back to Timetable']")
                back_button.click()

                return False, error_message

            # Select the new index in the dropdown
            select_dropdown = Select(dropdown_element)
            select_dropdown.select_by_value(new_index)

            if vacancies <= 0:
                # If there are no vacancies, handle it gracefully
                error_message = f"Index {new_index} has no vacancies. Swap cannot proceed."
                update_status(redis_db, swap_id, idx, error_message)

                # Click the 'Back To Timetable' button
                back_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Back to Timetable']")
                back_button.click()

                return False, error_message

        except Exception as e:
            # Catch any unexpected errors
            error_message = f"Unexpected error while checking new index {new_index}: {str(e)}"
            update_overall_status(redis_db, swap_id, status="Error", message=error_message)

            # Click the 'Back To Timetable' button
            back_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Back to Timetable']")
            back_button.click()
            
            return False, error_message

        # 8) Click 'OK'
        ok_button2 = driver.find_element(By.XPATH, "//input[@type='submit' and @value='OK']")
        ok_button2.click()
        
        # Catch Module Clash error with other existing modules
        try:
            WebDriverWait(driver, 5).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()  # Close the alert
            update_overall_status(redis_db, swap_id, status="Error", message=alert_text)

            # Click the 'Back To Timetable' button
            back_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Back to Timetable']")
            back_button.click()
            
            return False, alert_text
        except TimeoutException:
            pass # If no alert, proceed to the swap index page

        """
        Confirm Swap Index page after choosing the mod and index you want to swap
        """

        # 9) Wait for the confirm swap index page
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[@id='top']/div/section[2]/div/div/form[1]"))
        )

        # 10) Click the 'Confirm to Change Index Number' button 
        confirm_change_button = driver.find_element(By.XPATH, "//input[@type='submit' and @value='Confirm to Change Index Number']")
        confirm_change_button.click()

        # 11) Wait for the official changed index alert to pop up and click OK
        WebDriverWait(driver, 10).until(
            EC.alert_is_present()
        )

        alert = driver.switch_to.alert
        print(f"Alert text: {alert.text}")
        alert.accept()      # Accept (click OK) on the alert

        update_status(redis_db, swap_id, idx, f"Successfully swapped {old_index} -> {new_index}", success=True)
        return True, "" # Successful swap, no error message
    
    except SessionNotCreatedException as e:
        error_message = "Session expired. Re-logging in..."
        update_status(redis_db, swap_id, idx, error_message)
        return False, error_message

    except Exception as e:
        error_message = f"Error during swap attempt for {old_index} -> {new_index}: {str(e)}"
        update_status(redis_db, swap_id, idx, error_message)
        return False, error_message
    
    finally:
        pass

# ============================================================================
# PYDANTIC MODELS FOR REQUEST VALIDATION
# ============================================================================
class SwapRequest(BaseModel):
    old_index: str
    new_index: str
    swap_id: str

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
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

    # CORS confiuration for React development (TBC)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000", # React dev server (port 3000)
            "https://ntu-add-drop-automator.vercel.app", # Vercel frontend domain
            "https://ntu-add-drop-automator-v2-backend.onrender.com",  # Render backend domain
            # "https://www.ntu-add-drop-automator.site" # Custom domain name
        ],
        allow_credentials=True, # Important for session cookies
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static folder/files
    app.mount("/static", StaticFiles(directory="static"), name="static")

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

    @app.get('/')
    async def root():
        return {"message": "NTU Add-Drop Automator Backend API", "status": "running"}
    
    # API route to process first form submission page on the frontend (username, password, numModules)
    @app.post('/api/login')
    async def api_login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        num_modules: int = Form(...)
    ):
        if not validate_login(username, password):
            raise HTTPException(status_code=400, detail="Invalid credentials")
        
        # Store credentials in session
        request.session["username"] = username
        request.session["password"] = password

        return {"success": True, "message": "Login successful"}
    
    # API route to process module indexes (second form submission page on the frontend - [old_index, new_indexes] for each module)
    @app.post('/api/submit-swap')
    async def api_submit_swap(request: Request, redis_db=Depends(get_redis)):
        try: 
            form_data = await request.json()

            # Get credentials stored in the session
            username = request.session.get("username")
            password = request.session.get("password")

            # Should I just call this once? Maybe just here? and no need to do this in api_login (save on backend API calls - although we are still calling the backend, just not performing the login)
            if not validate_login(username, password):
                raise HTTPException(status_code=401, detail="Not authenticated")

            num_modules = form_data.get("num_modules", 0)
            module_data = form_data.get("modules", [])

            if num_modules <= 0 or len(module_data) != num_modules:
                raise HTTPException(status_code=400, detail="Invalid module data")

            # Parse modules into swap_items
            swap_items = [] # List to store (old_index, new_indexes, swapped)
            for i, module in enumerate(module_data):
                old_index = module.get("old_index")
                new_indexes_raw = module.get("new_indexes")

                if not old_index or not new_indexes_raw:
                    raise HTTPException(status_code=400, detail=f"Missing or invalid data for module {i+1}")
            
                new_index_list = [index.strip() for index in new_indexes_raw.split(",") if index.strip()]
                    
                swap_items.append({
                    "old_index": old_index,
                    "new_indexes": new_index_list,
                    "swapped": False
                })

            # Generate a unique swap id for this swap session
            swap_id = f"username_{int(time.time())}"

            # Initialize Redis with status data
            status_data = {
                "status": "Processing",
                "details": [
                    {"old_index": item["old_index"], 
                    "new_indexes": ", ".join(item["new_indexes"]), 
                    "swapped": False,
                    "message": "Pending..."}
                    for item in swap_items
                ],
                "message": "Your swap request is being processed." # Was previously None
            }
            redis_db.set(swap_id, json.dumps(status_data))
                
            # Start background thread for to execute the swap operation
            thread = threading.Thread(
                target=perform_swaps,
                args=(username, password, swap_items, swap_id, redis_db),
                daemon=True
            )
            thread.start()

            return {
                "success": True,
                "swap_id": swap_id,
                "message": "Swap process started successfully"
            }
        
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Swap initiation failed: {str(e)}")
    
    
    # API route for returning swap_status data to the frontend
    @app.get('/api/swap-status/{swap_id}')
    async def api_get_swap_status(swap_id: str, redis_db=Depends(get_redis)):
        # Get latest status from Redis
        status_data = get_status_data(redis_db, swap_id)

        return status_data

    # API route for stopping swap operation
    @app.post('/api/stop-swap/{swap_id}')
    async def api_stop_swap(swap_id: str, request: Request, redis_db=Depends(get_redis)):
        # Update Redis to stop the process
        status_data = get_status_data(redis_db, swap_id)
        status_data["status"] = "Stopped"
        set_status_data(redis_db, swap_id, status_data)

        # Clean up
        request.session.clear()
        redis_db.delete(swap_id)

        return {"success": True, "message": "Swap successfully stopped"}

    # Testing redis route (for my own usage)
    @app.get('/test-redis')
    async def test_redis(redis_db=Depends(get_redis)):
        try:
            redis_db.set("test_key", "Hello, Redis!")
            value = redis_db.get("test_key")
            return {"message": "Redis is working!", "retrieved_value": value}
        except Exception as e:
            return {"error": f"Redis connection error: {str(e)}"}

    @app.get('/thumbnail')
    async def serve_thumbnail():
        # Serve the image from the "static" directory
        image_path = os.path.join("static", "thumbnail.jpg")

        # Ensure the file exists
        if not os.path.exists(image_path):
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        
        return FileResponse(image_path, media_type="image/jpeg")

    return app

# ============================================================================
# MAIN FUNCTION
# ============================================================================

# Initialize app at the module level
app = create_app()

def main():
    """Start the server when running directly."""
    port = int(os.environ.get("PORT", 5000)) # Use Render's PORT env var
    print(f"Starting server on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()