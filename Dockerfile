# ---- base image ----
FROM python:3.11-slim

# ---- system dependencies ----
# chromium + chromedriver are the headless browser stack used by Selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# ---- working directory ----
WORKDIR /app

# ---- Python dependencies (cached layer) ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- application code ----
COPY main.py hardcover.py goodreads.py storygraph.py driver.py ./

# ---- non-root user ----
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser
RUN mkdir /app/state

# ---- runtime config ----
ENV PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

CMD ["python", "main.py"]
