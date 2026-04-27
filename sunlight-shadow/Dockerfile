FROM python:3.9.17-bullseye

LABEL maintainer="UD-IArbre Research Team" \
      source.repo="https://github.com/VCityTeam/pySunlight-docker" \
      description="Repo-local pySunlight Docker environment for the sunlight-shadow workspace"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update

# Keep the package set close to ../pySunlight-docker so we inherit its
# dependency resolution behavior while still building from local sources.
RUN apt-get install -y swig4.0
RUN ln -s /usr/bin/swig4.0 /usr/bin/swig
RUN swig -version

RUN apt-get install -y \
    build-essential \
    cmake \
    git

# Build from the repository root so the image can initialize submodules even
# when the host checkout was not cloned with --recursive.
COPY .git /workspace/.git
COPY .gitmodules /workspace/.gitmodules
COPY sunlight-shadow /workspace/sunlight-shadow

RUN git config --global --add safe.directory /workspace && \
    git submodule update --init --recursive \
      sunlight-shadow/Sunlight \
      sunlight-shadow/pySunlight \
      sunlight-shadow/pySunlight-docker \
      sunlight-shadow/UD-Demo-Sunlight

WORKDIR /workspace/sunlight-shadow/pySunlight

RUN pip install -e .

WORKDIR /workspace/sunlight-shadow

ENV START_DATE=${START_DATE}
ENV END_DATE=${END_DATE}
ENV TIMESTAMP=${TIMESTAMP}
ENV LOG_LEVEL=${LOG_LEVEL}
ENV FORMAT=${FORMAT}
ENV OPTIONAL_ARGS=${OPTIONAL_ARGS}

COPY sunlight-shadow/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
