# main.py — অটো পোস্ট বটের অ্যাডমিন প্যানেল (সম্পূর্ণ বাংলা UI)
from dotenv import load_dotenv
load_dotenv()
import json
from config import DATA_DIR

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          filters, ContextTypes)

from config import BOT_TOKEN, ADMIN_IDS
from settings_store import load_settings, save_settings, ADMIN_PERMISSIONS
from ai_client import answer_group_message
import personal_messenger
import status_store
import multi_watch
from notifier import notify_admin, notify_ai_error

# ═══ স্টেট ও ডেটা ═══
settings = load_settings()
my_channels = {}
user_state = {}   # {user_id: {"step": ..., "type": ...}}
live_chat_history = {}   # {user_id: [last few "User: ..." / "AI: ..." lines]} — in-memory only


def base_ai_context() -> dict:
    """Shared AI identity + master rules + private knowledge, used by both
    Group AI replies and private Live Chat replies."""
    ai = settings.get("ai", {})
    return {
        "identity_name": ai.get("identity_name", ""),
        "owner_name": ai.get("owner_name", ""),
        "identity_filter": ai.get("identity_filter", ""),
        "master_instruction": ai.get("master_instruction", ""),
        "private_knowledge": ai.get("private_knowledge", ""),
    }

# ═══ ফাইল-ভিত্তিক স্টেট ও স্ট্যাটস (আসল, কাজ করে) ═══
def set_autopost(on: bool):
    settings["autopost"] = on
    save_settings(settings)

def read_stats():
    pub = skip = 0
    try:
        with open(DATA_DIR / "posts.log") as f:
            for line in f:
                if line.strip() == "published": pub += 1
                elif line.strip() == "skipped": skip += 1
    except FileNotFoundError:
        pass
    return pub, skip

