import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
API_ID = int(os.getenv("TELEGRAM_API_ID") or "0")
API_HASH = os.getenv("TELEGRAM_API_HASH")
_session = os.getenv("TELEGRAM_SESSION", "telegram_user")
SESSION = _session if os.path.isabs(_session) else str(DATA_DIR / _session)
PHONE = os.getenv("TELEGRAM_PHONE")
DELAY_MINUTES = int(os.getenv("DELAY_MINUTES") or "0")
TEMPLATE_HEADER = os.getenv("TEMPLATE_HEADER", "")
TEMPLATE_FOOTER = os.getenv("TEMPLATE_FOOTER", "")
EMOJI_EDITING = os.getenv("EMOJI_EDITING", "true").lower() in ("1", "true", "yes", "on")
