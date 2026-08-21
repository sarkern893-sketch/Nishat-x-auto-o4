import json
import os
import re
from copy import deepcopy
from pathlib import Path

from config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "settings.json"
PROCESSED_FILE = DATA_DIR / "processed_posts.log"

CURRENT_AI_MODEL = "openai/gpt-oss-120b"
# Groq decommissions old model IDs from time to time. Anything saved in an
# older settings.json under these names gets auto-migrated to
# CURRENT_AI_MODEL on load, so a stale deployment doesn't keep failing.
DEPRECATED_AI_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "qwen/qwen3-32b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "deepseek-r1-distill-llama-70b",
}

DEFAULT_SETTINGS = {
    "autopost": True,
    "delay_minutes": 0,
    "template": {"header": "", "footer": ""},
    "sources": [],
    "destinations": [],
    "forward_groups": [],
    "forwarding": {
        "enabled": False,
        "repeat_count": 1,
        "repeat_interval_minutes": 0,
    },
    "replacements": {
        "username": "",
        "phone": "",
        "email": "",
        "tme_link": "",
    },
    "emoji_editing": True,
    "ai": {
        "enabled": False,
        "style": "পরিষ্কার, স্বাভাবিক ও পেশাদার",
        "custom_prompt": "",
        "length": "মূল পোস্টের কাছাকাছি",
        "emoji": True,
        "model": CURRENT_AI_MODEL,
        "identity_name": "",
        "owner_name": "",
        "identity_filter": "",
        "master_instruction": "",
        "private_knowledge": "",
    },
    "live_chat": {
        "enabled": False,
        "style": "বন্ধুত্বপূর্ণ ও সহায়ক",
        "custom_prompt": "",
        "answer_length": "মাঝারি",
        "context_enabled": True,
    },
    "welcome": {
        "enabled": False,
        "message": "স্বাগতম {name}! 🎉",
    },
    "word_filters": [],
    "users": {},
    "user_campaign": {
        "message": "",
        "user_ids": [],
        "delay_minutes": 0,
        "enabled": False,
    },
    "group_ai": {},
    "channel_group_forwarding": {
        "enabled": False,
        "selected_group": "",
        "groups": {},
    },
    "privacy": {
        "username": {"on": True},
        "tme_link": {"on": True},
        "phone": {"on": True},
        "email": {"on": True},
        "user_id": {"on": False},
    },
    # Feature 7 — separate ON/OFF filter for images vs files/documents.
    "media_filter": {
        "image": True,
        "file": True,
    },
    # Feature 9 — Multi Admin System. ADMIN_IDS (env) are always Super Owner
    # and cannot be removed from here. Extra admins added from the bot live
    # in this dict: {"<user_id>": {"role": "admin"/"super_owner",
    # "name": "...", "permissions": {perm: bool}, "added_by": "<uid>"}}.
    "admins": {},
    # Feature 10 — Custom AI Post Format Style. When enabled, AI editing
    # wraps/formats posts using these admin-chosen pieces instead of a plain
    # paragraph rewrite.
    "format_style": {
        "enabled": False,
        "border": "━━━━━━━━━━━━━━━━━━",
        "header": "",
        "footer": "",
        "contact_line": "",
        "use_bullets": True,
        "use_emoji_heading": True,
    },
    # Feature 4 — Multi-Channel Watch + Smart Scheduling (ON/OFF). When OFF,
    # the normal per-source auto forward/post keeps working exactly as before.
    "multi_watch": {
        "enabled": False,
        "channels": [],
        "schedule_times": ["09:00", "14:00", "19:00"],
        "max_posts_per_slot": 1,
        "similarity_skip": True,
    },
}

# Feature 9 — permission keys admins can be granted individually.
ADMIN_PERMISSIONS = [
    "channel_manage",
    "schedule_manage",
    "ai_settings",
    "all_data_manage",
    "bot_settings",
    "user_account_control",
]


def _merge(default, current):
    if isinstance(default, dict):
        result = {}
        source = current if isinstance(current, dict) else {}
        for key, value in default.items():
            result[key] = _merge(value, source.get(key))
        # Bug fix — settings["users"], settings["admins"], settings["group_ai"]
        # ইত্যাদি "dynamic" dict (default স্কিমায় খালি {} থাকে, আসল key গুলো
        # user-id/group-id হওয়ায় স্কিমায় আগে থেকে জানা থাকে না)। আগে এখানে
        # শুধু default-এ থাকা key-গুলোই রাখা হতো, ফলে প্রতিবার Bot restart হলে
        # disk-এ সংরক্ষিত এই dynamic dict-গুলোর সব ডেটা মুছে যেত। এখন current-এ
        # থাকা অতিরিক্ত key-ও রাখা হয়, যাতে restart-এ ডেটা হারিয়ে না যায়।
        for key, value in source.items():
            if key not in result:
                result[key] = value
        return result
    return current if current is not None else default


def _looks_like_user_id(value) -> bool:
    """Bug fix — একটা menu-button-এর টেক্সট (যেমন '⬅️ ফিরে যান') ভুল করে
    User Campaign list-এ 'user' হিসেবে ঢুকে গেলে, সেটা দিয়ে message পাঠাতে
    গিয়ে Telegram 'entity খুঁজে পাওয়া যায়নি' error দিয়ে বার বার Admin-কে
    notify করত। এই ফাংশন শুধু আসল Telegram numeric ID বা @username ফরম্যাট
    মেনে চলা মান রাখে, বাকি (ইমোজি/মেনু টেক্সট) বাদ দেয়।"""
    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d{3,15}", text):
        return True
    return bool(re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", text))


def load_settings() -> dict:
    try:
        with SETTINGS_FILE.open(encoding="utf-8") as file:
            current = json.load(file)
        legacy_destination = current.get("destination_channel_id")
        settings = _merge(DEFAULT_SETTINGS, current)
        if legacy_destination and not current.get("destinations"):
            settings["destinations"] = [legacy_destination]
        if settings.get("ai", {}).get("model") in DEPRECATED_AI_MODELS:
            settings["ai"]["model"] = CURRENT_AI_MODEL
            save_settings(settings)
        campaign_ids = settings.get("user_campaign", {}).get("user_ids", [])
        cleaned_ids = [u for u in campaign_ids if _looks_like_user_id(u)]
        if cleaned_ids != campaign_ids:
            settings["user_campaign"]["user_ids"] = cleaned_ids
            for bad in set(campaign_ids) - set(cleaned_ids):
                settings.get("users", {}).pop(str(bad), None)
            save_settings(settings)
        return settings
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return deepcopy(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
    os.replace(temporary, SETTINGS_FILE)


def is_processed(post_key: str) -> bool:
    try:
        with PROCESSED_FILE.open(encoding="utf-8") as file:
            return post_key in {line.strip() for line in file}
    except FileNotFoundError:
        return False


def mark_processed(post_key: str) -> None:
    with PROCESSED_FILE.open("a", encoding="utf-8") as file:
        file.write(post_key + "\n")
