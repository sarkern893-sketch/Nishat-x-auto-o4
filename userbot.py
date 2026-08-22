"""Collect source posts, make minimal edits, publish, then forward from ours.

Feature 1 — personal_message_worker() sends admin's "personal message" jobs
            with user_client (Personal Account) instead of the bot token.
Feature 2 — important failures call notify_admin()/helpers from notifier.py.
Feature 7 — media filter (image/file ON-OFF) + sturdier media sending.
Feature 8 — group forwarding retries and falls back to a user_client copy
            when Telegram blocks a plain forward (e.g. protected content),
            and reports the real reason to the admin when nothing works.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, AuthKeyUnregisteredError, RPCError

from telegram.ext import Application

from config import BOT_TOKEN, API_ID, API_HASH, SESSION, PHONE, DATA_DIR, EMOJI_EDITING
from editor import edit_post
from ai_client import edit_post_with_ai
from privacy import clean_personal, replace_personal
from settings_store import is_processed, load_settings, mark_processed
import personal_messenger
import status_store
import multi_watch
import xlsx_sanitizer
from notifier import (
    notify_session_expired,
    notify_publish_failed,
    notify_forward_failed,
    notify_connection_issue,
    notify_ai_error,
    notify_access_issue,
)

bot_app = Application.builder().token(BOT_TOKEN).build()
user_client = TelegramClient(SESSION, API_ID, API_HASH)
TEMP_DIR = DATA_DIR / "media_tmp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
FORWARD_HISTORY = DATA_DIR / "channel_group_forward_history.jsonl"


def log_post(status: str):
    with open(DATA_DIR / "posts.log", "a", encoding="utf-8") as file:
        file.write(status + "\n")


def forward_key(source_chat, message_id, group, repeat_index):
    return f"{source_chat}:{message_id}:{group}:{repeat_index}"


def forward_done(key: str) -> bool:
    try:
        with FORWARD_HISTORY.open(encoding="utf-8") as file:
            return any(json.loads(line).get("key") == key for line in file if line.strip())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def record_forward(key, source_chat, message_id, group, repeat_index, status, error=""):
    record = {
        "key": key,
        "source_chat": str(source_chat),
        "source_message_id": message_id,
        "group": str(group),
        "forward_number": repeat_index + 1,
        "time": datetime.utcnow().isoformat() + "Z",
        "status": status,
        "error": error[:300],
    }
    with FORWARD_HISTORY.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def schedule_wait_seconds(group_settings: dict) -> int:
    schedule = group_settings.get("schedule", {})
    if not schedule.get("enabled"):
        return 0
    now = datetime.now()
    try:
        start = datetime.strptime(schedule.get("start", "00:00"), "%H:%M").time()
        end = datetime.strptime(schedule.get("end", "23:59"), "%H:%M").time()
    except ValueError:
        return 0
    inside = start <= now.time() <= end if start <= end else now.time() >= start or now.time() <= end
    if inside:
        return 0
    next_start = datetime.combine(now.date(), start)
    if next_start <= now:
        next_start += timedelta(days=1)
    return max(0, int((next_start - now).total_seconds()))


def _document_filename(message) -> str:
    """Telethon message-এর আসল ফাইলনেম বের করে (থাকলে), না থাকলে খালি স্ট্রিং।"""
    try:
        return message.file.name or ""
    except Exception:
        return ""


def _cleanup_temp_files(*paths) -> None:
    seen = set()
    for raw in paths:
        if not raw:
            continue
        path = Path(raw)
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            path.unlink()
        except OSError:
            pass


async def _send_document_sanitized(message, send_fn) -> object:
    """Excel ফাইল হলে (.xlsx/.xlsm) sanitize করে পাঠায়, না হলে None রিটার্ন
    করে যাতে caller স্বাভাবিক (native) পথে পাঠাতে পারে। send_fn(path_str)
    হলো এমন একটা async ফাংশন যেটা sanitize করা লোকাল ফাইল পাঠিয়ে দেয়।"""
    filename = _document_filename(message)
    if not xlsx_sanitizer.is_excel_file(filename):
        return None
    local_path = await message.download_media(file=str(TEMP_DIR))
    if not local_path:
        return None
    settings = load_settings()
    sanitized_path, _redacted = xlsx_sanitizer.sanitize_xlsx(Path(local_path), settings)
    try:
        return await send_fn(str(sanitized_path))
    finally:
        _cleanup_temp_files(local_path, sanitized_path)


async def copy_via_user_client(source_chat, message_id, destination):
    """Feature 8 fallback — used when a plain bot forward is blocked
    (e.g. Telegram 'forwards restricted'/protected content). user_client
    already has read access to the source (it is what detects new posts),
    so it re-sends the same content directly instead of a native forward.
    Excel ফাইল হলে পাঠানোর আগে ব্যক্তিগত তথ্য sanitize করা হয়।
    """
    message = await user_client.get_messages(source_chat, ids=message_id)
    if not message:
        return None
    text = message.text or message.message or ""
    if message.media:
        sanitized = await _send_document_sanitized(
            message, lambda path: user_client.send_message(destination, text, file=path))
        if sanitized is not None:
            return sanitized
        return await user_client.send_message(destination, text, file=message.media)
    return await user_client.send_message(destination, text)


async def forward_via_user_client(source_chat, message_id, destination) -> bool:
    """Feature 2 fix — Channel → Group forward এখন সবসময় প্রথমে Personal
    Account (user_client) দিয়ে চেষ্টা হয়। Bot-কে source channel বা
    destination group কোনোটারই member হওয়া লাগে না — শুধু Personal
    Account-এর access লাগে, ঠিক যেভাবে একজন মানুষ নিজে হাতে forward করত।
    Excel ফাইল হলে native forward বাদ দিয়ে সরাসরি sanitize+copy পথে যায়,
    কারণ native forward-এ ফাইলের ভেতরের কনটেন্ট বদলানো যায় না।"""
    try:
        message = await user_client.get_messages(source_chat, ids=message_id)
        if message and message.media and xlsx_sanitizer.is_excel_file(_document_filename(message)):
            return bool(await copy_via_user_client(source_chat, message_id, destination))
        await user_client.forward_messages(destination, message_id, source_chat)
        return True
    except Exception:
        try:
            return bool(await copy_via_user_client(source_chat, message_id, destination))
        except Exception:
            return False


async def send_to_group_via_user_client(message, group, text: str, settings: dict) -> None:
    """AI দিয়ে edit করা group post-ও Personal Account দিয়েই পাঠানো হয়।
    Telethon-এর file= প্যারামিটার সরাসরি message.media নিতে পারে, তাই আলাদা
    করে download/upload-এর দরকার নেই — শুধু Excel ফাইলের বেলায় আগে sanitize
    করে নেওয়া হয়।"""
    if message.media and _media_allowed(message, settings):
        sanitized = await _send_document_sanitized(
            message, lambda path: user_client.send_message(group, text or "", file=path))
        if sanitized is not None:
            return
        await user_client.send_message(group, text or "", file=message.media)
    elif text:
        await user_client.send_message(group, text)


async def forward_channel_post(event, settings):
    config = settings.get("channel_group_forwarding", {})
    if not config.get("enabled"):
        return
    groups = config.get("groups", {})
    if not groups:
        return
    source_chat = event.chat_id
    for group, group_settings in groups.items():
        if not group_settings.get("enabled", True) or group_settings.get("paused"):
            continue
        count = max(1, min(20, int(group_settings.get("count", 1))))
        delay = max(0, int(group_settings.get("delay_seconds", 0)))
        wait_for_schedule = schedule_wait_seconds(group_settings)
        if wait_for_schedule:
            await asyncio.sleep(wait_for_schedule)
        for repeat_index in range(count):
            key = forward_key(source_chat, event.id, group, repeat_index)
            if forward_done(key) or not settings.get("channel_group_forwarding", {}).get("enabled"):
                continue
            if repeat_index and delay:
                await asyncio.sleep(delay)
            try:
                if group_settings.get("ai_enabled") and (event.message.text or event.message.message):
                    ai_settings = dict(settings.get("ai", {}))
                    ai_settings.update(group_settings.get("ai", {}))
                    edited = await edit_post_with_ai(event.message.text or event.message.message or "", {"ai": ai_settings})
                    await send_to_group_via_user_client(event.message, group, edited, settings)
                else:
                    ok = await forward_via_user_client(source_chat, event.id, group)
                    if not ok:
                        # Personal Account দিয়েও না হলে শেষ চেষ্টা হিসেবে Bot API
                        # (শুধু তখনই কাজ করবে যদি Bot আগে থেকেই group-এর member থাকে)।
                        await bot_app.bot.forward_message(
                            chat_id=group,
                            from_chat_id=source_chat,
                            message_id=event.id,
                        )
                record_forward(key, source_chat, event.id, group, repeat_index, "success")
            except Exception as error:
                record_forward(key, source_chat, event.id, group, repeat_index, "error", str(error))
                print(f"⚠️ Channel → Group failed for {group}: {error}")
                await notify_forward_failed(bot_app.bot, group, error)


def apply_template(text: str, template: dict) -> str:
    parts = []
    if template.get("header"):
        parts.append(template["header"])
    if text:
        parts.append(text)
    if template.get("footer"):
        parts.append(template["footer"])
    return "\n\n".join(parts)


def apply_word_filters(text: str, word_filters: list) -> str:
    """Simple literal find→replace list, admin-configured (⚙️ AI সেটিংস → 🔤 Word Filter)."""
    for item in word_filters or []:
        find = (item or {}).get("find", "")
        if find:
            text = text.replace(find, (item or {}).get("replace", ""))
    return text


async def prepare_text(text: str, settings: dict) -> str:
    text = clean_personal(
        text,
        settings.get("privacy"),
        settings.get("replacements"),
    )
    text = apply_word_filters(text, settings.get("word_filters", []))
    ai = settings.get("ai", {})
    if ai.get("enabled") and text:
        try:
            text = await edit_post_with_ai(text, settings)
        except Exception as error:
            print(f"⚠️ AI editing failed, original cleaned text used: {error}")
            await notify_ai_error(bot_app.bot, error)
    text = edit_post(text, settings.get("emoji_editing", EMOJI_EDITING))
    return apply_template(text, settings.get("template", {}))


def _media_allowed(message, settings: dict) -> bool:
    """Feature 7 — separate ON/OFF filter for Image vs File/Document."""
    media_filter = settings.get("media_filter", {"image": True, "file": True})
    if message.photo:
        return media_filter.get("image", True)
    if not message.media:
        return True
    return media_filter.get("file", True)


async def send_message(destination, message, text: str, settings: dict = None):
    """Send text or media through the bot, preserving the original media type.

    Feature 7: if the message's media type is filtered OFF, only the media
    is skipped — the (edited) text still gets published, matching:
    "Image OFF: শুধু Image বাদ যাবে। File OFF: শুধু File/Document বাদ যাবে।"
    """
    settings = settings or {}
    if not message.media or not _media_allowed(message, settings):
        if not text:
            return None
        return await bot_app.bot.send_message(destination, text)

    last_error = None
    for attempt in range(2):  # Feature 7/8 — one retry for transient network hiccups
        try:
            media_path = await message.download_media(file=str(TEMP_DIR))
            if not media_path:
                return await bot_app.bot.send_message(destination, text)
            path = Path(media_path)
            sanitized_path = path
            # Excel ফাইলে ব্যক্তিগত তথ্য থাকতে পারে — পাঠানোর আগে sanitize করা হয়।
            if xlsx_sanitizer.is_excel_file(path.name):
                sanitized_path, _redacted = xlsx_sanitizer.sanitize_xlsx(path, settings)
            try:
                with sanitized_path.open("rb") as media:
                    if message.photo:
                        return await bot_app.bot.send_photo(destination, media, caption=text)
                    if message.video:
                        return await bot_app.bot.send_video(destination, media, caption=text)
                    if message.animation:
                        return await bot_app.bot.send_animation(destination, media, caption=text)
                    if message.audio:
                        return await bot_app.bot.send_audio(destination, media, caption=text)
                    if message.voice:
                        return await bot_app.bot.send_voice(destination, media, caption=text)
                    return await bot_app.bot.send_document(destination, media, caption=text)
            finally:
                _cleanup_temp_files(path, sanitized_path)
        except Exception as error:
            last_error = error
            if attempt == 0:
                await asyncio.sleep(2)
    raise last_error


async def forward_repeatedly(source_chat, message_id, groups, forwarding):
    """Forward the edited message from the user's channel, never re-compose it."""
    if not forwarding.get("enabled") or not groups:
        return
    count = max(1, int(forwarding.get("repeat_count", 1)))
    interval = max(0, int(forwarding.get("repeat_interval_minutes", 0))) * 60
    for repeat_index in range(count):
        if repeat_index and interval:
            await asyncio.sleep(interval)
        for group in groups:
            try:
                ok = await forward_via_user_client(source_chat, message_id, group)
                if not ok:
                    await bot_app.bot.forward_message(
                        chat_id=group,
                        from_chat_id=source_chat,
                        message_id=message_id,
                    )
            except Exception as error:
                print(f"⚠️ Forward failed for {group}: {error}")
                await notify_forward_failed(bot_app.bot, group, error)


