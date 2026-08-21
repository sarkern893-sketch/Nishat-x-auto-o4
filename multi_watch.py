"""Feature 4 — Multi-Channel Watch + Smart Scheduling.

Pure helper functions only — no Telegram calls live here. userbot.py wires
these into the actual event handler / publish call. Keeping this separate
means:
  - it is easy to test in isolation
  - it never touches the normal per-source auto-post pipeline in userbot.py
    (Feature 1's "existing system stays exactly as-is" requirement), since
    that pipeline doesn't import anything from this file
  - when settings["multi_watch"]["enabled"] is False, none of this runs and
    the normal Auto Forward/Auto Post system behaves exactly as before.
"""
import json
import os
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from config import DATA_DIR

BUFFER_FILE = DATA_DIR / "multi_watch_buffer.jsonl"
STATE_FILE = DATA_DIR / "multi_watch_state.json"
PUBLISHED_LOG = DATA_DIR / "multi_watch_published.log"
SEEN_FILE = DATA_DIR / "multi_watch_seen.log"

SIMILARITY_THRESHOLD = 0.82
BUFFER_MAX_AGE_HOURS = 48


def _read_jsonl(path: Path) -> list:
    rows = []
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return rows


def is_seen(key: str) -> bool:
    """Dedupe raw source messages across restarts, same pattern as
    settings_store.is_processed() for the normal pipeline."""
    try:
        with SEEN_FILE.open(encoding="utf-8") as file:
            return key in {line.strip() for line in file}
    except FileNotFoundError:
        return False


def mark_seen(key: str) -> None:
    with SEEN_FILE.open("a", encoding="utf-8") as file:
        file.write(key + "\n")


def add_candidate(chat_id, message_id, text: str, has_media: bool) -> None:
    record = {
        "chat_id": str(chat_id),
        "message_id": message_id,
        "text": text or "",
        "has_media": bool(has_media),
        "collected_at": datetime.utcnow().isoformat() + "Z",
    }
    with BUFFER_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_buffer() -> list:
    return _read_jsonl(BUFFER_FILE)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:1000], b[:1000]).ratio()


def _recent_published_texts(limit: int = 40) -> list:
    try:
        with PUBLISHED_LOG.open(encoding="utf-8") as file:
            lines = [line.rstrip("\n") for line in file if line.strip()]
        return lines[-limit:]
    except FileNotFoundError:
        return []


def record_published(text: str) -> None:
    """Feature 4 — remembers recently published text so tomorrow's picks
    don't repeat something already sent (cross-channel duplicate memory)."""
    lines = _recent_published_texts(200) + [(text or "")[:300].replace("\n", " ")]
    with PUBLISHED_LOG.open("w", encoding="utf-8") as file:
        file.write("\n".join(lines[-200:]) + "\n")


def pick_best_candidates(candidates: list, max_count: int, skip_similar: bool) -> list:
    """'উপযুক্ত Post নির্বাচন' + 'Duplicate Content Filter'.

    Dedupe candidates against each other and against recently published
    text, then prefer the richer post (has media, longer text) as 'best'.
    """
    recent = _recent_published_texts() if skip_similar else []
    kept = []
    for candidate in candidates:
        text = candidate.get("text", "")
        if skip_similar:
            if any(_similar(text, other.get("text", "")) >= SIMILARITY_THRESHOLD for other in kept):
                continue
            if any(_similar(text, published) >= SIMILARITY_THRESHOLD for published in recent):
                continue
        kept.append(candidate)
    kept.sort(key=lambda c: (not c.get("has_media", False), -len(c.get("text", ""))))
    return kept[:max(0, max_count)]


def remove_from_buffer(used_keys: set) -> None:
    """Rewrite the buffer file leaving out candidates that were just
    published and any that expired (older than BUFFER_MAX_AGE_HOURS, so a
    long-dead post can't resurface weeks later)."""
    remaining = []
    cutoff = datetime.utcnow().timestamp() - BUFFER_MAX_AGE_HOURS * 3600
    for row in _read_jsonl(BUFFER_FILE):
        key = f"{row.get('chat_id')}:{row.get('message_id')}"
        if key in used_keys:
            continue
        try:
            collected = datetime.fromisoformat(row.get("collected_at", "").rstrip("Z")).timestamp()
        except ValueError:
            collected = 0
        if collected and collected < cutoff:
            continue
        remaining.append(row)
    temp = BUFFER_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        for row in remaining:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, BUFFER_FILE)


def due_slot(schedule_times: list, now: datetime, state: dict) -> str:
    """Returns the HH:MM slot that is due right now and hasn't already
    fired today, or '' if none is due."""
    today = now.strftime("%Y-%m-%d")
    current = now.strftime("%H:%M")
    for slot in schedule_times or []:
        if slot == current and state.get(slot) != today:
            return slot
    return ""


def load_state() -> dict:
    try:
        with STATE_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    temp = STATE_FILE.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(temp, STATE_FILE)


def mark_fired(state: dict, slot: str, now: datetime) -> None:
    state[slot] = now.strftime("%Y-%m-%d")
    save_state(state)
