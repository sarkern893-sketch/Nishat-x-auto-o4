"""Feature 2 — Automatic Admin Notification System.

Any important failure anywhere in the bot (session expire, publish/forward
failed, AI error, connection problem, permission problem...) should call
`notify_admin(...)`. The same error category is not re-sent more often than
`COOLDOWN_SECONDS`, so a repeating error does not spam the admin.
"""
import json
import time
from pathlib import Path

from config import DATA_DIR, ADMIN_IDS

NOTIFY_STATE_FILE = DATA_DIR / "notify_state.json"
NOTIFY_LOG_FILE = DATA_DIR / "admin_notifications.log"

# Same category will not be re-sent within this window, to avoid spam when a
# problem repeats (e.g. every incoming post fails for the same reason).
COOLDOWN_SECONDS = 20 * 60


def _load_state() -> dict:
    try:
        with NOTIFY_STATE_FILE.open(encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    try:
        temp = NOTIFY_STATE_FILE.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        temp.replace(NOTIFY_STATE_FILE)
    except OSError:
        pass


def _log(category: str, text: str) -> None:
    try:
        with NOTIFY_LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "category": category,
                "text": text,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def notify_admin(bot, category: str, what: str, why: str = "", how_to_fix: str = "", force: bool = False) -> bool:
    """Send a Telegram message to every admin describing a problem.

    category: short stable key (e.g. "session_expired", "publish_failed:<dest>")
              used only for spam-control — same category is throttled.
    what:     কী সমস্যা হয়েছে
    why:      কেন হয়েছে (সহজ ভাষায়)
    how_to_fix: কীভাবে ঠিক করতে হবে (ধাপে ধাপে)
    force:    True হলে cooldown উপেক্ষা করে সবসময় পাঠাবে (খুব critical কিছু হলে)
    """
    state = _load_state()
    now = time.time()
    last = state.get(category, 0)
    if not force and (now - last) < COOLDOWN_SECONDS:
        return False
    state[category] = now
    _save_state(state)

    lines = [f"🚨 Bot Alert: {category.split(':')[0]}", "", f"❗ সমস্যা: {what}"]
    if why:
        lines.append(f"🔍 কারণ: {why}")
    if how_to_fix:
        lines.append(f"🛠️ সমাধানের ধাপ:\n{how_to_fix}")
    text = "\n".join(lines)
    _log(category, text)

    sent_any = False
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
            sent_any = True
        except Exception as error:  # noqa: BLE001 — never let a notification crash the caller
            print(f"⚠️ Admin notify failed for {admin_id}: {error}")
    return sent_any


# ── Ready-made helpers for the most common problems (Feature 2 list) ──

async def notify_session_expired(bot):
    await notify_admin(
        bot, "session_expired",
        what="Personal Telegram Account (user_client)-এর session/login expire হয়ে গেছে।",
        why="Telegram session মেয়াদ শেষ হয়েছে অথবা অন্য কোথাও একই account দিয়ে নতুন login হয়েছে।",
        how_to_fix=(
            "১. Server-এ আবার login করে নতুন session তৈরি করুন (OTP + প্রয়োজনে 2FA password)।\n"
            "২. session ফাইলটি ঠিকভাবে persistent volume-এ রাখা আছে কিনা যাচাই করুন।\n"
            "৩. পুনরায় deploy/restart করুন।"
        ),
        force=True,
    )


async def notify_publish_failed(bot, destination, error):
    await notify_admin(
        bot, f"publish_failed:{destination}",
        what=f"Destination {destination}-এ পোস্ট Publish করা যায়নি।",
        why=f"Telegram error: {str(error)[:200]}",
        how_to_fix=(
            "১. Bot/Account-টি ওই Channel-এ Admin/Post permission আছে কিনা যাচাই করুন।\n"
            "২. Channel ID/username ঠিক আছে কিনা দেখুন।\n"
            "৩. Internet/Telegram সংযোগ ঠিক আছে কিনা যাচাই করুন।"
        ),
    )


async def notify_forward_failed(bot, group, error):
    await notify_admin(
        bot, f"forward_failed:{group}",
        what=f"Group/Channel {group}-এ Forward করা যায়নি।",
        why=f"Telegram error: {str(error)[:200]}",
        how_to_fix=(
            "১. বট/Account-টি ওই Group-এ আছে কিনা এবং Message পাঠানোর অনুমতি আছে কিনা দেখুন।\n"
            "২. Group-এর Privacy/Restricted Content Settings-এর কারণে Forward বন্ধ থাকতে পারে।\n"
            "৩. Group ID ঠিক আছে কিনা যাচাই করুন।"
        ),
    )


async def notify_connection_issue(bot, detail=""):
    await notify_admin(
        bot, "connection_issue",
        what="Internet/Telegram সংযোগে সমস্যা হয়েছে।",
        why=detail or "সাময়িক network/Telegram server সমস্যা।",
        how_to_fix="কিছুক্ষণ পর বট নিজে থেকেই আবার চেষ্টা করবে। সমস্যা চলতে থাকলে Server/Hosting-এর network status যাচাই করুন।",
    )


async def notify_ai_error(bot, error):
    await notify_admin(
        bot, "ai_error",
        what="AI Service (Groq)-এ সমস্যা হয়েছে।",
        why=f"বিস্তারিত: {str(error)[:200]}",
        how_to_fix=(
            "১. GROQ_API_KEY সঠিক ও সচল আছে কিনা যাচাই করুন।\n"
            "২. Groq-এর ব্যবহৃত Model বন্ধ/deprecated হয়ে থাকতে পারে — সেটিংস থেকে model পরিবর্তন করুন।\n"
            "৩. সাময়িক সমস্যা হলে বট automatic fallback ব্যবহার করবে।"
        ),
    )


async def notify_access_issue(bot, chat, error):
    await notify_admin(
        bot, f"access_issue:{chat}",
        what=f"{chat}-এ Access/Permission সমস্যা হয়েছে।",
        why=f"বিস্তারিত: {str(error)[:200]}",
        how_to_fix=(
            "১. Personal Account/Bot ওই Channel/Group-এ Member/Admin আছে কিনা যাচাই করুন।\n"
            "২. Channel/Group Private হলে Invite Link দিয়ে যোগ করান।\n"
            "৩. ID/Username সঠিক আছে কিনা আবার চেক করুন।"
        ),
    )