# ═══ UI: মেনুগুলো ═══
def main_kb(uid=None):
    rows = [
        ["📡 চ্যানেল সেটিংস"],
        ["🛡️ প্রাইভেসি ফিল্টার"],
        ["📝 অটো পোস্ট", "📊 পরিসংখ্যান"],
        ["🤖 AI সেটিংস", "📩 User Messaging"],
        ["📢 Channel → Group", "🧭 Multi-Channel Watch"],
        ["⚙️ সেটিংস", "❓ সাহায্য"],
    ]
    # Feature 9 — Admin Management বাটন শুধু Super Owner-কে দেখানো হয়।
    if uid is not None and is_super_owner(uid):
        rows.insert(-1, ["👑 Admin Management"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def channel_kb():
    return ReplyKeyboardMarkup([
        ["🏠 Destination", "📡 Source"],
        ["➕ Source যোগ", "➖ Source বাদ"],
        ["➕ Destination যোগ", "➖ Destination বাদ"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)

def multi_watch_kb():
    # Feature 4 — Multi-Channel Watch + Smart Scheduling menu.
    return ReplyKeyboardMarkup([
        ["🟢 Multi Watch চালু", "🔴 Multi Watch বন্ধ"],
        ["➕ Watch Channel যোগ", "➖ Watch Channel বাদ"],
        ["📋 Watch Channel তালিকা", "📥 Buffer Status"],
        ["⏰ Schedule Times", "🔢 Max Posts/Slot"],
        ["🔁 Similarity Filter ON/OFF"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)

def privacy_kb():
    return ReplyKeyboardMarkup([
        ["👤 @username", "📞 ফোন"],
        ["✉️ ইমেইল", "🔗 t.me লিংক"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)

def autopost_kb():
    return ReplyKeyboardMarkup([
        ["🟢 চালু করুন", "🔴 বন্ধ করুন"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)

def settings_kb():
    return ReplyKeyboardMarkup([
        ["⏱️ ডিলে সেট", "📝 টেমপ্লেট"],
        ["🔁 Forward সেটিংস", "📇 Personal তথ্য"],
        ["🖼️ মিডিয়া ফিল্টার"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)


def media_filter_kb():
    return ReplyKeyboardMarkup([
        ["🟢 Image চালু", "🔴 Image বন্ধ"],
        ["🟢 File চালু", "🔴 File বন্ধ"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)


def ai_kb():
    return ReplyKeyboardMarkup([
        ["🟢 AI Editing চালু", "🔴 AI Editing বন্ধ"],
        ["🎨 AI Style", "🧠 Custom Prompt"],
        ["📏 Post Length", "✨ AI Emoji ON/OFF"],
        ["🎭 AI পরিচয় সেটিংস"],
        ["👥 Group AI সেটিংস", "💬 Live Chat সেটিংস"],
        ["📚 Private Knowledge", "👋 Welcome সেটিংস"],
        ["🔤 Word Filter", "🎨 Post Format Style"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)


def live_chat_kb():
    return ReplyKeyboardMarkup([
        ["🟢 Live Chat চালু", "🔴 Live Chat বন্ধ"],
        ["🎨 Chat Style", "🧠 Chat Prompt"],
        ["🔁 Context ON/OFF"],
        ["⬅️ AI সেটিংসে ফিরুন"],
    ], resize_keyboard=True)


def welcome_kb():
    return ReplyKeyboardMarkup([
        ["🟢 Welcome চালু", "🔴 Welcome বন্ধ"],
        ["✏️ Welcome বার্তা"],
        ["⬅️ AI সেটিংসে ফিরুন"],
    ], resize_keyboard=True)


def word_filter_kb():
    return ReplyKeyboardMarkup([
        ["➕ Filter যোগ", "➖ Filter বাদ"],
        ["📋 Filter তালিকা"],
        ["⬅️ AI সেটিংসে ফিরুন"],
    ], resize_keyboard=True)


def ai_identity_kb():
    return ReplyKeyboardMarkup([
        ["🤖 AI-র নাম", "👑 Owner-এর নাম"],
        ["🚫 অতিরিক্ত Filter/নিষেধ"],
        ["📄 Master নির্দেশনা (Text/File)"],
        ["⬅️ AI সেটিংসে ফিরুন"],
    ], resize_keyboard=True)


def user_message_kb():
    return ReplyKeyboardMarkup([
        ["👥 User List", "➕ User যোগ"],
        ["📝 Common Message", "⏰ Schedule"],
        ["📩 এখন পাঠান", "🟢 Campaign চালু"],
        ["🔴 Campaign বন্ধ", "🗑️ User সরান"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)


def channel_group_kb():
    return ReplyKeyboardMarkup([
        ["➕ Group যোগ করুন", "👥 Group List"],
        ["🎯 Select Group"],
        ["⚙️ Forward Settings", "🔢 Forward Count"],
        ["⏱️ Delay", "📅 Schedule"],
        ["🤖 AI Editing", "🟢 C→G চালু"],
        ["🔴 C→G বন্ধ", "⏸️ Pause"],
        ["▶️ Resume", "📊 Status"],
        ["📝 History", "⬅️ ফিরে যান"],
    ], resize_keyboard=True)


def selected_forward_group():
    forwarding = settings["channel_group_forwarding"]
    return forwarding["groups"].get(str(forwarding.get("selected_group", "")))


def read_forward_history():
    path = DATA_DIR / "channel_group_forward_history.jsonl"
    rows = []
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    rows.append(json.loads(line))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return rows

# ═══ Feature 9 — Multi Admin System ═══
# ADMIN_IDS (from env) are the permanent "Super Owner"s — always full access,
# can never be removed from the bot itself. Extra admins live in
# settings["admins"] and can be added/removed/permission-limited by a Super
# Owner from inside the bot.
def is_super_owner(uid) -> bool:
    if uid in ADMIN_IDS:
        return True
    record = settings.get("admins", {}).get(str(uid))
    return bool(record and record.get("role") == "super_owner")


def is_admin(uid) -> bool:
    return uid in ADMIN_IDS or str(uid) in settings.get("admins", {})


def has_permission(uid, permission: str) -> bool:
    if is_super_owner(uid):
        return True
    record = settings.get("admins", {}).get(str(uid))
    if not record:
        return False
    return bool(record.get("permissions", {}).get(permission, False))


PERMISSION_LABELS = {
    "channel_manage": "📡 Channel Manage",
    "schedule_manage": "📅 Schedule Manage",
    "ai_settings": "🤖 AI Settings",
    "all_data_manage": "📚 All Data Manage",
    "bot_settings": "⚙️ Bot Settings",
    "user_account_control": "👤 User Account Control",
}


def admin_kb():
    return ReplyKeyboardMarkup([
        ["➕ Admin যোগ", "🗑️ Admin সরান"],
        ["👥 Admin তালিকা", "🔐 Permission সেট"],
        ["⬅️ ফিরে যান"],
    ], resize_keyboard=True)


MENU_PERMISSION = {
    "📡 চ্যানেল সেটিংস": "channel_manage",
    "🏠 Destination": "channel_manage",
    "📡 Source": "channel_manage",
    "➕ Source যোগ": "channel_manage",
    "➖ Source বাদ": "channel_manage",
    "➕ Destination যোগ": "channel_manage",
    "➖ Destination বাদ": "channel_manage",
    "📢 Channel → Group": "channel_manage",
    "➕ Group যোগ করুন": "channel_manage",
    "👥 Group List": "channel_manage",
    "🎯 Select Group": "channel_manage",
    "⚙️ Forward Settings": "channel_manage",
    "🔢 Forward Count": "schedule_manage",
    "⏱️ Delay": "schedule_manage",
    "📅 Schedule": "schedule_manage",
    "🤖 AI সেটিংস": "ai_settings",
    "🎨 Post Format Style": "ai_settings",
    "📚 Private Knowledge": "all_data_manage",
    "⚙️ সেটিংস": "bot_settings",
    "📩 User Messaging": "user_account_control",
    "🧭 Multi-Channel Watch": "channel_manage",
    # "👑 Admin Management" is intentionally NOT here — it is gated by
    # is_super_owner() directly below, never by a delegable permission.
}


def format_style_kb():
    return ReplyKeyboardMarkup([
        ["🟢 Format Style চালু", "🔴 Format Style বন্ধ"],
        ["🎯 Border", "📰 Header"],
        ["🔻 Footer", "📩 Contact Line"],
        ["🔘 Bullet ON/OFF", "🔤 Emoji Heading ON/OFF"],
        ["⬅️ AI সেটিংসে ফিরুন"],
    ], resize_keyboard=True)


async def optin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    key = str(user.id)
    username_key = (user.username or "").lstrip("@")
    users = settings.setdefault("users", {})
    # যদি admin আগে থেকে @username দিয়ে pre-add করে রাখে, সেই পুরনো enable/status ধরে রেখে
    # numeric ID-তে merge করে দেওয়া হচ্ছে — send_message-এর জন্য numeric ID-ই আসল key।
    existing = users.pop(username_key, None) if username_key and username_key in users and username_key != key else None
    record = users.setdefault(key, existing or {})
    record.update({
        "id": user.id,
        "username": user.username or "",
        "name": user.full_name or "",
        "opted_in": True,
        "enabled": record.get("enabled", True),
        "status": "ready",
    })
    campaign_ids = settings.setdefault("user_campaign", {}).setdefault("user_ids", [])
    if username_key and username_key in campaign_ids:
        campaign_ids.remove(username_key)
    if key not in campaign_ids:
        campaign_ids.append(key)
    save_settings(settings)
    await update.message.reply_text("✅ আপনি opt-in করেছেন। এখন থেকে Admin-এর অনুমোদিত message পেতে পারেন।")


async def optout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    record = settings.setdefault("users", {}).setdefault(str(user.id), {})
    record.update({"id": user.id, "opted_in": False, "enabled": False, "status": "opted_out"})
    save_settings(settings)
    await update.message.reply_text("✅ আপনি opt-out করেছেন। আর campaign message পাঠানো হবে না।")


async def manage_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ অনুমতি নেই।")
        return
    command = update.message.text.split()[0].lower()
    value = update.message.text.split(maxsplit=1)[1].strip().lstrip("@") if len(update.message.text.split()) > 1 else ""
    if not value:
        await update.message.reply_text("User ID বা @username দিন।")
        return
    record = settings.setdefault("users", {}).get(value)
    if not record:
        await update.message.reply_text("⚠️ User তালিকায় পাওয়া যায়নি।")
        return
    if command == "/useron":
        record["enabled"] = True
        record["status"] = "ready"
        message = "✅ User চালু হয়েছে।"
    elif command == "/useroff":
        record["enabled"] = False
        record["status"] = "disabled"
        message = "✅ User বন্ধ হয়েছে।"
    elif command == "/userremove":
        settings["users"].pop(value, None)
        settings["user_campaign"]["user_ids"] = [item for item in settings["user_campaign"]["user_ids"] if str(item) != value]
        message = "✅ User সরানো হয়েছে।"
    elif command == "/userstatus":
        live_status = record.get("status", "unknown")
        if record.get("job_id"):
            job = personal_messenger.get_status(record["job_id"])
            live_status = job.get("status", live_status)
            if job.get("error"):
                live_status += f" ({job['error'][:80]})"
        message = f"User: {value}\nOpt-in: {record.get('opted_in', False)}\nEnabled: {record.get('enabled', False)}\nStatus (Personal Account): {live_status}"
    elif command == "/retry":
        if not record.get("opted_in") or not record.get("enabled", True):
            message = "⚠️ User opt-in করেনি অথবা বন্ধ আছে।"
        else:
            # Feature 1 — personal message এখন Bot Token নয়, user_client
            # (Personal Account) দিয়ে যায়। userbot.py প্রসেসটা queue তুলে
            # আসল পাঠানোর কাজ করে; এখানে শুধু job queue করা হচ্ছে।
            job_id = personal_messenger.queue_message(
                record.get("id", value), settings["user_campaign"]["message"], tag="retry")
            record["status"] = "queued"
            record["job_id"] = job_id
            message = "📤 Retry queue করা হয়েছে — Personal Account থেকে পাঠানো হচ্ছে। কিছুক্ষণ পর /userstatus দিয়ে দেখুন।"
    else:
        return
    save_settings(settings)
    await update.message.reply_text(message, reply_markup=user_message_kb())


async def handle_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    group = settings.setdefault("group_ai", {}).get(chat_id, {})
    if not group.get("enabled", False):
        return
    text = message.text.strip()
    mode = group.get("reply_mode", "question")
    mentioned = ctx.bot.username and f"@{ctx.bot.username.lower()}" in text.lower()
    replied_to_bot = bool(message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_bot)
    if mode == "mention" and not mentioned:
        return
    if mode == "reply" and not replied_to_bot:
        return
    if mode == "ask" and not text.lower().startswith("/ask"):
        return
    if mode == "question" and not (text.endswith(("?", "？")) or "?" in text):
        return
    if mode == "ask":
        text = text[4:].strip()
    group_context = {**base_ai_context(), **group}
    try:
        answer = await answer_group_message(text, group_context, "")
        await message.reply_text(answer[:4000], disable_web_page_preview=True)
    except Exception as error:
        print(f"⚠️ AI ERROR group={chat_id}: {repr(error)}", flush=True)
        await message.reply_text("⚠️ AI উত্তর দিতে পারেনি। কিছুক্ষণ পর আবার চেষ্টা করুন।")
        with open(DATA_DIR / "ai_errors.log", "a", encoding="utf-8") as file:
            file.write(f"group={chat_id} error={error}\n")
        await notify_ai_error(ctx.bot, error)


async def handle_user_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Live AI Chat for regular (non-admin) users in private messages."""
    live = settings.get("live_chat", {})
    if not live.get("enabled"):
        await update.message.reply_text("❌ অনুমতি নেই।")
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    uid = update.effective_user.id
    history_list = live_chat_history.setdefault(uid, [])
    context_text = "\n".join(history_list[-6:]) if live.get("context_enabled", True) else ""
    chat_context = {**base_ai_context(), **live}
    try:
        answer = await answer_group_message(text, chat_context, context_text)
    except Exception as error:
        print(f"⚠️ Live chat AI error uid={uid}: {repr(error)}", flush=True)
        await update.message.reply_text("⚠️ এই মুহূর্তে উত্তর দেওয়া যাচ্ছে না। একটু পর আবার চেষ্টা করুন।")
        with open(DATA_DIR / "ai_errors.log", "a", encoding="utf-8") as file:
            file.write(f"live_chat uid={uid} error={error}\n")
        await notify_ai_error(ctx.bot, error)
        return
    await update.message.reply_text(answer[:4000], disable_web_page_preview=True)
    history_list.append(f"User: {text}")
    history_list.append(f"AI: {answer}")
    live_chat_history[uid] = history_list[-12:]


async def handle_new_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """👋 Welcome System — greets new members joining a group the bot is in."""
    welcome = settings.get("welcome", {})
    if not welcome.get("enabled"):
        return
    message = update.message
    if not message or not message.new_chat_members:
        return
    template = welcome.get("message") or "স্বাগতম {name}! 🎉"
    for member in message.new_chat_members:
        if member.id == ctx.bot.id:
            continue
        name = member.full_name or member.first_name or "বন্ধু"
        try:
            await ctx.bot.send_message(chat_id=update.effective_chat.id, text=template.replace("{name}", name))
        except Exception as error:
            print(f"⚠️ Welcome message failed: {error}")


async def send_campaign(bot):
    """Feature 1 — এই System বরাবরের মতোই কাজ করে (একই বাটন/ওয়ার্কফ্লো),
    শুধু পাঠানোর কাজটা এখন Bot Token দিয়ে না করে Personal Account
    (user_client, userbot.py) দিয়ে করানো হয় — personal_messenger.py-এর
    queue-এর মাধ্যমে।
    """
    campaign = settings.get("user_campaign", {})
    message = campaign.get("message", "").strip()
    if not message:
        return
    delay = max(0, int(campaign.get("delay_minutes", 0)))
    for value in campaign.get("user_ids", []):
        record = settings.get("users", {}).get(str(value), {})
        if not record.get("opted_in") or not record.get("enabled", True):
            continue
        job_id = personal_messenger.queue_message(record.get("id", value), message, tag="campaign")
        record["status"] = "queued"
        record["job_id"] = job_id
        save_settings(settings)
        if delay:
            import asyncio
            await asyncio.sleep(delay * 60)


async def campaign_loop(application):
    import asyncio
    while True:
        await asyncio.sleep(60)
        if settings.get("user_campaign", {}).get("enabled"):
            await send_campaign(application.bot)
            settings["user_campaign"]["enabled"] = False
            save_settings(settings)


async def post_init(application):
    import asyncio
    asyncio.create_task(campaign_loop(application))

# ═══ হ্যান্ডলার ═══
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Feature 3 — /start এখন বটের পরিচয়, মূল Feature ও ব্যবহারের নিয়ম দেখায়।
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "👋 স্বাগতম! এটি একটি প্রাইভেট অটো-পোস্ট/AI বট।\n"
            "শুধুমাত্র Admin এই বট পরিচালনা করতে পারে।\n\n"
            "Admin আপনাকে opt-in করতে বললে /optin কমান্ড ব্যবহার করুন।")
        return
    await update.message.reply_text(
        "👋 স্বাগতম, Admin!\n\n"
        "🤖 এটি আপনার Telegram Auto-Post & AI বট।\n\n"
        "মূল Feature সমূহ:\n"
        "📡 একাধিক Source Channel মনিটর করে নতুন পোস্ট Auto-Publish\n"
        "🤖 AI দিয়ে পোস্ট Edit/Formatting (Groq)\n"
        "🛡️ ব্যক্তিগত তথ্য (username/phone/email/link) auto-filter\n"
        "📢 Channel → Group Forward (repeat/schedule সহ)\n"
        "📩 Opt-in User Messaging (Personal Account থেকে পাঠানো হয়)\n"
        "👋 Group Welcome + Group/Live AI Chat\n"
        "🚨 কোনো সমস্যা হলে Bot নিজে থেকেই আপনাকে জানাবে\n\n"
        "ব্যবহারের নিয়ম:\n"
        "১. নিচের মেনু বাটন ব্যবহার করে সব সেটিংস ধাপে ধাপে করুন — কোনো কমান্ড মুখস্থ করতে হবে না।\n"
        "২. দরকারি কমান্ড: /help (সাহায্য), /status (বটের বর্তমান অবস্থা)।\n\n"
        "নিচের বাটন থেকে শুরু করুন।",
        reply_markup=main_kb(update.effective_user.id))


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Feature 3 — /help: গুরুত্বপূর্ণ Command ও Feature সম্পর্কে সাহায্য।
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❓ /optin — Admin-এর message পাওয়ার জন্য opt-in করুন\n"
            "❓ /optout — Opt-out করুন")
        return
    await update.message.reply_text(
        "❓ সাহায্য / Command তালিকা\n\n"
        "/start — বটের পরিচয় ও মূল Feature\n"
        "/help — এই সাহায্য বার্তা\n"
        "/status — Bot, Personal Account (user client), AI ইত্যাদির বর্তমান Status\n\n"
        "User Messaging Command:\n"
        "/useron <id/username> — নির্দিষ্ট user চালু করুন\n"
        "/useroff <id/username> — নির্দিষ্ট user বন্ধ করুন\n"
        "/userremove <id/username> — User তালিকা থেকে বাদ দিন\n"
        "/userstatus <id/username> — User-এর ডেলিভারি status দেখুন\n"
        "/retry <id/username> — আবার message পাঠানোর চেষ্টা করুন\n\n"
        "মূল কাজগুলোর জন্য মেনু বাটন ব্যবহার করাই সহজ পথ:\n"
        "📡 চ্যানেল সেটিংস → Source/Destination যোগ\n"
        "🛡️ প্রাইভেসি ফিল্টার → ব্যক্তিগত তথ্য filter\n"
        "🤖 AI সেটিংস → AI Editing, Group AI, Live Chat, Private Knowledge\n"
        "📢 Channel → Group → Forward সেটিংস\n"
        "⚙️ সেটিংস → Delay, Template, মিডিয়া ফিল্টার\n"
        "🎨 AI সেটিংস → Post Format Style → Custom post format\n"
        + ("👑 Admin Management → Admin যোগ/সরান, Permission সেট\n" if is_super_owner(update.effective_user.id) else ""),
        reply_markup=main_kb(update.effective_user.id))


async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Feature 3 — /status: Bot, User Client, AI ও গুরুত্বপূর্ণ Service-এর অবস্থা।
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ অনুমতি নেই।")
        return
    live = status_store.read_status()
    userbot_alive = live.get("userbot_alive", False)
    user_client_authorized = live.get("user_client_authorized", False)
    ai_configured = live.get("ai_configured", False)
    pub, skip = read_stats()

    def mark(ok):
        return "🟢 চালু/ঠিক আছে" if ok else "🔴 সমস্যা/বন্ধ"

    lines = [
        "📊 Bot Status",
        "",
        f"🤖 Admin Panel (main.py): {mark(True)}",
        f"👤 Personal Account (user client): {mark(userbot_alive and user_client_authorized)}",
        f"🔌 Userbot Process: {mark(userbot_alive)}",
        f"🧠 AI Service (Groq): {mark(ai_configured)} — এই বটে {'চালু' if settings.get('ai', {}).get('enabled') else 'বন্ধ'}",
        f"📝 Auto Post: {'🟢 চালু' if settings.get('autopost') else '🔴 বন্ধ'}",
        f"📡 Source Channel: {len(settings.get('sources', []))} টি",
        f"🏠 Destination Channel: {len(settings.get('destinations', []))} টি",
        f"📈 এখন পর্যন্ত Publish: {pub} | Skip: {skip}",
    ]
    if not userbot_alive:
        lines.append("\n⚠️ Userbot process থেকে সাড়া পাওয়া যাচ্ছে না — Server/Deployment চেক করুন।")
    elif not user_client_authorized:
        lines.append("\n⚠️ Personal Account-এর Session/Login সমস্যা আছে — আবার login করুন।")
    await update.message.reply_text("\n".join(lines), reply_markup=main_kb(update.effective_user.id))

async def handle_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fwd = update.message.forward_from_chat
    state = user_state.get(update.effective_user.id, {})
    if not fwd or state.get("step") != "await_channel" or state.get("type") != "destination_add":
        await update.message.reply_text("💡 Destination যোগ করতে আগে '➕ Destination যোগ' চাপুন।")
        return
    if fwd.id not in settings["destinations"]:
        settings["destinations"].append(fwd.id)
    save_settings(settings)
    user_state.pop(update.effective_user.id, None)
    await update.message.reply_text(
        f"✅ Destination যোগ হয়েছে!\n\n📛 নাম: {fwd.title}\n🆔 ID: {fwd.id}",
        reply_markup=channel_kb())

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    step = user_state.get(uid, {}).get("step")
    if step not in ("await_master_instruction", "await_private_knowledge"):
        return
    doc = update.message.document
    if not doc:
        return
    if doc.file_size and doc.file_size > 200_000:
        await update.message.reply_text("⚠️ ফাইল খুব বড় (সর্বোচ্চ ~200KB)। ছোট .txt ফাইল পাঠান।")
        return
    try:
        file = await ctx.bot.get_file(doc.file_id)
        raw = await file.download_as_bytearray()
        content = bytes(raw).decode("utf-8", errors="replace").strip()
    except Exception as error:
        await update.message.reply_text(f"❌ ফাইল পড়তে সমস্যা হয়েছে: {error}")
        return
    if not content:
        await update.message.reply_text("⚠️ ফাইলটা খালি মনে হচ্ছে।")
        return
    field = "master_instruction" if step == "await_master_instruction" else "private_knowledge"
    label = "Master নির্দেশনা" if field == "master_instruction" else "Private Knowledge"
    kb = ai_identity_kb() if field == "master_instruction" else ai_kb()
    settings["ai"][field] = content[:8000]
    save_settings(settings)
    user_state.pop(uid, None)
    await update.message.reply_text(
        f"✅ ফাইল থেকে {label} সেভ হয়েছে ({len(content)} অক্ষর)। AI এখন থেকে এই তথ্য/নিয়ম মেনে চলবে।",
        reply_markup=kb)


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    uid = update.effective_user.id
    if not is_admin(uid):
        await handle_user_chat(update, ctx)
        return

    # Feature 9 — limited admins only reach sections their permissions allow.
    # Super Owner is never restricted.
    required = MENU_PERMISSION.get(t)
    if required and not has_permission(uid, required):
        await update.message.reply_text(
            f"❌ আপনার এই অংশে ({PERMISSION_LABELS.get(required, required)}) অনুমতি নেই। "
            "Super Owner-কে জিজ্ঞেস করুন।",
            reply_markup=main_kb(uid))
        return

    if uid in user_state and user_state[uid]["step"] == "await_campaign_message":
        settings["user_campaign"]["message"] = t
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Common message সেভ হয়েছে।", reply_markup=user_message_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_campaign_delay":
        if not t.strip().isdigit():
            await update.message.reply_text("⚠️ শুধু মিনিট সংখ্যা লিখুন।")
            return
        settings["user_campaign"]["delay_minutes"] = int(t.strip())
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Schedule delay সেভ হয়েছে।", reply_markup=user_message_kb())
        return

    if uid in user_state and user_state[uid]["step"] in ("await_ai_style", "await_ai_prompt", "await_ai_length"):
        field = {"await_ai_style": "style", "await_ai_prompt": "custom_prompt", "await_ai_length": "length"}[user_state[uid]["step"]]
        settings["ai"][field] = "" if t.strip() in ("না", "-", "খালি") else t.strip()
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ AI সেটিংস সেভ হয়েছে।", reply_markup=ai_kb())
        return

    if uid in user_state and user_state[uid]["step"] in ("await_ai_identity_name", "await_ai_owner_name", "await_ai_identity_filter"):
        field = {
            "await_ai_identity_name": "identity_name",
            "await_ai_owner_name": "owner_name",
            "await_ai_identity_filter": "identity_filter",
        }[user_state[uid]["step"]]
        settings["ai"][field] = "" if t.strip() in ("না", "-", "খালি") else t.strip()
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ AI পরিচয় সেটিংস সেভ হয়েছে।", reply_markup=ai_identity_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_master_instruction":
        if t.strip() in ("না", "-", "খালি"):
            settings["ai"]["master_instruction"] = ""
        else:
            settings["ai"]["master_instruction"] = t.strip()[:8000]
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(
            "✅ Master নির্দেশনা সেভ হয়েছে। AI এখন থেকে এই নিয়ম মেনে সব জায়গায় কাজ করবে।",
            reply_markup=ai_identity_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_private_knowledge":
        if t.strip() in ("না", "-", "খালি"):
            settings["ai"]["private_knowledge"] = ""
        else:
            settings["ai"]["private_knowledge"] = t.strip()[:8000]
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(
            "✅ Private Knowledge সেভ হয়েছে। Group AI ও Live Chat উত্তরের সময় প্রয়োজনে এই তথ্য ব্যবহার করবে।",
            reply_markup=ai_kb())
        return

    # ── Feature 10 — Custom AI Post Format Style text inputs ──
    if uid in user_state and user_state[uid]["step"] == "await_format_field":
        field = user_state[uid]["field"]
        fs = settings.setdefault("format_style", {})
        fs[field] = "" if t.strip() in ("না", "-", "খালি") else t.strip()[:500]
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Format Style সেভ হয়েছে।", reply_markup=format_style_kb())
        return

    # ── Feature 9 — Multi Admin System text inputs (Super Owner only) ──
    if uid in user_state and user_state[uid]["step"] == "await_admin_add":
        user_state.pop(uid, None)
        if not is_super_owner(uid):
            await update.message.reply_text("❌ অনুমতি নেই।", reply_markup=main_kb(uid))
            return
        raw = t.strip()
        if not raw.isdigit():
            await update.message.reply_text("⚠️ শুধু numeric Telegram user ID দিন (@username নয়)।")
            return
        settings.setdefault("admins", {})[raw] = {
            "role": "admin",
            "name": "",
            "permissions": {perm: False for perm in ADMIN_PERMISSIONS},
            "added_by": str(uid),
        }
        save_settings(settings)
        await update.message.reply_text(
            f"✅ Admin যোগ হয়েছে: {raw}\n\n"
            "এখন 🔐 Permission সেট থেকে তার Permission ঠিক করুন — নতুন Admin ডিফল্টভাবে কোনো Permission পায় না।",
            reply_markup=admin_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_admin_remove":
        user_state.pop(uid, None)
        if not is_super_owner(uid):
            await update.message.reply_text("❌ অনুমতি নেই।", reply_markup=main_kb(uid))
            return
        raw = t.strip()
        if settings.get("admins", {}).pop(raw, None) is None:
            await update.message.reply_text("⚠️ এই ID Admin তালিকায় পাওয়া যায়নি।", reply_markup=admin_kb())
        else:
            save_settings(settings)
            await update.message.reply_text(f"✅ Admin সরানো হয়েছে: {raw}", reply_markup=admin_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_admin_perm_target":
        raw = t.strip()
        if raw not in settings.get("admins", {}):
            await update.message.reply_text("⚠️ এই ID Admin তালিকায় পাওয়া যায়নি। আবার চেষ্টা করুন।")
            return
        user_state[uid] = {"step": "await_admin_perm_toggle", "target": raw}
        rows = [f"{PERMISSION_LABELS[p]} — {'🟢' if settings['admins'][raw]['permissions'].get(p) else '🔴'}" for p in ADMIN_PERMISSIONS]
        await update.message.reply_text(
            "যে Permission-টি ON/OFF করতে চান, তার নাম হুবহু লিখুন:\n\n" + "\n".join(rows))
        return

    if uid in user_state and user_state[uid]["step"] == "await_admin_perm_toggle":
        target = user_state[uid]["target"]
        matched = next((p for p in ADMIN_PERMISSIONS if PERMISSION_LABELS[p] == t.strip()), None)
        if not matched:
            await update.message.reply_text("⚠️ তালিকা থেকে হুবহু নাম লিখুন, অথবা ⬅️ ফিরে যান চাপুন।")
            return
        record = settings.setdefault("admins", {}).setdefault(target, {"role": "admin", "permissions": {}})
        perms = record.setdefault("permissions", {})
        perms[matched] = not perms.get(matched, False)
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(
            f"✅ {target}-এর {PERMISSION_LABELS[matched]}: {'🟢 চালু' if perms[matched] else '🔴 বন্ধ'}",
            reply_markup=admin_kb())
        return

    # ── Feature 4 — Multi-Channel Watch text inputs ──
    if uid in user_state and user_state[uid]["step"] == "await_mw_channel":
        action = user_state[uid]["action"]
        user_state.pop(uid, None)
        value = t.strip()
        if not value.lstrip("-").isdigit():
            value = value if value.startswith("@") else f"@{value}"
        channels = settings.setdefault("multi_watch", {}).setdefault("channels", [])
        if action == "add":
            if value not in channels:
                channels.append(value)
            message = f"✅ Watch Channel যোগ হয়েছে: {value}"
        else:
            if value in channels:
                channels.remove(value)
                message = f"✅ Watch Channel বাদ দেওয়া হয়েছে: {value}"
            else:
                message = "⚠️ তালিকায় পাওয়া যায়নি।"
        save_settings(settings)
        await update.message.reply_text(message, reply_markup=multi_watch_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_mw_schedule":
        raw_times = [chunk.strip() for chunk in t.split(",") if chunk.strip()]
        valid = []
        for chunk in raw_times:
            try:
                hh, mm = chunk.split(":")
                if 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
                    valid.append(f"{int(hh):02d}:{int(mm):02d}")
            except ValueError:
                continue
        if not valid:
            await update.message.reply_text("⚠️ সঠিক ফরম্যাট দিন, যেমন: 09:00,14:00,19:00")
            return
        settings.setdefault("multi_watch", {})["schedule_times"] = valid
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(f"✅ Schedule Times সেট হয়েছে: {', '.join(valid)}", reply_markup=multi_watch_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_mw_maxposts":
        if not t.strip().isdigit() or int(t.strip()) < 1:
            await update.message.reply_text("⚠️ শুধু ১ বা তার বেশি সংখ্যা লিখুন।")
            return
        settings.setdefault("multi_watch", {})["max_posts_per_slot"] = int(t.strip())
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(f"✅ Max Posts/Slot সেট হয়েছে: {t.strip()}", reply_markup=multi_watch_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_live_chat_field":
        field = user_state[uid]["field"]
        lc = settings.setdefault("live_chat", {})
        lc[field] = "" if t.strip() in ("না", "-", "খালি") else t.strip()
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Live Chat সেটিংস সেভ হয়েছে।", reply_markup=live_chat_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_welcome_message":
        welcome = settings.setdefault("welcome", {})
        welcome["message"] = "" if t.strip() in ("না", "-", "খালি") else t.strip()
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Welcome বার্তা সেভ হয়েছে।", reply_markup=welcome_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_word_filter_add":
        if "=>" not in t:
            await update.message.reply_text("⚠️ Format ভুল। উদাহরণ: পুরনো_শব্দ => নতুন_শব্দ")
            return
        find_part, replace_part = t.split("=>", 1)
        find_part, replace_part = find_part.strip(), replace_part.strip()
        if not find_part:
            await update.message.reply_text("⚠️ খুঁজবে অংশ খালি রাখা যাবে না।")
            return
        settings.setdefault("word_filters", []).append({"find": find_part, "replace": replace_part})
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Filter যোগ হয়েছে।", reply_markup=word_filter_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_word_filter_remove":
        filters_list = settings.get("word_filters", [])
        value = t.strip()
        if not value.isdigit() or not (1 <= int(value) <= len(filters_list)):
            await update.message.reply_text("⚠️ 📋 Filter তালিকা থেকে সঠিক নম্বর লিখুন।")
            return
        removed = filters_list.pop(int(value) - 1)
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(f"✅ বাদ দেওয়া হয়েছে: {removed['find']}", reply_markup=word_filter_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_users":
        values = [item.strip() for item in t.replace(",", "\n").splitlines() if item.strip()]
        for value in values:
            key = value.lstrip("@")
            if key not in settings["user_campaign"]["user_ids"]:
                settings["user_campaign"]["user_ids"].append(key)
            settings.setdefault("users", {}).setdefault(key, {
                "id": int(key) if key.isdigit() else key,
                "username": value if value.startswith("@") else "",
                "opted_in": False,
                "enabled": True,
                "status": "waiting for opt-in",
            })
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text(f"✅ {len(values)} জন user যোগ হয়েছে। শুধু /optin করা user-দের message যাবে।", reply_markup=user_message_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_user_remove":
        key = t.strip().lstrip("@")
        campaign_ids = settings.get("user_campaign", {}).get("user_ids", [])
        if key in campaign_ids:
            campaign_ids.remove(key)
            settings.get("users", {}).pop(key, None)
            save_settings(settings)
            message = f"✅ User সরানো হয়েছে: {key}"
        else:
            message = "⚠️ User তালিকায় পাওয়া যায়নি।"
        user_state.pop(uid, None)
        await update.message.reply_text(message, reply_markup=user_message_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_group_config":
        parts = t.strip().split(maxsplit=2)
        if len(parts) < 2:
            await update.message.reply_text("উদাহরণ: -100123456789 on mention")
            return
        chat_id, enabled = parts[0], parts[1].lower() in ("on", "চালু", "1", "true")
        settings["group_ai"][chat_id] = {
            "enabled": enabled,
            "reply_mode": parts[2] if len(parts) > 2 else "question",
            "style": "সহায়ক, ভদ্র ও সংক্ষিপ্ত",
            "answer_length": "মাঝারি",
            "context_enabled": True,
            "custom_prompt": "",
        }
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Group AI সেটিংস আপডেট হয়েছে।", reply_markup=ai_kb())
        return

    if uid in user_state and user_state[uid]["step"].startswith("await_cg_"):
        step = user_state[uid]["step"]
        value = t.strip()
        forwarding = settings["channel_group_forwarding"]
        if step == "await_cg_add":
            group = value if value.startswith("@") else (int(value) if value.lstrip("-").isdigit() else f"@{value}")
            key = str(group)
            forwarding["groups"].setdefault(key, {
                "enabled": True, "paused": False, "count": 1, "delay_seconds": 0,
                "ai_enabled": False, "schedule": {"enabled": False, "start": "00:00", "end": "23:59"},
                "status": "active",
            })
            forwarding["selected_group"] = key
            save_settings(settings)
            user_state.pop(uid, None)
            await update.message.reply_text(f"✅ Group যোগ হয়েছে এবং selected হয়েছে: {key}", reply_markup=channel_group_kb())
            return
        if step == "await_cg_select":
            key = value if value in forwarding["groups"] else (f"@{value}" if f"@{value}" in forwarding["groups"] else value)
            if key not in forwarding["groups"]:
                await update.message.reply_text("⚠️ এই Group list-এ নেই।", reply_markup=channel_group_kb())
                return
            forwarding["selected_group"] = key
            save_settings(settings)
            user_state.pop(uid, None)
            await update.message.reply_text(f"✅ Selected Group: {key}", reply_markup=channel_group_kb())
            return
        group = selected_forward_group()
        if not group:
            user_state.pop(uid, None)
            await update.message.reply_text("⚠️ আগে Group যোগ করে select করুন।", reply_markup=channel_group_kb())
            return
        if step == "await_cg_count" and value.isdigit():
            group["count"] = max(1, min(20, int(value)))
        elif step == "await_cg_delay" and value.isdigit():
            group["delay_seconds"] = max(0, int(value))
        elif step == "await_cg_schedule":
            parts = value.split()
            if len(parts) == 3 and parts[0].lower() in ("on", "off"):
                group["schedule"] = {"enabled": parts[0].lower() == "on", "start": parts[1], "end": parts[2]}
            else:
                await update.message.reply_text("Format: on 09:00 23:00 অথবা off 00:00 23:59")
                return
        else:
            await update.message.reply_text("⚠️ সঠিক সংখ্যা লিখুন।")
            return
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Group forwarding setting সেভ হয়েছে।", reply_markup=channel_group_kb())
        return

    # ── ধাপ: source/destination যোগ বা বাদ ──
    if uid in user_state and user_state[uid]["step"] == "await_channel":
        state_type = user_state[uid]["type"]
        if state_type.startswith("forward_group_"):
            action = state_type.rsplit("_", 1)[1]
            value = t.strip()
            try:
                value = int(value)
            except ValueError:
                value = value if value.startswith("@") else f"@{value}"
            groups = settings["forward_groups"]
            if action == "add":
                if value not in groups:
                    groups.append(value)
                message = "✅ Forward Group যোগ হয়েছে।"
            else:
                if value in groups:
                    groups.remove(value)
                    message = "✅ Forward Group বাদ দেওয়া হয়েছে।"
                else:
                    message = "⚠️ Group তালিকায় পাওয়া যায়নি।"
            save_settings(settings)
            user_state.pop(uid, None)
            await update.message.reply_text(message, reply_markup=settings_kb())
            return
        if state_type == "source_remove" or state_type == "destination_remove":
            try:
                chat = await ctx.bot.get_chat(t.strip() if t.strip().startswith("@") else f"@{t.strip()}")
                value = chat.username and f"@{chat.username}" or chat.id
            except Exception:
                value = t.strip()
            target = settings["sources"] if state_type == "source_remove" else settings["destinations"]
            candidates = [value, str(value), value.lstrip("@") if isinstance(value, str) else value]
            removed = False
            for item in list(target):
                if item in candidates or str(item).lstrip("@") in candidates:
                    target.remove(item)
                    removed = True
            user_state.pop(uid, None)
            save_settings(settings)
            label = "Source" if state_type == "source_remove" else "Destination"
            await update.message.reply_text(
                f"{'✅ বাদ দেওয়া হয়েছে' if removed else '⚠️ তালিকায় পাওয়া যায়নি'}: {label}",
                reply_markup=channel_kb())
            return

        username = t.replace("https://t.me/", "").replace("@", "").split("/")[0]
        try:
            chat = await ctx.bot.get_chat(f"@{username}")
            if state_type == "source_add":
                source = f"@{chat.username}" if chat.username else str(chat.id)
                if source not in settings["sources"]:
                    settings["sources"].append(source)
                label = "Source"
            else:
                if chat.id not in settings["destinations"]:
                    settings["destinations"].append(chat.id)
                label = "Destination"
            save_settings(settings)
            user_state.pop(uid)
            await update.message.reply_text(
                f"✅ {label} যোগ হয়েছে!\n\n"
                f"📛 নাম: {chat.title}\n🆔 ID: {chat.id}", reply_markup=channel_kb())
        except Exception:
            await update.message.reply_text(
                "❌ চ্যানেল পাওয়া যায়নি。\n\n"
                "💡 ধাপ ১: বটকে ওই চ্যানেলে অ্যাডমিন বানান\n"
                "💡 ধাপ ২: আবার URL পাঠান", reply_markup=channel_kb())
        return

    # ── ধাপ: ডিলে ──
    if uid in user_state and user_state[uid]["step"] == "await_delay":
        if not t.strip().isdigit():
            await update.message.reply_text("⚠️ শুধু মিনিট সংখ্যা লিখুন (যেমন: 5)"); return
        settings["delay_minutes"] = int(t.strip())
        save_settings(settings)
        user_state.pop(uid)
        await update.message.reply_text(
            f"✅ ডিলে সেট হয়েছে: **{settings['delay_minutes']} মিনিট**。", reply_markup=settings_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_forward_value":
        state = user_state[uid]
        value = t.strip()
        if state["field"] in ("repeat_count", "repeat_interval_minutes"):
            if not value.isdigit():
                await update.message.reply_text("⚠️ শুধু 0 বা ধনাত্মক সংখ্যা লিখুন।")
                return
            settings["forwarding"][state["field"]] = int(value)
        else:
            settings["forwarding"]["enabled"] = value.lower() in ("চালু", "on", "yes", "1")
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Forward সেটিংস আপডেট হয়েছে।", reply_markup=settings_kb())
        return

    if uid in user_state and user_state[uid]["step"] == "await_replacement":
        field = user_state[uid]["field"]
        settings["replacements"][field] = "" if t.strip() in ("না", "-", "খালি") else t.strip()
        save_settings(settings)
        user_state.pop(uid, None)
        await update.message.reply_text("✅ Personal তথ্য সেভ হয়েছে।", reply_markup=settings_kb())
        return

    # ── ধাপ: টেমপ্লেট ──
    if uid in user_state and user_state[uid]["step"] in ("await_tmpl_header", "await_tmpl_footer"):
        step = user_state[uid]["step"]
        if step == "await_tmpl_header":
            settings["template"]["header"] = t
            save_settings(settings)
            user_state[uid]["step"] = "await_tmpl_footer"
            await update.message.reply_text("✅ হেডার সেভ হয়েছে!\n\n📝 এখন **ফুটার** লিখুন (না চাইলে 'না' লিখুন):")
        else:
            if t.lower() != "না":
                settings["template"]["footer"] = t
            save_settings(settings)
            user_state.pop(uid)
            await update.message.reply_text(
                f"✅ টেমপ্লেট সেভ হয়েছে!\n\n"
                f"📌 হেডার: {settings['template']['header'] or '(খালি)'}\n"
                f"📌 ফুটার: {settings['template']['footer'] or '(খালি)'}",
                reply_markup=settings_kb())
        return

    # ── মূল মেনু ──
    if t == "📡 চ্যানেল সেটিংস":
        await update.message.reply_text("📡 চ্যানেল সেটিংস\n\nকোন ধরনের চ্যানেল?", reply_markup=channel_kb())

    elif t == "🏠 Destination":
        lst = "\n".join(f"🟢 {channel}" for channel in settings["destinations"]) or "খালি"
        await update.message.reply_text(
            f"🏠 Destination channel:\n{lst}", reply_markup=channel_kb())

    elif t == "📡 Source":
        lst = "\n".join(f"📡 {s}" for s in settings["sources"]) or "খালি"
        await update.message.reply_text(
            f"📡 Source channel (যেগুলো userbot দেখবে):\n{lst}", reply_markup=channel_kb())

    elif t in ("➕ Source যোগ", "➖ Source বাদ", "➕ Destination যোগ", "➖ Destination বাদ"):
        is_source = "Source" in t
        is_add = "যোগ" in t
        kind = "source" if is_source else "destination"
        action = "add" if is_add else "remove"
        user_state[uid] = {"step": "await_channel", "type": f"{kind}_{action}"}
        if kind == "destination" and is_add:
            await update.message.reply_text(
                "Destination channel-এর একটি post forward করুন অথবা @username/লিংক পাঠান।",
                reply_markup=channel_kb())
        else:
            label = "Source" if is_source else "Destination"
            action_text = "যোগ" if is_add else "বাদ"
            await update.message.reply_text(
                f"{label} {action_text} করতে @username বা t.me লিংক পাঠান।",
                reply_markup=channel_kb())

    elif t == "🛡️ প্রাইভেসি ফিল্টার":
        await update.message.reply_text(
            "🛡️ প্রাইভেসি ফিল্টার\n\nযে তথ্য পোস্ট থেকে মুছে যাবে — চাপ দিয়ে ON/OFF করুন:",
            reply_markup=privacy_kb())

    elif t in ("👤 @username", "📞 ফোন", "✉️ ইমেইল", "🔗 t.me লিংক"):
        key = {"👤 @username": "username", "📞 ফোন": "phone",
               "✉️ ইমেইল": "email", "🔗 t.me লিংক": "tme_link"}[t]
        settings["privacy"][key]["on"] = not settings["privacy"][key]["on"]
        save_settings(settings)
        st = "🟢 চালু" if settings["privacy"][key]["on"] else "🔴 বন্ধ"
        await update.message.reply_text(
            f"✅ ফিল্টার আপডেট!\n\n🔍 {t}: {st}\n\n"
            f"⚠️ এই নিয়ম এখন থেকে সোর্স পোস্টে প্রযোজ্য।", reply_markup=privacy_kb())

    elif t == "📢 Channel → Group":
        forwarding = settings["channel_group_forwarding"]
        await update.message.reply_text(
            f"📢 Channel → Group Auto Forward\n\n"
            f"অবস্থা: {'🟢 চালু' if forwarding['enabled'] else '🔴 বন্ধ'}\n"
            f"Group: {len(forwarding['groups'])}\n"
            f"Selected: {forwarding.get('selected_group') or '(নেই)'}",
            reply_markup=channel_group_kb())

    elif t == "➕ Group যোগ করুন":
        user_state[uid] = {"step": "await_cg_add"}
        await update.message.reply_text("Group-এর numeric ID বা @username পাঠান।")

    elif t == "👥 Group List":
        rows = []
        for key, group in settings["channel_group_forwarding"]["groups"].items():
            state = "⏸️ paused" if group.get("paused") else ("🟢 active" if group.get("enabled") else "🔴 disabled")
            rows.append(f"{key} — {state} — count {group.get('count', 1)} — delay {group.get('delay_seconds', 0)}s")
        await update.message.reply_text("\n".join(rows) or "Group list খালি।", reply_markup=channel_group_kb())

    elif t == "🎯 Select Group":
        user_state[uid] = {"step": "await_cg_select"}
        await update.message.reply_text("যে Group configure করবেন তার ID/@username পাঠান।")

    elif t == "⚙️ Forward Settings":
        group = selected_forward_group()
        if not group:
            await update.message.reply_text("⚠️ আগে Group যোগ করুন।", reply_markup=channel_group_kb())
        else:
            await update.message.reply_text(
                f"Selected: {settings['channel_group_forwarding']['selected_group']}\n"
                f"Status: {'paused' if group.get('paused') else ('active' if group.get('enabled') else 'disabled')}\n"
                f"Count: {group.get('count', 1)}\nDelay: {group.get('delay_seconds', 0)} seconds\n"
                f"AI: {'ON' if group.get('ai_enabled') else 'OFF'}\n"
                f"Schedule: {group.get('schedule', {})}",
                reply_markup=channel_group_kb())

    elif t in ("🔢 Forward Count", "⏱️ Delay", "📅 Schedule"):
        steps = {"🔢 Forward Count": "await_cg_count", "⏱️ Delay": "await_cg_delay", "📅 Schedule": "await_cg_schedule"}
        if not selected_forward_group():
            await update.message.reply_text("⚠️ আগে Group যোগ করুন।", reply_markup=channel_group_kb())
        else:
            user_state[uid] = {"step": steps[t]}
            prompt = "Count দিন (1-20)।" if t == "🔢 Forward Count" else ("Delay seconds দিন।" if t == "⏱️ Delay" else "Format: on 09:00 23:00 অথবা off 00:00 23:59")
            await update.message.reply_text(prompt)

    elif t == "🤖 AI Editing":
        group = selected_forward_group()
        if group:
            group["ai_enabled"] = not group.get("ai_enabled", False)
            save_settings(settings)
            await update.message.reply_text(f"✅ Selected group AI: {'ON' if group['ai_enabled'] else 'OFF'}", reply_markup=channel_group_kb())
        else:
            await update.message.reply_text("⚠️ আগে Group যোগ করুন।", reply_markup=channel_group_kb())

    elif t in ("🟢 C→G চালু", "🔴 C→G বন্ধ", "⏸️ Pause", "▶️ Resume"):
        group = selected_forward_group()
        if group:
            if t == "🟢 C→G চালু":
                settings["channel_group_forwarding"]["enabled"] = True
                group["enabled"] = True
            elif t == "🔴 C→G বন্ধ":
                settings["channel_group_forwarding"]["enabled"] = False
            elif t == "⏸️ Pause":
                group["paused"] = True
            else:
                group["paused"] = False
            save_settings(settings)
            await update.message.reply_text("✅ Forward status আপডেট হয়েছে।", reply_markup=channel_group_kb())
        else:
            await update.message.reply_text("⚠️ আগে Group যোগ করুন।", reply_markup=channel_group_kb())

    elif t == "📊 Status":
        rows = read_forward_history()
        success = sum(1 for row in rows if row.get("status") == "success")
        failed = sum(1 for row in rows if row.get("status") == "error")
        await update.message.reply_text(
            f"📊 Forward Status\n\nTotal: {len(rows)}\nSuccessful: {success}\nFailed: {failed}\n"
            f"Pending: 0\nLast: {rows[-1].get('time') if rows else 'নেই'}",
            reply_markup=channel_group_kb())

    elif t == "📝 History":
        rows = read_forward_history()[-15:]
        text = "\n".join(f"{r.get('time')} | {r.get('group')} | post {r.get('source_message_id')} | {r.get('status')}" for r in rows)
        await update.message.reply_text(text or "History খালি।", reply_markup=channel_group_kb())

    elif t == "🤖 AI সেটিংস":
        ai = settings["ai"]
        await update.message.reply_text(
            f"🤖 GROQ AI Post Editing\n\n"
            f"অবস্থা: {'🟢 চালু' if ai['enabled'] else '🔴 বন্ধ'}\n"
            f"Style: {ai['style']}\nLength: {ai['length']}\n"
            f"Emoji: {'চালু' if ai['emoji'] else 'বন্ধ'}\n"
            f"Custom prompt: {ai['custom_prompt'] or '(খালি)'}",
            reply_markup=ai_kb())

    elif t in ("🟢 AI Editing চালু", "🔴 AI Editing বন্ধ"):
        settings["ai"]["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ AI Post Editing আপডেট হয়েছে।", reply_markup=ai_kb())

    elif t == "✨ AI Emoji ON/OFF":
        settings["ai"]["emoji"] = not settings["ai"].get("emoji", True)
        save_settings(settings)
        await update.message.reply_text("✅ AI Emoji সেটিংস আপডেট হয়েছে।", reply_markup=ai_kb())

    elif t in ("🎨 AI Style", "🧠 Custom Prompt", "📏 Post Length"):
        steps = {"🎨 AI Style": "await_ai_style", "🧠 Custom Prompt": "await_ai_prompt", "📏 Post Length": "await_ai_length"}
        user_state[uid] = {"step": steps[t]}
        await update.message.reply_text("নতুন মান লিখুন। খালি করতে 'না' লিখুন।")

    elif t == "👥 Group AI সেটিংস":
        user_state[uid] = {"step": "await_group_config"}
        await update.message.reply_text(
            "এক লাইনে লিখুন: <chat_id> <on/off> <mode>\n"
            "Mode: always / question / mention / reply / ask\n"
            "উদাহরণ: -100123456789 on mention", reply_markup=ai_kb())

    elif t == "💬 Live Chat সেটিংস":
        live = settings.setdefault("live_chat", {})
        await update.message.reply_text(
            f"💬 Live AI Chat (private DM)\n\n"
            f"অবস্থা: {'🟢 চালু' if live.get('enabled') else '🔴 বন্ধ'}\n"
            f"Style: {live.get('style') or '(default)'}\n"
            f"Context (আগের কথা মনে রাখা): {'🟢 ON' if live.get('context_enabled', True) else '🔴 OFF'}\n"
            f"Custom prompt: {live.get('custom_prompt') or '(খালি)'}\n\n"
            f"চালু থাকলে সাধারণ user private message পাঠালে AI সরাসরি উত্তর দেবে।",
            reply_markup=live_chat_kb())

    elif t in ("🟢 Live Chat চালু", "🔴 Live Chat বন্ধ"):
        settings.setdefault("live_chat", {})["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Live Chat অবস্থা আপডেট হয়েছে।", reply_markup=live_chat_kb())

    elif t in ("🎨 Chat Style", "🧠 Chat Prompt"):
        field = "style" if t == "🎨 Chat Style" else "custom_prompt"
        user_state[uid] = {"step": "await_live_chat_field", "field": field}
        await update.message.reply_text("নতুন মান লিখুন। খালি করতে 'না' লিখুন।")

    elif t == "🔁 Context ON/OFF":
        lc = settings.setdefault("live_chat", {})
        lc["context_enabled"] = not lc.get("context_enabled", True)
        save_settings(settings)
        await update.message.reply_text("✅ Context সেটিংস আপডেট হয়েছে।", reply_markup=live_chat_kb())

    elif t == "📚 Private Knowledge":
        knowledge = settings.get("ai", {}).get("private_knowledge", "")
        preview = (knowledge[:300] + "...") if len(knowledge) > 300 else (knowledge or "(খালি)")
        user_state[uid] = {"step": "await_private_knowledge"}
        await update.message.reply_text(
            f"📚 বর্তমান Private Knowledge:\n{preview}\n\n"
            "AI যেন জানে এমন তথ্য (FAQ, product info, নিয়মকানুন ইত্যাদি) লিখে পাঠান, "
            "অথবা .txt ফাইল পাঠান। খালি করতে 'না' লিখুন।\n"
            "⚠️ Password/Token/API Key এখানে যোগ করবেন না।")

    elif t == "👋 Welcome সেটিংস":
        welcome = settings.setdefault("welcome", {})
        await update.message.reply_text(
            f"👋 Welcome System (Group-এ নতুন member Join)\n\n"
            f"অবস্থা: {'🟢 চালু' if welcome.get('enabled') else '🔴 বন্ধ'}\n"
            f"বার্তা: {welcome.get('message') or '(default)'}\n\n"
            f"{{name}} লিখলে নতুন member-এর নাম বসবে।",
            reply_markup=welcome_kb())

    elif t in ("🟢 Welcome চালু", "🔴 Welcome বন্ধ"):
        settings.setdefault("welcome", {})["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Welcome অবস্থা আপডেট হয়েছে।", reply_markup=welcome_kb())

    elif t == "✏️ Welcome বার্তা":
        user_state[uid] = {"step": "await_welcome_message"}
        await update.message.reply_text("Welcome বার্তা লিখুন। {name} দিয়ে user-এর নাম বসবে। খালি করতে 'না' লিখুন।")

    elif t == "🔤 Word Filter":
        filters_list = settings.get("word_filters", [])
        rows = [f"{i+1}. {f['find']} → {f['replace'] or '(খালি)'}" for i, f in enumerate(filters_list)]
        await update.message.reply_text(
            "🔤 Word Filter/Replace (Auto Post editing-এ প্রযোজ্য)\n\n" + ("\n".join(rows) if rows else "তালিকা খালি।"),
            reply_markup=word_filter_kb())

    elif t == "➕ Filter যোগ":
        user_state[uid] = {"step": "await_word_filter_add"}
        await update.message.reply_text("Format: পুরনো_শব্দ => নতুন_শব্দ\nউদাহরণ: cheap => affordable")

    elif t == "➖ Filter বাদ":
        filters_list = settings.get("word_filters", [])
        if not filters_list:
            await update.message.reply_text("তালিকা খালি।", reply_markup=word_filter_kb())
        else:
            rows = [f"{i+1}. {f['find']} → {f['replace'] or '(খালি)'}" for i, f in enumerate(filters_list)]
            user_state[uid] = {"step": "await_word_filter_remove"}
            await update.message.reply_text("যে নম্বরটা বাদ দিতে চান তা লিখুন:\n\n" + "\n".join(rows))

    elif t == "📋 Filter তালিকা":
        filters_list = settings.get("word_filters", [])
        rows = [f"{i+1}. {f['find']} → {f['replace'] or '(খালি)'}" for i, f in enumerate(filters_list)]
        await update.message.reply_text("\n".join(rows) or "তালিকা খালি।", reply_markup=word_filter_kb())

    # ── Feature 10 — Custom AI Post Format Style ──
    elif t == "🎨 Post Format Style":
        fs = settings.setdefault("format_style", {})
        await update.message.reply_text(
            "🎨 Post Format Style\n\n"
            f"অবস্থা: {'🟢 চালু' if fs.get('enabled') else '🔴 বন্ধ'}\n"
            f"Border: {fs.get('border') or '(খালি)'}\n"
            f"Header: {fs.get('header') or '(খালি)'}\n"
            f"Footer: {fs.get('footer') or '(খালি)'}\n"
            f"Contact line: {fs.get('contact_line') or '(খালি)'}\n"
            f"Bullet: {'চালু' if fs.get('use_bullets', True) else 'বন্ধ'}\n"
            f"Emoji heading: {'চালু' if fs.get('use_emoji_heading', True) else 'বন্ধ'}\n\n"
            "চালু থাকলে AI post edit করার সময় এই format অনুযায়ী post সাজাবে "
            "(header/border/bullet/footer/contact)। বন্ধ থাকলে আগের মতো plain edit হবে।",
            reply_markup=format_style_kb())

    elif t in ("🟢 Format Style চালু", "🔴 Format Style বন্ধ"):
        settings.setdefault("format_style", {})["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Format Style অবস্থা আপডেট হয়েছে।", reply_markup=format_style_kb())

    elif t in ("🎯 Border", "📰 Header", "🔻 Footer", "📩 Contact Line"):
        field = {"🎯 Border": "border", "📰 Header": "header", "🔻 Footer": "footer", "📩 Contact Line": "contact_line"}[t]
        user_state[uid] = {"step": "await_format_field", "field": field}
        await update.message.reply_text("নতুন লেখা পাঠান। খালি করতে 'না' লিখুন।")

    elif t == "🔘 Bullet ON/OFF":
        fs = settings.setdefault("format_style", {})
        fs["use_bullets"] = not fs.get("use_bullets", True)
        save_settings(settings)
        await update.message.reply_text(f"✅ Bullet: {'চালু' if fs['use_bullets'] else 'বন্ধ'}", reply_markup=format_style_kb())

    elif t == "🔤 Emoji Heading ON/OFF":
        fs = settings.setdefault("format_style", {})
        fs["use_emoji_heading"] = not fs.get("use_emoji_heading", True)
        save_settings(settings)
        await update.message.reply_text(f"✅ Emoji heading: {'চালু' if fs['use_emoji_heading'] else 'বন্ধ'}", reply_markup=format_style_kb())

    # ── Feature 9 — Multi Admin System (Super Owner only) ──
    elif t == "👑 Admin Management":
        if not is_super_owner(uid):
            await update.message.reply_text("❌ শুধুমাত্র Super Owner এই অংশ ব্যবহার করতে পারবেন।", reply_markup=main_kb(uid))
        else:
            admins = settings.get("admins", {})
            await update.message.reply_text(
                f"👑 Admin Management\n\nমোট Extra Admin: {len(admins)}\n"
                "Super Owner (env-এ সেট, সরানো যায় না) সবসময় Full Access পায়।",
                reply_markup=admin_kb())

    elif t == "➕ Admin যোগ":
        if not is_super_owner(uid):
            await update.message.reply_text("❌ অনুমতি নেই।", reply_markup=main_kb(uid))
        else:
            user_state[uid] = {"step": "await_admin_add"}
            await update.message.reply_text("নতুন Admin-এর numeric Telegram user ID পাঠান।")

    elif t == "🗑️ Admin সরান":
        if not is_super_owner(uid):
            await update.message.reply_text("❌ অনুমতি নেই।", reply_markup=main_kb(uid))
        else:
            admins = settings.get("admins", {})
            if not admins:
                await update.message.reply_text("তালিকা খালি।", reply_markup=admin_kb())
            else:
                user_state[uid] = {"step": "await_admin_remove"}
                rows = [f"{aid} — {info.get('name') or info.get('role')}" for aid, info in admins.items()]
                await update.message.reply_text("যার ID সরাতে চান, সেটা পাঠান:\n\n" + "\n".join(rows))

    elif t == "👥 Admin তালিকা":
        admins = settings.get("admins", {})
        lines = [f"👑 Super Owner (env): {', '.join(str(a) for a in ADMIN_IDS) or '(নেই)'}"]
        for aid, info in admins.items():
            perms = ", ".join(p for p, on in info.get("permissions", {}).items() if on) or "(কোনো permission নেই)"
            lines.append(f"• {aid} — {info.get('role', 'admin')} — {perms}")
        await update.message.reply_text("\n".join(lines), reply_markup=admin_kb())

    elif t == "🔐 Permission সেট":
        if not is_super_owner(uid):
            await update.message.reply_text("❌ অনুমতি নেই।", reply_markup=main_kb(uid))
        else:
            admins = settings.get("admins", {})
            if not admins:
                await update.message.reply_text("আগে Admin যোগ করুন।", reply_markup=admin_kb())
            else:
                user_state[uid] = {"step": "await_admin_perm_target"}
                rows = [f"{aid} — {info.get('name') or info.get('role')}" for aid, info in admins.items()]
                await update.message.reply_text("যার permission পরিবর্তন করবেন, সেই ID পাঠান:\n\n" + "\n".join(rows))

    # ── Feature 4 — Multi-Channel Watch + Smart Scheduling ──
    elif t == "🧭 Multi-Channel Watch":
        mw = settings.setdefault("multi_watch", {})
        await update.message.reply_text(
            "🧭 Multi-Channel Watch + Smart Scheduling\n\n"
            f"অবস্থা: {'🟢 চালু' if mw.get('enabled') else '🔴 বন্ধ'}\n"
            f"Watch Channel: {len(mw.get('channels', []))}\n"
            f"Schedule Times: {', '.join(mw.get('schedule_times', [])) or '(নেই)'}\n"
            f"Max Posts/Slot: {mw.get('max_posts_per_slot', 1)}\n"
            f"Duplicate Filter: {'চালু' if mw.get('similarity_skip', True) else 'বন্ধ'}\n\n"
            "চালু থাকলে এখানে যোগ করা Channel-গুলো থেকে Bot নিজে সেরা Post বেছে "
            "নির্ধারিত সময়ে Destination-এ পাবলিশ করবে (Duplicate বাদ দিয়ে)। "
            "বন্ধ থাকলে আগের মতো সাধারণ Auto Forward/Auto Post System-ই কাজ করবে।",
            reply_markup=multi_watch_kb())

    elif t in ("🟢 Multi Watch চালু", "🔴 Multi Watch বন্ধ"):
        settings.setdefault("multi_watch", {})["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Multi-Channel Watch অবস্থা আপডেট হয়েছে।", reply_markup=multi_watch_kb())

    elif t in ("➕ Watch Channel যোগ", "➖ Watch Channel বাদ"):
        user_state[uid] = {"step": "await_mw_channel", "action": "add" if "যোগ" in t else "remove"}
        await update.message.reply_text("Channel-এর @username অথবা numeric ID পাঠান (যেমন @example বা -1001234567890)।")

    elif t == "📋 Watch Channel তালিকা":
        channels = settings.get("multi_watch", {}).get("channels", [])
        await update.message.reply_text(
            "\n".join(f"{i+1}. {c}" for i, c in enumerate(channels)) or "তালিকা খালি।",
            reply_markup=multi_watch_kb())

    elif t == "📥 Buffer Status":
        try:
            buffered = multi_watch.read_buffer()
        except Exception:
            buffered = []
        await update.message.reply_text(f"📥 এই মুহূর্তে Buffer-এ জমা আছে: {len(buffered)}টি Post", reply_markup=multi_watch_kb())

    elif t == "⏰ Schedule Times":
        user_state[uid] = {"step": "await_mw_schedule"}
        await update.message.reply_text(
            "কমা দিয়ে সময় পাঠান, 24-ঘণ্টা ফরম্যাটে (যেমন: 09:00,14:00,19:00)")

    elif t == "🔢 Max Posts/Slot":
        user_state[uid] = {"step": "await_mw_maxposts"}
        await update.message.reply_text("প্রতি Slot-এ সর্বোচ্চ কতটি Post পাবলিশ হবে, সংখ্যা পাঠান (যেমন: 1)")

    elif t == "🔁 Similarity Filter ON/OFF":
        mw = settings.setdefault("multi_watch", {})
        mw["similarity_skip"] = not mw.get("similarity_skip", True)
        save_settings(settings)
        await update.message.reply_text(
            f"✅ Duplicate Filter: {'চালু' if mw['similarity_skip'] else 'বন্ধ'}", reply_markup=multi_watch_kb())

    elif t in ("🎭 AI পরিচয় সেটিংস", "⬅️ AI সেটিংসে ফিরুন"):
        if t == "⬅️ AI সেটিংসে ফিরুন":
            await update.message.reply_text("🤖 AI সেটিংসে ফিরে গেলাম।", reply_markup=ai_kb())
        else:
            ai = settings["ai"]
            master = ai.get("master_instruction") or ""
            master_preview = (master[:200] + "...") if len(master) > 200 else (master or "(সেট করা নেই)")
            await update.message.reply_text(
                f"🎭 AI পরিচয় সেটিংস\n\n"
                f"AI-র নাম: {ai.get('identity_name') or '(সেট করা নেই — default AI পরিচয় দেবে)'}\n"
                f"Owner-এর নাম: {ai.get('owner_name') or '(সেট করা নেই — মালিকের প্রশ্ন এড়িয়ে যাবে)'}\n"
                f"Filter/নিষেধ: {ai.get('identity_filter') or '(খালি)'}\n"
                f"Master নির্দেশনা: {master_preview}\n\n"
                f"এই সেটিংস অনুযায়ী AI কখনো ChatGPT/OpenAI/Groq-এর নাম বলবে না, "
                f"বরং আপনার দেওয়া পরিচয়/মালিক-এর নাম বলবে এবং Master নির্দেশনা সবসময় মেনে চলবে।",
                reply_markup=ai_identity_kb())

    elif t in ("🤖 AI-র নাম", "👑 Owner-এর নাম", "🚫 অতিরিক্ত Filter/নিষেধ"):
        steps = {
            "🤖 AI-র নাম": "await_ai_identity_name",
            "👑 Owner-এর নাম": "await_ai_owner_name",
            "🚫 অতিরিক্ত Filter/নিষেধ": "await_ai_identity_filter",
        }
        prompts = {
            "🤖 AI-র নাম": "AI নিজেকে যে নামে পরিচয় দেবে সেটা লিখুন (উদাহরণ: Nishat X AI)। খালি করতে 'না' লিখুন।",
            "👑 Owner-এর নাম": "'তোমার মালিক কে' জিজ্ঞেস করলে AI যে নাম বলবে সেটা লিখুন। খালি করতে 'না' লিখুন।",
            "🚫 অতিরিক্ত Filter/নিষেধ": "AI যা যা বলতে পারবে না তা লিখুন (যেমন: ফোন নম্বর/ঠিকানা কখনো বলবে না)। খালি করতে 'না' লিখুন।",
        }
        user_state[uid] = {"step": steps[t]}
        await update.message.reply_text(prompts[t])

    elif t == "📄 Master নির্দেশনা (Text/File)":
        user_state[uid] = {"step": "await_master_instruction"}
        await update.message.reply_text(
            "AI কীভাবে আচরণ করবে, কী কী উত্তর দেবে/দেবে না — পুরো নিয়ম এখানে লিখে পাঠান, "
            "অথবা সরাসরি একটা .txt ফাইল পাঠান (ফাইলের ভেতরের লেখাটাই নির্দেশনা হিসেবে সেভ হবে)।\n"
            "খালি করতে 'না' লিখুন।")

    elif t == "📩 User Messaging":
        campaign = settings["user_campaign"]
        await update.message.reply_text(
            f"📩 Opt-in User Messaging\n\n"
            f"তালিকায় user: {len(campaign['user_ids'])}\n"
            f"Message: {'সেট করা আছে' if campaign['message'] else 'খালি'}\n"
            f"Delay: {campaign['delay_minutes']} মিনিট",
            reply_markup=user_message_kb())

    elif t == "👥 User List":
        rows = []
        for key in settings["user_campaign"]["user_ids"]:
            item = settings["users"].get(str(key), {})
            live_status = item.get("status", "unknown")
            if item.get("job_id"):
                live_status = personal_messenger.get_status(item["job_id"]).get("status", live_status)
            rows.append(f"{key} — {'🟢 opt-in' if item.get('opted_in') else '🔴 opt-in নেই'} — {live_status}")
        await update.message.reply_text("\n".join(rows) or "User list খালি।", reply_markup=user_message_kb())

    elif t == "➕ User যোগ":
        user_state[uid] = {"step": "await_users"}
        await update.message.reply_text("User ID বা @username দিন। একাধিক হলে comma বা নতুন line ব্যবহার করুন।")

    elif t == "🗑️ User সরান":
        campaign_ids = settings.get("user_campaign", {}).get("user_ids", [])
        if not campaign_ids:
            await update.message.reply_text("User list খালি।", reply_markup=user_message_kb())
        else:
            user_state[uid] = {"step": "await_user_remove"}
            await update.message.reply_text(
                "যে User-এর ID/username সরাতে চান তা পাঠান:\n\n" + "\n".join(str(u) for u in campaign_ids))

    elif t == "📝 Common Message":
        user_state[uid] = {"step": "await_campaign_message"}
        await update.message.reply_text("Common message লিখুন।")

    elif t == "⏰ Schedule":
        user_state[uid] = {"step": "await_campaign_delay"}
        await update.message.reply_text("প্রতিটি message-এর মাঝে কত মিনিট বিরতি থাকবে? 0 দিলে পরপর যাবে।")

    elif t == "📩 এখন পাঠান":
        await send_campaign(ctx.bot)
        await update.message.reply_text(
            "📤 Campaign queue করা হয়েছে — Personal Account থেকে পাঠানো হচ্ছে। "
            "কিছুক্ষণ পর 👥 User List-এ আসল ডেলিভারি status দেখুন।",
            reply_markup=user_message_kb())

    elif t in ("🟢 Campaign চালু", "🔴 Campaign বন্ধ"):
        settings["user_campaign"]["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Campaign অবস্থা আপডেট হয়েছে।", reply_markup=user_message_kb())

    elif t == "📝 অটো পোস্ট":
        st = "🟢 চালু" if settings["autopost"] else "🔴 বন্ধ"
        await update.message.reply_text(
            f"📝 অটো পোস্ট\n\n📊 অবস্থা: {st}\n"
            f"📡 সোর্স: {len(settings['sources'])}টা\n"
            f"🏠 Destination: {len(settings['destinations'])}টা\n\n"
            f"চালু/বন্ধ করতে নিচের বাটন ব্যবহার করুন:", reply_markup=autopost_kb())

    elif t == "🟢 চালু করুন":
        settings["autopost"] = True
        set_autopost(True)
        await update.message.reply_text("✅ অটো পোস্ট চালু হয়েছে!", reply_markup=autopost_kb())

    elif t == "🔴 বন্ধ করুন":
        settings["autopost"] = False
        set_autopost(False)
        await update.message.reply_text("⏸️ অটো পোস্ট বন্ধ করা হয়েছে!", reply_markup=autopost_kb())

    elif t == "📊 পরিসংখ্যান":
        pub, skip = read_stats()
        await update.message.reply_text(
            f"📊 পরিসংখ্যান\n\n"
            f"✅ পাবলিশ: {pub}\n"
            f"⏭️ স্কিপ: {skip}\n"
            f"📡 সোর্স: {len(settings['sources'])}\n"
            f"🏠 Destination: {len(settings['destinations'])}", reply_markup=main_kb(uid))

    elif t == "⚙️ সেটিংস":
        await update.message.reply_text(
            f"⚙️ সেটিংস\n\n"
            f"⏱️ ডিলে: {settings['delay_minutes']} মিনিট\n"
            f"📌 হেডার: {settings['template']['header'] or '(খালি)'}\n"
            f"📌 ফুটার: {settings['template']['footer'] or '(খালি)'}",
            reply_markup=settings_kb())

    elif t == "⏱️ ডিলে সেট":
        user_state[uid] = {"step": "await_delay"}
        await update.message.reply_text(
            "⏱️ ডিলে সেট\n\n"
            "**ধাপ ১:** কত মিনিট পরে পোস্ট হবে, সংখ্যায় লিখুন (যেমন: 5)\n"
            "**ধাপ ২:** ✅ নিশ্চিত মেসেজ পাবেন")

    elif t == "📝 টেমপ্লেট":
        user_state[uid] = {"step": "await_tmpl_header"}
        await update.message.reply_text(
            "📝 টেমপ্লেট\n\n"
            "**ধাপ ১:** হেডার লিখুন (যেমন: 📢 নতুন আপডেট)\n"
            "**ধাপ ২:** ফুটার লিখুন\n"
            "**ধাপ ৩:** ✅ সেভ হয়ে যাবে")

    elif t == "🔁 Forward সেটিংস":
        f = settings["forwarding"]
        await update.message.reply_text(
            "🔁 Channel-এর edited post Group-এ forward\n\n"
            f"অবস্থা: {'🟢 চালু' if f['enabled'] else '🔴 বন্ধ'}\n"
            f"বার: {f['repeat_count']}\n"
            f"Interval: {f['repeat_interval_minutes']} মিনিট\n"
            f"Group: {len(settings['forward_groups'])}টি\n\n"
            "নিচের অপশন বেছে নিন।",
            reply_markup=ReplyKeyboardMarkup([
                ["🟢 Forward চালু", "🔴 Forward বন্ধ"],
                ["🔢 Repeat সংখ্যা", "⏱️ Repeat interval"],
                ["➕ Forward Group", "➖ Forward Group"],
                ["⬅️ ফিরে যান"],
            ], resize_keyboard=True))

    elif t in ("🟢 Forward চালু", "🔴 Forward বন্ধ"):
        settings["forwarding"]["enabled"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Forward অবস্থা আপডেট হয়েছে।", reply_markup=settings_kb())

    elif t in ("🔢 Repeat সংখ্যা", "⏱️ Repeat interval"):
        field = "repeat_count" if t.startswith("🔢") else "repeat_interval_minutes"
        user_state[uid] = {"step": "await_forward_value", "field": field}
        await update.message.reply_text(
            "সংখ্যা লিখুন। Repeat count কমপক্ষে 1 এবং interval মিনিটে দিতে হবে।")

    elif t in ("➕ Forward Group", "➖ Forward Group"):
        action = "add" if t.startswith("➕") else "remove"
        user_state[uid] = {"step": "await_channel", "type": f"forward_group_{action}"}
        await update.message.reply_text(
            "Group-এর @username বা numeric chat ID পাঠান।")

    elif t == "🖼️ মিডিয়া ফিল্টার":
        mf = settings.setdefault("media_filter", {"image": True, "file": True})
        await update.message.reply_text(
            "🖼️ মিডিয়া ফিল্টার (Feature 7)\n\n"
            f"Image: {'🟢 চালু' if mf.get('image', True) else '🔴 বন্ধ'}\n"
            f"File/Document: {'🟢 চালু' if mf.get('file', True) else '🔴 বন্ধ'}\n\n"
            "বন্ধ করলে শুধু ওই ধরনের মিডিয়া বাদ যাবে, লেখা (edited text) ঠিকই পোস্ট হবে।",
            reply_markup=media_filter_kb())

    elif t in ("🟢 Image চালু", "🔴 Image বন্ধ"):
        settings.setdefault("media_filter", {})["image"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ Image ফিল্টার আপডেট হয়েছে।", reply_markup=media_filter_kb())

    elif t in ("🟢 File চালু", "🔴 File বন্ধ"):
        settings.setdefault("media_filter", {})["file"] = t.startswith("🟢")
        save_settings(settings)
        await update.message.reply_text("✅ File ফিল্টার আপডেট হয়েছে।", reply_markup=media_filter_kb())

    elif t == "📇 Personal তথ্য":
        await update.message.reply_text(
            "যে তথ্যগুলো source post-এর বদলে বসবে:\n"
            f"Username: {settings['replacements']['username'] or '(খালি)'}\n"
            f"Phone: {settings['replacements']['phone'] or '(খালি)'}\n"
            f"Email: {settings['replacements']['email'] or '(খালি)'}\n"
            f"Telegram link: {settings['replacements']['tme_link'] or '(খালি)'}\n\n"
            "পরিবর্তন করতে নিচের অপশন বেছে নিন।",
            reply_markup=ReplyKeyboardMarkup([
                ["👤 Username", "📞 Phone"],
                ["✉️ Email", "🔗 Telegram link"],
                ["⬅️ ফিরে যান"],
            ], resize_keyboard=True))

    elif t in ("👤 Username", "📞 Phone", "✉️ Email", "🔗 Telegram link"):
        field = {
            "👤 Username": "username", "📞 Phone": "phone",
            "✉️ Email": "email", "🔗 Telegram link": "tme_link",
        }[t]
        user_state[uid] = {"step": "await_replacement", "field": field}
        await update.message.reply_text("নতুন তথ্য পাঠান। মুছে দিতে 'না' লিখুন।")

    elif t == "❓ সাহায্য":
        await help_command(update, ctx)

    elif t == "⬅️ ফিরে যান":
        await update.message.reply_text("🏠 মূল মেনুতে ফিরে এলাম।", reply_markup=main_kb(uid))

    else:
        await update.message.reply_text("❌ চিনতে পারিনি। নিচের বাটন ব্যবহার করুন。", reply_markup=main_kb(uid))

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("optin", optin))
    app.add_handler(CommandHandler("optout", optout))
    for command in ("useron", "useroff", "userremove", "userstatus", "retry"):
        app.add_handler(CommandHandler(command, manage_user))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forward))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, handle_document))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, handle_group))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle))
    print(r"""
⢀⡴⠦⢤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡤⠤⡄
⢸⡇⠀⣀⡈⠙⠲⠴⠒⠚⢹⠉⣹⠉⣿⠓⠒⠦⠔⠚⠉⡀⠀⠀⡇
⠀⣧⠀⢹⣩⠗⠀⠀⠀⠀⠛⠀⠛⠀⠛⠀⠀⠀⠀⠰⣏⡟⠀⣸⠁
⠀⠸⡆⠀⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠁⢠⠇⠀
⠀⢠⠏⠀⠀⠀⣠⣴⣦⢄⡀⠀⠀⠀⠀⣠⣦⣦⣄⠀⠀⠀⠸⡄⠀
⠀⣸⠤⠄⠀⣼⣿⣿⣿⣇⢳⠀⠀⠀⣼⣟⠛⣿⣏⢳⠀⢠⣀⡇⠀
⠀⢹⠶⠆⠀⣿⣿⣿⣿⣿⢸⠀⠀⠀⣿⣷⣶⣿⣿⢸⠀⠰⠤⡗⠀
⠀⢸⡚⠂⠀⢹⣿⣿⣿⢇⡞⠠⣤⠀⢻⣿⣿⣿⢏⡞⠃⠰⢒⡇⠀
⠀⠀⢷⡀⠀⠀⠙⠛⠓⠋⠀⠰⠵⠆⠀⠙⠛⠛⠋⠀⠀⢀⡞⠀⠀
⠀⠀⠀⠙⢧⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡴⠋⠀⠀⠀
⠀⠀⠀⠀⠀⠉⠓⠶⠤⣤⣀⣀⣀⣀⣀⣤⠤⠶⠚⠉⠀⠀⠀⠀⠀
🟢 Nishat X System Online
""")
    app.run_polling()

if __name__ == "__main__":
    main()
