#!/bin/sh
set -e

echo "=== Migraflow API Container Initializing ==="

# Wait for PostgreSQL if DATABASE_URL is set
if [ -n "$DATABASE_URL" ]; then
  echo "Checking database connection..."
  python - <<'EOF'
import os
import sys
import time
from urllib.parse import urlparse

db_url = os.getenv("DATABASE_URL", "")
clean_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

retries = 20
connected = False
while retries > 0:
    try:
        import psycopg2
        parsed = urlparse(clean_url)
        conn = psycopg2.connect(
            dbname=parsed.path.lstrip("/"),
            user=parsed.username,
            password=parsed.password,
            host=parsed.hostname,
            port=parsed.port or 5432,
            connect_timeout=3
        )
        conn.close()
        connected = True
        print("PostgreSQL connection confirmed.")
        break
    except Exception as e:
        print(f"Waiting for database ({e}). Retrying in 2s...")
        time.sleep(2)
        retries -= 1

if not connected:
    print("Warning: Could not connect to PostgreSQL within timeout. Continuing...")
EOF
fi

echo "Running database schema migrations..."
alembic upgrade head

echo "Starting application server..."
exec "$@"
