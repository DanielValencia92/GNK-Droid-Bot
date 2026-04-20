"""
PostgreSQL key-value store for persistent bot data.

When DATABASE_URL is set (DigitalOcean production), load_json/save_json in
gnk_bot.py mirror every write here so data survives container restarts.
On startup, restore_files() writes the DB contents back to local files so
that helper functions which read files directly (generate_standings_image etc.)
continue to work without any changes.

When DATABASE_URL is not set (local dev), this module is a no-op and the bot
reads/writes JSON files as usual.
"""

import os
import json
import logging

_conn = None


def is_enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _connect():
    import psycopg2
    url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn


def _get_conn():
    global _conn
    if not is_enabled():
        return None
    try:
        if _conn is None or _conn.closed:
            _conn = _connect()
            return _conn
        # Lightweight liveness check
        with _conn.cursor() as cur:
            cur.execute("SELECT 1")
        return _conn
    except Exception:
        try:
            _conn = _connect()
            return _conn
        except Exception as e:
            logging.error(f"DB connection failed: {e}")
            return None


def init():
    """Create the kv_store table if it doesn't exist. Call once on bot startup."""
    conn = _get_conn()
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key        TEXT PRIMARY KEY,
                data       JSONB        NOT NULL,
                updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """)
    logging.info("DB initialised (kv_store table ready).")


def load(key: str) -> dict:
    """Return the stored dict for *key*, or {} if not found."""
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM kv_store WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else {}
    except Exception as e:
        logging.error(f"DB load({key}) failed: {e}")
        return {}


def save(key: str, data: dict):
    """Upsert *data* under *key*."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kv_store (key, data, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET data       = EXCLUDED.data,
                        updated_at = NOW()
            """, (key, json.dumps(data)))
    except Exception as e:
        logging.error(f"DB save({key}) failed: {e}")


def restore_files(file_key_map: dict):
    """
    Write DB contents back to local files on startup.

    file_key_map: {local_filepath: db_key}
    e.g. {"current_runs.json": "current_runs"}
    """
    if not is_enabled():
        return
    for filepath, key in file_key_map.items():
        data = load(key)
        if data:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=4)
            logging.info(f"Restored {filepath} from DB (key={key}).")