@user_client.on(events.NewMessage())
async def on_destination_post(event):
    settings = load_settings()
    destination_values = {str(item).lstrip("@").lower() for item in settings.get("destinations", [])}
    chat = await event.get_chat()
    chat_id = str(event.chat_id)
    username = str(getattr(chat, "username", "") or "").lstrip("@").lower()
    if chat_id not in destination_values and (not username or username not in destination_values):
        return
    asyncio.create_task(forward_channel_post(event, settings))


@user_client.on(events.NewMessage())
async def on_new_post(event):
    settings = load_settings()
    chat = await event.get_chat()
    source_values = {str(item).lstrip("@").lower() for item in settings["sources"]}
    chat_id = str(event.chat_id)
    chat_username = str(getattr(chat, "username", "") or "").lstrip("@").lower()
    if chat_id not in source_values and (not chat_username or chat_username not in source_values):
        return

    post_key = f"{event.chat_id}:{event.id}"
    if is_processed(post_key) or not settings["autopost"]:
        return

    message = event.message
    raw_text = message.text or message.message or ""
    final_text = await prepare_text(raw_text, settings)
    if not final_text and not message.media:
        log_post("skipped")
        mark_processed(post_key)
        return

    delay_minutes = max(0, int(settings.get("delay_minutes", 0)))
    if delay_minutes:
        await asyncio.sleep(delay_minutes * 60)

    destinations = settings["destinations"]
    if not destinations:
        print("❌ destination channel সেট করা নেই — স্কিপ")
        log_post("skipped")
        return

    for destination in destinations:
        try:
            sent = await send_message(destination, message, final_text, settings)
            if sent is None:
                continue
            # Option B: forward the edited message from our destination channel.
            asyncio.create_task(
                forward_repeatedly(
                    destination,
                    sent.message_id,
                    settings.get("forward_groups", []),
                    settings.get("forwarding", {}),
                )
            )
        except Exception as error:
            print(f"⚠️ Publish failed for {destination}: {error}")
            log_post("failed")
            await notify_publish_failed(bot_app.bot, destination, error)
            return

    mark_processed(post_key)
    log_post("published")
    print(f"✅ Edited post published and forwarding scheduled: {final_text[:50]}...")


