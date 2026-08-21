"""Feature 1 — Bot-এর Personal Message System Fix.

Only this file changes HOW the existing "send a message to another Telegram
user/chat from the bot" system delivers its message: instead of the Bot
Token, it now goes out from the Personal Telegram Account (user_client).

`main.py` (admin panel process) does not own `user_client` — `userbot.py`
does. So `main.py` enqueues each personal message here, and `userbot.py`
(which is always connected as the personal account) picks it up and sends
it with `user_client`, then records the real delivery status back here.
Existing buttons/workflow/UI in main.py are unchanged — only the actual
sending mechanism underneath.
"""
import json
import time
import uuid
from pathlib import Path

from config import DATA_DIR

QUEUE_FILE = DATA_DIR / "personal_message_queue.jsonl"
STATUS_FILE = DATA_DIR / "personal_message_status.json"


def _read_all_status() -> dict:
    try:
        with STATUS_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_all_status(data: dict) -> None:
    try:
        temp = STATUS_FILE.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        temp.replace(STATUS_FILE)
    except OSError:
        pass


def queue_message(chat_id, text: str, tag: str = "") -> str:
    """Called from main.py. Returns a job_id the caller can poll with get_status()."""
    job_id = uuid.uuid4().hex[:12]
    record = {
        "job_id": job_id,
        "chat_id": chat_id,
        "text": text,
        "tag": tag,
        "queued_at": time.time(),
    }
    with QUEUE_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    status = _read_all_status()
    status[job_id] = {"status": "queued", "chat_id": chat_id, "tag": tag, "queued_at": record["queued_at"]}
    _write_all_status(status)
    return job_id


def get_status(job_id: str) -> dict:
    return _read_all_status().get(job_id, {"status": "unknown"})


def get_latest_status_for_chat(chat_id) -> dict:
    """Convenience for UI: latest known delivery status for a given chat_id."""
    best = None
    for record in _read_all_status().values():
        if str(record.get("chat_id")) == str(chat_id):
            if best is None or record.get("queued_at", 0) >= best.get("queued_at", 0):
                best = record
    return best or {"status": "unknown"}


# ── consumer side (used only by userbot.py, which owns user_client) ──

def pop_pending() -> list:
    """Read and clear the queue file, returning all pending jobs (FIFO)."""
    try:
        with QUEUE_FILE.open(encoding="utf-8") as file:
            lines = [line for line in file if line.strip()]
    except FileNotFoundError:
        return []
    try:
        QUEUE_FILE.unlink()
    except OSError:
        pass
    jobs = []
    for line in lines:
        try:
            jobs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return jobs


def mark_result(job_id: str, ok: bool, error: str = "") -> None:
    status = _read_all_status()
    entry = status.setdefault(job_id, {})
    entry["status"] = "sent" if ok else "failed"
    entry["finished_at"] = time.time()
    if error:
        entry["error"] = error[:300]
    _write_all_status(status)
