FROM --platform=linux/amd64 python:3.13.3-slim

# Update system packages to reduce vulnerabilities
# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    gdal-bin \
    libgdal-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /usr/src/app

# Copy the requirements file into the container
COPY desealing/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY desealing/main.py .
COPY desealing/lecture.py .
COPY desealing/methods.py .
COPY desealing/visualization.py .
COPY desealing/config_casier_docker.yaml .
# Copy the 'donnees' directory as-is
COPY desealing/donnees ./donnees

# Command to run the application
CMD ["python", "main.py", "-c", "config_casier_docker.yaml"]