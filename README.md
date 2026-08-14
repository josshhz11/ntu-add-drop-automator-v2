# NTU Add-Drop Automator v2

This project is an automation tool designed for Nanyang Technological University (Singapore), benefiting over 30,000 students. It utilizes a Redis cache database database to store all session swap information (with credentials encrypted and stored for no longer than 2 hours), a FastAPI application to create a RESTful API for the backend, and a ReactJS frontend for intuitive user interaction.

## Visit The Site

Feel free to check out the [project here!](https://ntu-add-drop-automator.vercel.app/)

<img width="1301" alt="NTU Add-Drop Automator Home Page" src="https://github.com/josshhz11/ntu-add-drop-automator-v3/blob/main/assets/NTU-Add-Drop-Automator-Home-Page.png">

## Run It Locally — No Setup Required (Windows)

Prefer to run everything on your own computer instead of using the hosted site? There's a standalone version that needs nothing installed except Google Chrome — no Python, no Node, no Docker, no Redis, no config files.

1. Go to the [Releases page](https://github.com/josshhz11/ntu-add-drop-automator-v2/releases) and download `NTU-AddDrop-Automator.exe` (Windows) from the latest release.
2. Double-click it.
3. A console window will open (this is normal — it's just showing status/log output), and a few seconds later your default browser will automatically open to the app.
4. Log in with your real NTU credentials and submit your swap request exactly as you would on the hosted site.

That's it — no accounts to create, no servers to configure. Your credentials and swap data stay entirely on your own machine and are never sent anywhere except NTU's own portal.

**The only prerequisite is having Google Chrome already installed.** If it isn't, the app will tell you so clearly instead of crashing.

**To stop it**, just close the console window — this shuts down the server and cancels any swap attempt in progress.

**Advanced/power users**: settings like how long a swap session keeps retrying, or how many browser instances run in parallel, can be customized by creating a `config.env` file in the same folder as the `.exe` (see `backend/config.py` for the full list of available settings and their defaults). Most users will never need this — the defaults are tuned for a single local user.

*macOS/Linux builds are produced by the same pipeline but haven't been verified on real hardware yet — check the [Releases page](https://github.com/josshhz11/ntu-add-drop-automator-v2/releases) for availability.*

## Features

- **Redis Database:** Stores detailed information on each user's NTU portal credentials (encrypted) and swap sessions (including unique session ID, old and new indexes, and swap status).
- **FastAPI Backend:** Provides a high-performance RESTful API to manage asynchronous, periodic swap execution efficiently. The backend is packaged as a Dockerfile and hosted on Render/Heroku.
- **ReactJS Frontend:** A user-friendly interface for keying in user credentials, swap information, and viewing swap statuses. The frontend is hosted on Vercel.

## Prerequisites

Before running this project locally, ensure you have the following installed:

- Python 3.9 or higher
- Node.js and npm (Node Package Manager)
- Redis database (or Docker for Redis container)
- Chrome browser (for Selenium automation)
- IDE (VS Code, PyCharm, etc.)

## Installation

### Backend Setup

1. Clone this repository.
2. Navigate to the `backend` directory in your terminal.
3. Create a virtual environment: `python -m venv venv`
4. Activate the virtual environment:
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`
5. Install dependencies: `pip install -r requirements.txt`
6. Configure the `.env` file with your Redis database credentials.
7. Install Chrome and ChromeDriver for Selenium automation.
8. Run the FastAPI application using `uvicorn app:app --reload`.

### Frontend Setup

1. Navigate to the `frontend` directory in your terminal.
2. Run `npm install` to install the necessary dependencies.
3. Update the `src/config/api.js` file with the appropriate backend API URL.
4. Run `npm start` to start the ReactJS application.

### Local Development with Docker

1. Start Redis using Docker:
   ```bash
   docker run --name redis-local -p 6379:6379 -d redis:latest
   ```
2. Follow the Backend and Frontend setup steps above.

## Usage

- Access the frontend application via `http://localhost:3000`.
- The FastAPI backend runs on `http://localhost:8000` by default.
- Use the provided API endpoints to perform course swap operations:
  - `/api/login` - POST user authentication with NTU credentials.
  - `/api/submit-swap` - POST to start automated course swap process.
  - `/api/swap-status/{sessionId}` - GET real-time swap progress and status.
  - `/api/stop-swap/{sessionId}` - POST to stop ongoing swap process.
  - `/api/health` - GET application health check.

## API Documentation

### Authentication
- **POST** `/api/login`
  - Body: `{"username": "ntu_username", "password": "ntu_password", "num_modules": 2}`
  - Response: `{"success": true, "session_id": "unique_session_id", "num_modules": 2}`

### Course Swapping
- **POST** `/api/submit-swap`
  - Body: `{"num_modules": 2, "modules": [{"old_index": "12345", "new_indexes": "12346,12347"}]}`
  - Response: `{"success": true, "session_id": "session_id", "message": "Swap process started"}`

### Status Monitoring
- **GET** `/api/swap-status/{sessionId}`
  - Response: `{"status": "Processing", "message": "Attempting swap...", "details": [...]}`

### Interactive API Documentation
FastAPI provides automatic interactive API documentation:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## Contributing

Contributions are welcome! If you'd like to enhance this project or report issues, please submit a pull request or open an issue.

## License

This project is for educational purposes only. Please use responsibly and in accordance with NTU's terms of service.
