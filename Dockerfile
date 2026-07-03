FROM python:3.11-slim-bookworm

ARG INSTALL_OFFICE=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    DTEXTBOOKS_DATA=/data \
    DTEXTBOOKS_RAW=/data/raw \
    DTEXTBOOKS_WORK=/data/work_material1

WORKDIR /app

RUN set -eux; \
    packages="curl ffmpeg fonts-noto-cjk libmagic1 poppler-utils"; \
    if [ "$INSTALL_OFFICE" = "true" ]; then packages="$packages libreoffice"; fi; \
    for attempt in 1 2 3 4; do \
        apt-get update -o Acquire::Retries=5 \
        && apt-get install -y --download-only --no-install-recommends -o Acquire::Retries=5 --fix-missing $packages \
        && break; \
        if [ "$attempt" = "4" ]; then exit 1; fi; \
        apt-get clean; \
        rm -rf /var/lib/apt/lists/*; \
        sleep 10; \
    done; \
    apt-get install -y --no-install-recommends $packages; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

RUN mkdir -p /data

CMD ["python", "scripts/run_topic_textbook.py", "--help"]
