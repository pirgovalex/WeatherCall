FROM python:3.12-slim

# cron runs the scheduled forecast capture
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install only the runtime dependencies (base + ml) declared in pyproject.toml.
# The package itself is not installed: the code resolves the project root from its own
# file location, so it must run from /app/src.
COPY pyproject.toml .
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['ml']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . /app
ENV PYTHONPATH=/app/src

# Full pipeline every 6 hours (UTC): capture, station data, live risk row, marts.
# cron starts jobs with a minimal environment, so the script uses absolute paths.
# Output goes to the log file and to `docker logs`.
RUN printf '%s\n' \
    'SHELL=/bin/sh' \
    'PATH=/usr/local/bin:/usr/bin:/bin' \
    '0 */6 * * * root mkdir -p /app/data/logs && /bin/sh /app/scripts/run_pipeline.sh 2>&1 | tee -a /app/data/logs/pipeline.log > /proc/1/fd/1' \
    > /etc/cron.d/ingest \
    && chmod 0644 /etc/cron.d/ingest

# One run at start-up so a fresh container has current data, then hand over to cron.
CMD ["/bin/sh", "-c", "mkdir -p /app/data/logs && (/bin/sh /app/scripts/run_pipeline.sh 2>&1 | tee -a /app/data/logs/pipeline.log) & exec cron -f"]
