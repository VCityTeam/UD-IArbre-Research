FROM python:3.12-slim-bookworm

ARG PYTHON_VERSION=3.12

LABEL maintainer="Marwan Ait Addi <marwan.ait.addi@univ-lyon2.fr>" \
      description="Sunpath calculation tool for pySunlight and sunlight libraries" \
      version="1.0.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install -r /tmp/requirements.txt

COPY . /workspace

RUN chmod +x /workspace/sunpath.py

ENTRYPOINT ["python", "/workspace/sunpath.py"]
CMD ["--help"]
