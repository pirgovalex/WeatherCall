FROM python:3.12-slim

WORKDIR /app

# Runtime dependencies only, read from pyproject.toml
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY contracts ./contracts
COPY src ./src

# Marts are mounted read-only from the warehouse at runtime
ENV MARTS_DIR=/marts \
    ENV=production

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