# ── Feature 4 — Multi-Channel Watch + Smart Scheduling ──
# Separate from the normal source/destination pipeline above (Feature 1's
# rule: don't touch the existing system). Only channels the admin puts in
# settings["multi_watch"]["channels"] are watched here, and only when
# settings["multi_watch"]["enabled"] is True. When it's False this handler
# does nothing at all and the normal Auto Forward/Auto Post system (above)
# keeps working exactly as before.
@user_client.on(events.NewMessage())
async def on_multi_watch_source(event):
    settings = load_settings()
    watch = settings.get("multi_watch", {})
    if not watch.get("enabled"):
        return
    watch_values = {str(item).lstrip("@").lower() for item in watch.get("channels", [])}
    if not watch_values:
        return
    chat = await event.get_chat()
    chat_id = str(event.chat_id)
    chat_username = str(getattr(chat, "username", "") or "").lstrip("@").lower()
    if chat_id not in watch_values and (not chat_username or chat_username not in watch_values):
        return

    key = f"mw:{event.chat_id}:{event.id}"
    if multi_watch.is_seen(key):
        return
    multi_watch.mark_seen(key)

    message = event.message
    raw_text = message.text or message.message or ""
    if not raw_text and not message.media:
        return
    # Feature 4 — "Content যাচাই/তুলনা" happens later at publish time
    # (pick_best_candidates), so every qualifying post just gets buffered
    # here; nothing is published immediately from this handler.
    multi_watch.add_candidate(event.chat_id, event.id, raw_text, bool(message.media))


