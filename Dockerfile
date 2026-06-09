# Flight Meetup Finder — container image.
# Uses the official Playwright Python image so Chromium and its system
# dependencies are already baked in (fast-flights local fetch mode needs them).
# The -noble tag ships Python 3.12 (the project's target); the -jammy tag of
# the same Playwright version ships Python 3.10, which lacks datetime.UTC.
# Created 2026-06-09.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code (includes templates + static assets under app/).
COPY app ./app

# SQLite lives on a mounted volume at /data; owned by the non-root pwuser
# that the Playwright base image provides.
RUN mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
