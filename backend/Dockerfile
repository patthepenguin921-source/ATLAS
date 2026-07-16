FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# tesseract-ocr powers text extraction from uploaded photos/scans (pytesseract).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium for Playwright — used as a last-resort fallback when a district's
# PowerSchool login can't be automated with a plain HTTP client (see
# app/integrations/powerschool_browser.py). --with-deps installs the OS
# packages Chromium needs (fonts, libgbm, etc.) alongside the browser itself.
# This meaningfully increases image size/memory use; bump the Cloud Run
# service's memory limit if the container starts getting OOM-killed.
RUN python -m playwright install --with-deps chromium

COPY app ./app

EXPOSE 8000
# Cloud Run injects $PORT (8080) and requires the container to listen on it;
# default to 8000 for docker-compose/local runs where $PORT isn't set.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