async def scheduled_posts_worker():
    """Feature — Scheduled Posts: Admin-এর নিজে Save করা প্রতিটা Post তার
    নিজস্ব নির্ধারিত সময়ে (HH:MM) প্রতিদিন সব Destination Channel-এ পাবলিশ
    হয়। একবার Save করা থাকলে প্রতিদিন সেই সময়ে আবার চলতে থাকে, যতক্ষণ না
    Admin সেটা বন্ধ (🔁 Post ON/OFF) বা ডিলিট করছেন। Bot API-এর file_id
    ব্যবহার করা হয় বলে Personal Account-এর কোনো membership লাগে না — শুধু
    Bot নিজে Destination Channel-এ Admin/পোস্ট করার অনুমতি থাকলেই যথেষ্ট।"""
    while True:
        await asyncio.sleep(20)
        try:
            settings = load_settings()
            posts = settings.get("scheduled_posts", [])
            if not posts:
                continue
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")
            destinations = settings.get("destinations", [])
            changed = False
            for post in posts:
                if not post.get("enabled", True):
                    continue
                if post.get("time") != current_time:
                    continue
                if post.get("last_sent_date") == today:
                    continue
                if not destinations:
                    continue
                text = post.get("text") or ""
                media_type = post.get("media_type")
                file_id = post.get("file_id")
                for destination in destinations:
                    try:
                        if media_type == "photo":
                            await bot_app.bot.send_photo(destination, file_id, caption=text or None)
                        elif media_type == "video":
                            await bot_app.bot.send_video(destination, file_id, caption=text or None)
                        elif media_type == "document":
                            await bot_app.bot.send_document(destination, file_id, caption=text or None)
                        elif media_type == "animation":
                            await bot_app.bot.send_animation(destination, file_id, caption=text or None)
                        elif media_type == "audio":
                            await bot_app.bot.send_audio(destination, file_id, caption=text or None)
                        elif media_type == "voice":
                            await bot_app.bot.send_voice(destination, file_id, caption=text or None)
                        elif text:
                            await bot_app.bot.send_message(destination, text)
                        else:
                            continue
                        log_post("published")
                    except Exception as error:
                        print(f"⚠️ Scheduled post publish failed for {destination}: {error}")
                        await notify_publish_failed(bot_app.bot, destination, error)
                post["last_sent_date"] = today
                changed = True
            if changed:
                save_settings(settings)
        except Exception as error:
            print(f"⚠️ Scheduled posts worker error: {error}")
            await notify_access_issue(bot_app.bot, "scheduled_posts", error)


