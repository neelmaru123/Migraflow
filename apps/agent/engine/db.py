"""
Database connection pooling and identifier quoting utilities for agent execution engine.
"""

import logging
import threading
import urllib.parse
from sqlalchemy import create_engine

logger = logging.getLogger("docker-agent-execution")


def _clean_url_for_engine(db_url: str) -> str:
    cleaned = urllib.parse.unquote(db_url.strip())
    if cleaned.startswith("postgresql+asyncpg://"):
        cleaned = "postgresql+psycopg2://" + cleaned[len("postgresql+asyncpg://") :]
    elif cleaned.startswith("postgresql://"):
        cleaned = "postgresql+psycopg2://" + cleaned[len("postgresql://") :]
    elif cleaned.startswith("mysql+aiomysql://"):
        cleaned = "mysql+pymysql://" + cleaned[len("mysql+aiomysql://") :]
    elif cleaned.startswith("mysql://"):
        cleaned = "mysql+pymysql://" + cleaned[len("mysql://") :]
    return cleaned


_ENGINE_CACHE = {}
_ENGINE_CACHE_LOCK = threading.Lock()


def _get_engine(db_url: str):
    """Creates (or reuses a cached) SQLAlchemy engine with connection pooling, recycle limits, and active TCP keepalives."""
    clean_url = _clean_url_for_engine(db_url)

    with _ENGINE_CACHE_LOCK:
        if clean_url in _ENGINE_CACHE:
            return _ENGINE_CACHE[clean_url]

        connect_args = {}
        if "postgres" in clean_url:
            connect_args = {
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            }
        kwargs = {
            "pool_pre_ping": True,
            "pool_recycle": 300,
            "connect_args": connect_args,
        }
        if "sqlite" not in clean_url:
            kwargs["pool_timeout"] = 30

        engine = create_engine(clean_url, **kwargs)
        _ENGINE_CACHE[clean_url] = engine
        return engine


def dispose_all_engines():
    """Disposes and clears all cached engines. Call this once at the end of a job (success or failure)."""
    with _ENGINE_CACHE_LOCK:
        for engine in _ENGINE_CACHE.values():
            try:
                engine.dispose()
            except Exception:
                pass
        _ENGINE_CACHE.clear()


def _quote_identifier(name: str, engine_type: str = "postgresql") -> str:
    """Safely quotes table and column identifiers based on database dialect."""
    if not name:
        return ""
    engine_type = engine_type.lower()
    clean_name = name.replace("`", "").replace('"', '')
    if "mysql" in engine_type or "mariadb" in engine_type:
        return f"`{clean_name}`"
    return f'"{clean_name}"'
