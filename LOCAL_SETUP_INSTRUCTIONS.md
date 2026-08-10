Use Docker Desktop for Redis, then run the backend and frontend locally in two separate terminals. The only caveat is that the current backend is configured with secure cookies and hardcoded Chrome/ChromeDriver paths, so I’ll show the exact run steps first and then call out the one local setting that may need adjustment if the browser session does not stick on plain http://localhost.

**1. Start Redis in Docker Desktop**

Open PowerShell and run:

```powershell
docker run --name redis-local -p 6379:6379 -d redis:latest
```

Check that it is alive:

```powershell
docker ps
```

Optional sanity check:

```powershell
docker exec -it redis-local redis-cli ping
```

Expected output:

```text
PONG
```

If you ever need to stop it later:

```powershell
docker stop redis-local
```

To start it again:

```powershell
docker start redis-local
```

**2. Set up the backend**

Open a new PowerShell window and go into the backend folder:

```powershell
cd "c:\Users\joshua\OneDrive\Documents\GitHub\ntu-add-drop-automator-v2\backend"
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once in that terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Create a backend .env file in the backend folder with at least:

```powershell
REDIS_HOST=localhost
REDIS_PORT=6379
ENCRYPTION_KEY=put_a_valid_fernet_key_here
SECRET_KEY=any_long_random_string
PORT=5000
```

You need a valid Fernet key for ENCRYPTION_KEY. If you do not already have one, tell me and I can give you the exact command to generate it.

Now run the backend:

```powershell
python app.py
```

If you prefer Uvicorn directly:

```powershell
uvicorn app:app --host 0.0.0.0 --port 5000 --reload
```

Useful backend checks:

```powershell
curl http://localhost:5000/
curl http://localhost:5000/api/health
curl http://localhost:5000/test-redis
```

**3. Set up the frontend**

Open another PowerShell window and go into the frontend folder:

```powershell
cd "c:\Users\joshua\OneDrive\Documents\GitHub\ntu-add-drop-automator-v2\frontend"
```

Install frontend dependencies:

```powershell
npm install
```

Start the React app:

```powershell
npm start
```

Then open:

```text
http://localhost:3000
```

The frontend is already configured to call the backend at http://localhost:5000 in development via [frontend/src/config/api.js](frontend/src/config/api.js).

**4. Expected user flow once everything is up**

1. Open the frontend at http://localhost:3000.
2. Enter NTU username, password, and number of modules.
3. Submit the login form.
4. Enter old index and new index candidates.
5. Submit the swap request.
6. Watch the status page poll Redis through the backend every 5 seconds.

That flow is implemented in [frontend/src/components/HomePage/HomePage.js](frontend/src/components/HomePage/HomePage.js), [frontend/src/components/InputIndex/InputIndex.js](frontend/src/components/InputIndex/InputIndex.js), and [frontend/src/components/SwapStatus/SwapStatus.js](frontend/src/components/SwapStatus/SwapStatus.js).

**5. Important local caveat you should know now**

Your current backend uses SessionMiddleware with secure cookie settings, which is great for HTTPS production but can be awkward on plain localhost HTTP. In practical terms, if the login/session does not persist locally, that is probably why.

So if the app does not behave correctly after the steps above, the first thing to check is whether the browser cookie is being blocked because the backend expects HTTPS. The local workaround is usually one of these:

1. Temporarily relax the cookie settings for local development.
2. Run the frontend/backend behind local HTTPS.
3. Use the production deployment pattern instead of raw localhost.

Also, Selenium depends on Chrome and ChromeDriver being installed and reachable. The backend code currently uses hardcoded Windows paths, so make sure these exist on your machine:

- Chrome at the default Google Chrome location
- ChromeDriver at the exact path expected by the backend

If they are elsewhere, the backend will start but the swap worker will fail when it tries to launch ChromeDriver.

**6. Fastest verification sequence**

Run these in order:

```powershell
docker start redis-local
```

```powershell
cd "c:\Users\joshua\OneDrive\Documents\GitHub\ntu-add-drop-automator-v2\backend"
.\.venv\Scripts\Activate.ps1
python app.py
```

In another terminal:

```powershell
cd "c:\Users\joshua\OneDrive\Documents\GitHub\ntu-add-drop-automator-v2\frontend"
npm start
```

Then browse to http://localhost:3000 and test the flow.

If you want, I can next give you a precise “copy-paste setup checklist” for Windows, including how to generate the Fernet key, how to create the backend .env file, and how to confirm ChromeDriver is installed correctly.
