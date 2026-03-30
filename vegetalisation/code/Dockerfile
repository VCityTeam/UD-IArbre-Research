FROM python:3.11-slim-bookworm

ARG FLAIR_HUB_REF=main

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdal-bin \
    git \
    libgdal-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-workflow.txt /tmp/requirements-workflow.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r /tmp/requirements-workflow.txt && \
    python -m pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 && \
    python -m pip install "git+https://github.com/IGNF/FLAIR-HUB.git@${FLAIR_HUB_REF}"

COPY . /workspace

CMD ["python", "run_workflow.py", "--help"]
