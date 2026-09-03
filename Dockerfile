# Official Playwright image: Python + Chromium + all OS deps preinstalled.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

RUN apt-get update && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cron schedule: default is once a day at 06:00 UTC. Edit crontab to change.
COPY crontab /etc/cron.d/tv-agent-cron
RUN chmod 0644 /etc/cron.d/tv-agent-cron && crontab /etc/cron.d/tv-agent-cron

RUN touch /var/log/tv-agent.log

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
