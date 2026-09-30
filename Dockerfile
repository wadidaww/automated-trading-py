FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir poetry
COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --without-hashes | pip install --no-cache-dir --no-deps -r /dev/stdin

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY src/ ./src/
COPY config/ ./config/
COPY data/models/ ./data/models/
ENV PYTHONPATH=/app/src
RUN useradd -m trader && chown -R trader /app
USER trader
HEALTHCHECK CMD python -m trader --health-check
ENTRYPOINT ["python", "-m", "trader"]
