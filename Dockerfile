# ---- base image ----
FROM python:3.11-slim

# ---- system dependencies ----
# chromium + chromedriver are the headless browser stack used by Selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        xvfb \
        xauth \
        tini \
    && rm -rf /var/lib/apt/lists/*

# ---- working directory ----
WORKDIR /app

# ---- Python dependencies (cached layer) ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- application code ----
COPY main.py config.py hardcover.py goodreads.py storygraph.py driver.py \
     cookie_bundle.py matching.py sync_result.py sync_state.py \
     container_entrypoint.py ./

# ---- non-root user ----
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser
RUN mkdir /app/state

# ---- runtime config ----
ENV PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_HEADLESS=0 \
    CHROME_NO_SANDBOX=1 \
    DISPLAY=:99

ENTRYPOINT ["/usr/bin/tini", "-g", "-s", "--", "python", "-u", "container_entrypoint.py"]
CMD ["python", "-u", "main.py"]
