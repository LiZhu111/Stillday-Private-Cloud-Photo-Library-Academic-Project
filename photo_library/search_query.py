"""Recognize plain semantic photo searches that can skip tool selection."""
import re


def is_simple_photo_search(prompt):
    """Recognize a plain visual search that can skip the chat tool-selection call."""
    text = prompt.strip().lower()
    starts_search = re.match(
        r"^(?:please\s+)?(?:show|find|search|display|look\s+for)(?:\s+(?:me|us))?\b|"
        r"^(?:\u8bf7\u5e2e\u6211|\u5e2e\u6211|\u8bf7|\u7ed9\u6211)?(?:\u663e\u793a|\u67e5\u770b|\u627e\u627e?|\u641c\u7d22)",
        text,
    )
    if not starts_search or not re.search(r"\b(?:photos?|pictures?|images?)\b|\u7167\u7247|\u56fe\u7247|\u76f8\u7247", text):
        return False
    excluded = (
        r"\b(?:album|collection|save|create|delete|remove|trash|restore|upload|slideshow|play|compare|"
        r"duplicate|review|favorite|favourite|before|after|between|since|until|from|during|today|yesterday|"
        r"last\s+(?:summer|winter|spring|autumn|fall|week|month|year)|(?:19|20)\d{2}|"
        r"mum|mom|mother|dad|father|grandma|grandmother|grandpa|grandfather|aunt|uncle)\b"
    )
    if re.search(excluded, text):
        return False
    remainder = re.sub(
        r"^(?:please\s+)?(?:show|find|search|display|look\s+for)(?:\s+(?:me|us))?\b|"
        r"\b(?:photos?|pictures?|images?|a|an|the|some|any|all|my|me|us|of|with|that|has|have|"
        r"showing|containing|where|in|for)\b|"
        r"\u8bf7\u5e2e\u6211|\u5e2e\u6211|\u8bf7|\u7ed9\u6211|\u663e\u793a|\u67e5\u770b|\u627e\u627e?|\u641c\u7d22|\u7684|\u6709|\u4e2d|\u7167\u7247|\u56fe\u7247|\u76f8\u7247",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return bool(re.search(r"[a-z\u3400-\u9fff]", remainder))
