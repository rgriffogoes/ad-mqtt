# --- build stage: has git, used only to fetch the pinned insteon-mqtt fork ---
FROM python:3.10-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --prefix=/install -r /tmp/requirements.txt

# --- runtime stage: no git, no build tooling, non-root ---
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ADMQTT_LOG_SCREEN=True \
    ADMQTT_LOG_FILE=

COPY --from=build /install /usr/local
COPY ad_mqtt /home/ad_mqtt/
COPY run.py /home/run.py

RUN useradd --create-home --uid 1000 app \
 && chown -R app:app /home

# Mounting zones config file (never bake real zone/RF identifiers into the image)
VOLUME [ "/home/devices.py" ]

USER app
WORKDIR /home/
CMD ["python", "/home/run.py"]
