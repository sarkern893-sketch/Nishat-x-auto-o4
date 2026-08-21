"""Small Groq REST client with safe fallback behavior.

The key is read only from the GROQ_API_KEY environment variable.  No key is
stored in settings.json or written to logs.
"""
import asyncio
import json
import os
import urllib.error
import urllib.request


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq periodically deprecates/decommissions older model IDs. This is the
# safe, currently-supported model we fall back to if the configured model
# in settings.json is no longer valid (HTTP 404 model_not_found), so old
# deployments don't silently break. Keep in sync with settings_store's
# DEPRECATED_MODEL_MIGRATIONS default target.
FALLBACK_MODEL = "openai/gpt-oss-120b"


def _request(payload: dict, api_key: str) -> str:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        GROQ_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; NishatXBot/1.0; +https://railway.app)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise urllib.error.HTTPError(error.url, error.code, f"{error.reason} — {detail}", error.headers, None)
    return data["choices"][0]["message"]["content"].strip()


async def ask_groq(
    prompt: str,
    *,
    system: str,
    model: str = FALLBACK_MODEL,
    max_tokens: int = 900,
) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY সেট করা নেই")
    payload_base = {
        "temperature": 0.35,
        "max_tokens": max(100, min(max_tokens, 1800)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    # Try the configured model first; if Groq says it no longer exists
    # (deprecated/decommissioned), fall back to FALLBACK_MODEL automatically
    # instead of failing the whole request.
    models_to_try = [model] if model == FALLBACK_MODEL else [model, FALLBACK_MODEL]
    last_error = None
    for candidate_model in models_to_try:
        payload = {**payload_base, "model": candidate_model}
        for attempt in range(2):
            try:
                return await asyncio.to_thread(_request, payload, api_key)
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code == 404:
                    break  # unknown/deprecated model — stop retrying this one, try next candidate
                if attempt == 0:
                    await asyncio.sleep(1)
            except (urllib.error.URLError, KeyError, ValueError) as error:
                last_error = error
                if attempt == 0:
                    await asyncio.sleep(1)
    raise RuntimeError(f"Groq request failed: {last_error}")




def _identity_instructions(ai: dict) -> str:
    """Build a system-prompt block enforcing a custom AI identity/owner and
    hiding the real model/provider name. Fully configurable from the admin
    panel via settings['ai']['identity_name' / 'owner_name' / 'identity_filter']."""
    name = (ai.get("identity_name") or "").strip()
    owner = (ai.get("owner_name") or "").strip()
    extra = (ai.get("identity_filter") or "").strip()
    parts = []
    if name:
        parts.append(
            f"তুমি নিজের নাম/পরিচয় হিসেবে সবসময় '{name}' ব্যবহার করবে। "
            "তুমি কখনো ChatGPT, OpenAI, GPT, Groq, Llama, Meta, বা অন্য কোনো "
            "company/model-এর নাম নিজের পরিচয় হিসেবে বলবে না, উল্লেখও করবে না।"
        )
    else:
        parts.append(
            "তুমি কখনো ChatGPT, OpenAI, GPT, Groq, Llama, Meta, বা তোমাকে কে "
            "বানিয়েছে সেই company/model-এর নাম প্রকাশ করবে না।"
        )
    if owner:
        parts.append(f"কেউ তোমার মালিক/owner/creator/developer কে জিজ্ঞেস করলে বলবে: '{owner}'।")
    else:
        parts.append("কেউ তোমার মালিক/owner/creator জিজ্ঞেস করলে ব্যক্তিগত তথ্য না দিয়ে সংক্ষেপে এড়িয়ে যাবে।")
    if extra:
        parts.append(f"অতিরিক্ত নিয়ম (admin-সেট করা): {extra}")
    return " ".join(parts)


def _knowledge_block(ai: dict) -> str:
    """Admin-provided reference info (FAQ, product details, rules, etc.) that
    the AI may draw on when answering. settings['ai']['private_knowledge']."""
    text = (ai.get("private_knowledge") or "").strip()
    if not text:
        return ""
    return (
        "\n\n=== অনুমোদিত তথ্য (শুধু প্রাসঙ্গিক প্রশ্নে ব্যবহার করুন) ===\n"
        f"{text}\n"
        "এই তথ্যের মধ্যে কোনো password, token, API key বা অন্য গোপনীয় কিছু থাকলে "
        "তা কখনো প্রকাশ করবেন না।\n=== তথ্য শেষ ===\n"
    )


def _master_block(ai: dict) -> str:
    """A free-form rulebook the admin writes (by text or .txt upload) that the
    AI must follow above everything else. settings['ai']['master_instruction']."""
    text = (ai.get("master_instruction") or "").strip()
    if not text:
        return ""
    return f"\n\n=== সর্বোচ্চ অগ্রাধিকার নির্দেশনা (admin-এর দেওয়া, সবসময় মেনে চলবে) ===\n{text}\n=== নির্দেশনা শেষ ===\n"


def _format_style_block(settings: dict) -> str:
    """Feature 10 — Custom AI Post Format Style. When the admin has turned
    this on, tell the AI to wrap the post using the chosen pieces instead of
    a plain paragraph rewrite."""
    fs = settings.get("format_style", {})
    if not fs.get("enabled"):
        return ""
    border = fs.get("border") or "━━━━━━━━━━━━━━━━━━"
    parts = [
        "\n\n=== Post Format Style (admin-নির্ধারিত, বাধ্যতামূলক মেনে চলুন) ===",
        f"এই border লাইন উপরে ও নিচে ব্যবহার করুন: {border}",
    ]
    if fs.get("header"):
        parts.append(f"সবার উপরে এই হেডার/টাইটেল লাইন রাখুন (প্রয়োজনে পোস্টের বিষয় অনুযায়ী সামান্য মানানসই করে নিন): {fs['header']}")
    if fs.get("use_emoji_heading", True):
        parts.append("প্রতিটি গুরুত্বপূর্ণ সেকশনের শুরুতে একটি মানানসই emoji ব্যবহার করুন (যেমন 📦 Product, 💰 Price, ✨ Features, 📩 Contact)।")
    if fs.get("use_bullets", True):
        parts.append("গুরুত্বপূর্ণ তথ্য/ফিচার থাকলে সেগুলো • bullet point-এ সাজান।")
    if fs.get("footer"):
        parts.append(f"সবার নিচে (contact-এর পরে, শেষ border-এর আগে) এই ফুটার লাইন রাখুন: {fs['footer']}")
    if fs.get("contact_line"):
        parts.append(f"একটি Contact লাইন যোগ করুন (যদি মূল পোস্টে না থাকে): {fs['contact_line']}")
    parts.append("=== Format Style শেষ ===\n")
    return "\n".join(parts)


async def edit_post_with_ai(text: str, settings: dict) -> str:
    ai = settings.get("ai", {})
    style = ai.get("style") or "পরিষ্কার, স্বাভাবিক ও পেশাদার"
    length = ai.get("length") or "মূল পোস্টের কাছাকাছি"
    emoji = "প্রয়োজনে অল্প emoji ব্যবহার করুন" if ai.get("emoji", True) else "কোনো emoji ব্যবহার করবেন না"
    custom = ai.get("custom_prompt") or "কোনো অতিরিক্ত নির্দেশ নেই"
    system = (
        "আপনি Telegram পোস্ট সম্পাদক। মূল তথ্য, সংখ্যা, নাম, লিংক ও অর্থ পরিবর্তন করবেন না। "
        "অপ্রয়োজনীয় call-to-action, বিজ্ঞাপন বা নতুন তথ্য যোগ করবেন না। একই ভাষায় লিখুন। "
        "শুধুমাত্র নিচে দেওয়া 'অনুমোদিত তথ্য' থেকেই owner-এর নির্ধারিত তথ্য (যেমন price/contact) যোগ করবেন, "
        "নিজে থেকে কোনো নতুন তথ্য/দাম/প্রতিশ্রুতি বানাবেন না। "
        f"Style: {style}. Length: {length}. {emoji}. Custom instruction: {custom}. "
        "শুধু সম্পাদিত পোস্টটি ফেরত দিন, কোনো ব্যাখ্যা নয়।"
        f"{_knowledge_block(ai)}"
        f"{_format_style_block(settings)}"
        f"{_master_block(ai)}"
    )
    return await ask_groq(f"এই পোস্টটি সম্পাদনা করুন:\n\n{text}", system=system, model=ai.get("model", "openai/gpt-oss-120b"))


async def answer_group_message(text: str, settings: dict, context: str = "") -> str:
    style = settings.get("style") or "সহায়ক, ভদ্র ও সংক্ষিপ্ত"
    length = settings.get("answer_length") or "মাঝারি"
    custom = settings.get("custom_prompt") or "কোনো অতিরিক্ত নির্দেশ নেই"
    context_text = f"\nসাম্প্রতিক context:\n{context}" if context and settings.get("context_enabled", True) else ""
    identity = _identity_instructions(settings)
    system = (
        "আপনি Telegram group-এর AI assistant। ব্যবহারকারী যে ভাষায় লিখেছে সেই ভাষাতেই উত্তর দিন। "
        "নিশ্চিত না হলে বানিয়ে বলবেন না; সংক্ষেপে জানাবেন। "
        f"Style: {style}. Answer length: {length}. Custom instruction: {custom}. "
        f"{identity}"
        f"{_knowledge_block(settings)}"
        f"{_master_block(settings)}"
    )
    return await ask_groq(f"ব্যবহারকারীর message:\n{text}{context_text}", system=system)
