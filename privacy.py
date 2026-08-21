import re

# 🛡️ প্রাইভেসি ফিল্টার — প্রতিটা রুল ON/OFF (অ্যাডমিন প্যানেল থেকে)
# ক্রম গুরুত্বপূর্ণ: email আগে চেক করা হয়, কারণ email-এর "@domain" অংশ
# username প্যাটার্নের সাথেও মিলে যেতে পারে (যেমন karim@example.com-এর
# "@example" অংশ) — username আগে চললে email আংশিক কেটে গিয়ে ভুল ফলাফল দিত।
PRIVACY = {
    "email":    {"on": True,  "pattern": r"[\w.+-]+@[\w-]+\.[\w.]+"},
    "username": {"on": True,  "pattern": r"@[\w\d_]{3,}"},
    "tme_link": {"on": True,  "pattern": r"https?://t\.me/[\w\d_]+"},
    "phone":    {"on": True,  "pattern": r"(?<![\d])(?:\+?88)?01[3-9]\d{8}(?![\d])"},
    "user_id":  {"on": False, "pattern": r"(?<![\d])\d{9,10}(?![\d])"},
}

def clean_personal(text: str, enabled_rules=None, replacements=None) -> str:
    """সব ON রুল প্রয়োগ করে ব্যক্তিগত তথ্য মুছে দেয়"""
    for name, rule in PRIVACY.items():
        enabled = rule["on"]
        if enabled_rules is not None and name in enabled_rules:
            enabled = enabled_rules[name].get("on", enabled)
        if enabled:
            replacement = (replacements or {}).get(name, "")
            text = re.sub(rule["pattern"], replacement, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def replace_personal(text: str, replacements=None) -> str:
    """Replace detected personal values with admin-configured values."""
    replacements = replacements or {}
    values = {
        "username": replacements.get("username", ""),
        "phone": replacements.get("phone", ""),
        "email": replacements.get("email", ""),
        "tme_link": replacements.get("tme_link", ""),
    }
    for name, rule in PRIVACY.items():
        if name in values and values[name]:
            text = re.sub(rule["pattern"], values[name], text)
    return text
