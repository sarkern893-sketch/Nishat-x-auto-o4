"""Small shared file so the admin-panel process (main.py) can report the
live status of the userbot process (Feature 3 — /status command), without
the two processes needing a direct connection to each other.
"""
import json
import time
from pathlib import Path

from config import DATA_DIR

STATUS_FILE = DATA_DIR / "service_status.json"

# If the userbot process hasn't updated its heartbeat within this many
# seconds, /status treats it as "offline" even if the file still exists.
STALE_AFTER_SECONDS = 180


def write_status(**fields) -> None:
    data = read_status_raw()
    data.update(fields)
    data["updated_at"] = time.time()
    try:
        temp = STATUS_FILE.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        temp.replace(STATUS_FILE)
    except OSError:
        pass


def read_status_raw() -> dict:
    try:
        with STATUS_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def read_status() -> dict:
    data = read_status_raw()
    updated_at = data.get("updated_at", 0)
    data["userbot_alive"] = bool(updated_at) and (time.time() - updated_at) < STALE_AFTER_SECONDS
    return data
