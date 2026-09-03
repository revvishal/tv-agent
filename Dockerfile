# --- Stage 1: Build & Cache Dependencies ---
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Stage 2: Final Slimmer Runtime ---
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Setup cron configuration
COPY crontab /etc/cron.d/tv-agent-cron
RUN chmod 0644 /etc/cron.d/tv-agent-cron \
    && crontab /etc/cron.d/tv-agent-cron \
    && touch /var/log/tv-agent.log

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# FIXED FOR VERCEL: Copy explicit folder structures instead of a wild glob file pattern
COPY src/ ./src/
COPY config/ ./config/

# If you have specific individual files in your root folder (like main.py), copy them cleanly by name:
# COPY main.py ./

CMD ["/entrypoint.sh"]