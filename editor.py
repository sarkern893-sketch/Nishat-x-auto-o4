"""Minimal, structure-preserving post formatting.

The editor intentionally does not rewrite the post. It only normalizes spacing
and adds a small amount of configurable decoration, so source formatting and
meaning remain intact.
"""
import re


def edit_post(text: str, emoji_enabled: bool = True) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not emoji_enabled or not text:
        return text

    # Add decoration only to common announcement-like headings that do not
    # already start with an emoji. Do not alter ordinary paragraphs.
    first = text.split("\n", 1)[0].strip()
    if first and not re.match(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF]", first):
        if re.search(r"(অফার|ছাড়|জরুরি|আপডেট|নতুন|offer|update|breaking)", first, re.I):
            text = "📢 " + text
    return text