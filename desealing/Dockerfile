FROM --platform=linux/amd64 python:3.13.3-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    gdal-bin \
    libgdal-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY lecture.py .
COPY methods.py .
COPY visualization.py .


CMD ["python", "-u", "main.py", "-c", "config_casier_docker.yaml"]