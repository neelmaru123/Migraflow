FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock* /app/
RUN poetry config virtualenvs.create false && poetry install --no-root --no-interaction --no-ansi || true

COPY . /app

RUN groupadd -r agentuser && useradd -r -g agentuser -d /app -s /sbin/nologin agentuser \
    && chown -R agentuser:agentuser /app

USER agentuser

CMD ["poetry", "run", "python", "main.py"]
