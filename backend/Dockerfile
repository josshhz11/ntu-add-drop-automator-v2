# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Install necessary system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    gnupg \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    ca-certificates \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# 1. Add Google’s signing key using the recommended keyring method
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /usr/share/keyrings/google-chrome.gpg

# 2. Set up the Google Chrome repository using the keyring
RUN echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list

# 3. Install Google Chrome Stable
RUN apt-get update && apt-get install -y --no-install-recommends \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 4. Remove any existing ChromeDriver to avoid conflicts
RUN rm -f /usr/local/bin/chromedriver

# 5. Install ChromeDriver matching whatever Chrome version apt just installed
#    (looked up dynamically from Google's Chrome-for-Testing catalog), instead
#    of a hardcoded version number that silently drifts out of sync whenever
#    a future image rebuild picks up a newer "stable" Chrome from apt.
RUN CHROME_VERSION=$(google-chrome-stable --version | awk '{print $3}') \
    && echo "Detected installed Chrome version: ${CHROME_VERSION}" \
    && CHROMEDRIVER_URL=$(python3 -c "import json, urllib.request; data = json.load(urllib.request.urlopen('https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json')); versions = data['versions']; match = next((v for v in versions if v['version'] == '${CHROME_VERSION}'), None) or next((v for v in reversed(versions) if v['version'].split('.')[0] == '${CHROME_VERSION}'.split('.')[0]), versions[-1]); print(next(d['url'] for d in match['downloads']['chromedriver'] if d['platform'] == 'linux64'))") \
    && echo "Downloading chromedriver from: ${CHROMEDRIVER_URL}" \
    && wget -q "$CHROMEDRIVER_URL" -O /tmp/chromedriver.zip \
    && unzip -q /tmp/chromedriver.zip -d /tmp/ \
    && mv /tmp/chromedriver-*/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf /tmp/chromedriver-* /tmp/chromedriver.zip

# 6. Verify ChromeDriver version during build
RUN chromedriver --version

# 7. Set display (optional for headless operations)
ENV DISPLAY=:99

# 8. Copy requirements.txt and install Python dependencies
COPY requirements.txt /app/
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt

# 9. Copy your application code into the container
COPY . /app

# 10. Expose port 5000 for FastAPI
EXPOSE 5000

# 11. Command to start FastAPI with Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
