# --- Stage 1: Build & Cache Dependencies ---
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS builder

WORKDIR /app

# Install system dependencies and clean cache in the same layer
RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies first (prevents re-building layers)
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Stage 2: Final Slimmer Runtime ---
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Copy system/cron packages from the builder stage
RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

# Copy installed pip packages from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Setup cron configuration before moving app files
COPY crontab /etc/cron.d/tv-agent-cron
RUN chmod 0644 /etc/cron.d/tv-agent-cron \
    && crontab /etc/cron.d/tv-agent-cron \
    && touch /var/log/tv-agent.log

# Copy entrypoint directly
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy ONLY your python application code (prevents uploading heavy local junk)
COPY *.py ./
# Note: If you have specific source folders, copy them explicitly like:
# COPY src/ ./src/

CMD ["/entrypoint.sh"]