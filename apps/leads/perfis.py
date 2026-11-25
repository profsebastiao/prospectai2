import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models


class RedeSocial(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    FACEBOOK = "facebook", "Facebook"
    TIKTOK = "tiktok", "TikTok"
    LINKEDIN = "linkedin", "LinkedIn"
    YOUTUBE = "youtube", "YouTube"
    X = "x", "X"


HOSTS = {
    "instagram.com": RedeSocial.INSTAGRAM, "facebook.com": RedeSocial.FACEBOOK,
    "tiktok.com": RedeSocial.TIKTOK, "linkedin.com": RedeSocial.LINKEDIN,
    "youtube.com": RedeSocial.YOUTUBE, "x.com": RedeSocial.X,
    "twitter.com": RedeSocial.X,
}
CANONICAL_HOST = {rede: host for host, rede in HOSTS.items() if host != "twitter.com"}
PROFILE_HANDLE = re.compile(r"[a-zA-Z0-9_.-]{1,150}\Z")
RESERVED = {
    "p", "reel", "reels", "stories", "explore", "accounts", "login", "share",
    "sharer", "watch", "shorts", "video", "videos", "search", "intent", "i",
    "feed", "groups", "events", "posts", "about", "home", "settings", "redirect",
    "hashtag", "hashtag.php", "share.php", "photo.php", "permalink.php", "story.php",
}


def web_url(value):
    value = value.strip()
    try:
        URLValidator(schemes=["http", "https"])(value)
        parsed = urlsplit(value)
        if parsed.username or parsed.password or not parsed.hostname:
            return ""
    except (ValueError, ValidationError):
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def domain(url):
    host = urlsplit(url).hostname or ""
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            return host[len(prefix):]
    return host


def handle(value):
    value = value.strip().lstrip("@").casefold()
    return value if PROFILE_HANDLE.fullmatch(value) else ""


def profile_identity(url):
    url = web_url(url)
    if not url:
        return None
    parsed = urlsplit(url)
    rede = HOSTS.get(domain(url))
    if not rede:
        return None
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    if not parts:
        return None
    if rede == RedeSocial.FACEBOOK and parts == ["profile.php"]:
        identity = parse_qs(parsed.query).get("id", [""])[0]
        if re.fullmatch(r"[0-9]{1,24}", identity):
            return rede, identity, f"https://facebook.com/profile.php?id={identity}"
        return None
    if rede == RedeSocial.LINKEDIN:
        if len(parts) != 2 or parts[0] not in {"in", "company", "school"}:
            return None
        username = handle(parts[1])
        return (rede, "", f"https://linkedin.com/{parts[0]}/{username}") if username else None
    if rede == RedeSocial.YOUTUBE and len(parts) == 2 and parts[0] in {"channel", "c", "user"}:
        username = parts[1]
        return (rede, "", f"https://youtube.com/{parts[0]}/{username}") if PROFILE_HANDLE.fullmatch(username) else None
    if len(parts) != 1 or parts[0].lower() in RESERVED:
        return None
    if rede in {RedeSocial.YOUTUBE, RedeSocial.TIKTOK} and not parts[0].startswith("@"):
        return None
    username = handle(parts[0])
    prefix = "@" if rede in {RedeSocial.TIKTOK, RedeSocial.YOUTUBE} else ""
    return (rede, username, f"https://{CANONICAL_HOST[rede]}/{prefix}{username}") if username else None


def instagram_handle(value):
    raw = value.strip()
    if raw.lower().startswith(("instagram.com/", "www.instagram.com/", "m.instagram.com/")):
        raw = "https://" + raw
    if "://" in raw:
        parsed = profile_identity(raw)
        return parsed[1] if parsed and parsed[0] == RedeSocial.INSTAGRAM else ""
    return handle(raw)