async def multi_watch_publish_worker():
    """Feature 4 — checks every 20s whether a configured schedule slot
    (e.g. 09:00 / 14:00 / 19:00) is due, and if so publishes the
    'smart-picked' best, non-duplicate buffered post(s) for that slot."""
    while True:
        await asyncio.sleep(20)
        try:
            settings = load_settings()
            watch = settings.get("multi_watch", {})
            if not watch.get("enabled"):
                continue
            now = datetime.now()
            state = multi_watch.load_state()
            slot = multi_watch.due_slot(watch.get("schedule_times", []), now, state)
            if not slot:
                continue

            candidates = multi_watch.read_buffer()
            if not candidates:
                multi_watch.mark_fired(state, slot, now)
                continue

            max_count = max(1, int(watch.get("max_posts_per_slot", 1)))
            chosen = multi_watch.pick_best_candidates(candidates, max_count, watch.get("similarity_skip", True))
            # Slot is marked fired even with nothing to send, so it doesn't
            # retry every 20s for the rest of the minute.
            multi_watch.mark_fired(state, slot, now)
            if not chosen:
                continue

            destinations = settings.get("destinations", [])
            if not destinations:
                await notify_access_issue(bot_app.bot, "multi_watch", RuntimeError("destination channel সেট করা নেই"))
                continue

            used_keys = set()
            for candidate in chosen:
                used_keys.add(f"{candidate['chat_id']}:{candidate['message_id']}")
                try:
                    source_message = await user_client.get_messages(int(candidate["chat_id"]), ids=candidate["message_id"])
                except (TypeError, ValueError):
                    source_message = await user_client.get_messages(candidate["chat_id"], ids=candidate["message_id"])
                if not source_message:
                    continue
                raw_text = source_message.text or source_message.message or ""
                final_text = await prepare_text(raw_text, settings)
                if not final_text and not source_message.media:
                    continue
                for destination in destinations:
                    try:
                        sent = await send_message(destination, source_message, final_text, settings)
                        if sent is None:
                            continue
                        asyncio.create_task(
                            forward_repeatedly(
                                destination,
                                sent.message_id,
                                settings.get("forward_groups", []),
                                settings.get("forwarding", {}),
                            )
                        )
                    except Exception as error:
                        print(f"⚠️ Multi-Watch publish failed for {destination}: {error}")
                        await notify_publish_failed(bot_app.bot, destination, error)
                multi_watch.record_published(raw_text)
                log_post("published")
            multi_watch.remove_from_buffer(used_keys)
        except Exception as error:
            print(f"⚠️ Multi-Watch scheduler error: {error}")
            await notify_access_issue(bot_app.bot, "multi_watch scheduler", error)


