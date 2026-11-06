FROM python:3.12-slim

# Tesseract for OCR: English and German (P-04).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# Dependencies first (cached layer), including the spaCy model pinned in uv.lock.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY adjud ./adjud
COPY ui ./ui
COPY tools ./tools
RUN uv sync --frozen --no-dev

ENV ADJUD_DATA_DIR=/app/data
