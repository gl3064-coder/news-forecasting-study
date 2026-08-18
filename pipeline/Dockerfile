FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git + openssh-client are needed by app.vault.sync for cloning/pulling
# the user's Obsidian vault from a private GitHub repo (L4 integration).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

# Persistent state lives here — mount a volume in production to keep across deploys
RUN mkdir -p /app/state
ENV GMAIL_CREDENTIALS_FILE=/app/state/credentials.json \
    GMAIL_TOKEN_FILE=/app/state/token.json \
    PULSE_DB_FILE=/app/state/pulse.db

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