# ── Feature 1 — Personal Message System (sends with user_client, not bot) ──
async def personal_message_worker():
    while True:
        await asyncio.sleep(3)
        jobs = personal_messenger.pop_pending()
        if not jobs:
            continue
        if not user_client.is_connected() or not await user_client.is_user_authorized():
            await notify_session_expired(bot_app.bot)
            # put the jobs back so nothing is silently lost once session is fixed
            for job in jobs:
                personal_messenger.queue_message(job["chat_id"], job["text"], job.get("tag", ""))
            continue
        for job in jobs:
            try:
                chat_id = job["chat_id"]
                try:
                    chat_id = int(chat_id)
                except (TypeError, ValueError):
                    chat_id = str(chat_id).lstrip("@")
                await user_client.send_message(chat_id, job["text"])
                personal_messenger.mark_result(job["job_id"], True)
            except AuthKeyUnregisteredError:
                personal_messenger.mark_result(job["job_id"], False, "session expired")
                await notify_session_expired(bot_app.bot)
            except Exception as error:
                personal_messenger.mark_result(job["job_id"], False, str(error))
                print(f"⚠️ Personal message failed for {job.get('chat_id')}: {error}")
                await notify_access_issue(bot_app.bot, job.get("chat_id"), error)


# ── Feature 2/3 — heartbeat so main.py's /status command can report reality ──
async def heartbeat_worker():
    while True:
        try:
            authorized = user_client.is_connected() and await user_client.is_user_authorized()
        except Exception:
            authorized = False
        status_store.write_status(
            user_client_connected=user_client.is_connected(),
            user_client_authorized=authorized,
            ai_configured=bool(os.getenv("GROQ_API_KEY", "").strip()),
        )
        if not authorized:
            await notify_session_expired(bot_app.bot)
        await asyncio.sleep(60)


async def main():
    await bot_app.initialize()
    await bot_app.start()
    await user_client.connect()
    if not await user_client.is_user_authorized():
        await user_client.send_code_request(PHONE)
        print("📲 Telegram OTP পাঠানো হয়েছে। TELEGRAM_CODE সেট করে workflow আবার চালু করুন।", flush=True)
        code = os.getenv("TELEGRAM_CODE", "").strip()
        if not code:
            raise RuntimeError("TELEGRAM_CODE পাওয়া যায়নি")
        try:
            await user_client.sign_in(PHONE, code)
        except SessionPasswordNeededError:
            password = os.getenv("TELEGRAM_2FA_PASSWORD", "")
            if not password:
                raise RuntimeError("TELEGRAM_2FA_PASSWORD পাওয়া যায়নি")
            await user_client.sign_in(password=password)
    print("👀 settings.json-এর সব source channel দেখা হচ্ছে...")
    asyncio.create_task(personal_message_worker())
    asyncio.create_task(heartbeat_worker())
    asyncio.create_task(multi_watch_publish_worker())
    asyncio.create_task(scheduled_posts_worker())
    await user_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
