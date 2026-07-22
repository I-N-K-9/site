#!/usr/bin/env python3
"""
Импорт сообщения из Telegram в Nikola.

Примеры:
    python import_tg.py https://t.me/BORDER_NOT_PI/236 /path/to/nikola
    python import_tg.py https://t.me/my_channel/100 https://t.me/source/200

Требования:
    pip install telethon python-dateutil
"""

import os
import re
import sys
import shutil
import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse
from datetime import datetime, timedelta
from dateutil import tz
from telethon import TelegramClient, utils
from telethon.errors import ChannelInvalidError, MessageIdInvalidError


# === Настройки ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.environ.get(
    "TG_SESSION", os.path.join(SCRIPT_DIR, "telegram_session")
)
NIKOLA_ROOT = "."  # по умолчанию
POSTS_SUBDIR = "posts"
IMAGES_SUBDIR = "images"

TG_URL_RE = re.compile(r"https?://t\.me/([^/]+)/(\d+)")
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^&\s]+&)*v=|youtu\.be/)([A-Za-z0-9_-]{11})"
)


@dataclass
class RepostSource:
    text: str = ""
    source_url: str = ""
    source_title: str = ""
    media_files: list = field(default_factory=list)


@dataclass
class PostBundle:
    comment: str = ""
    comment_media: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    dt: datetime = None
    is_repost: bool = False


def ensure_dirs(root):
    posts_dir = os.path.join(root, POSTS_SUBDIR)
    images_dir = os.path.join(root, IMAGES_SUBDIR)
    os.makedirs(posts_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    return posts_dir, images_dir


def proxy_from_env():
    """Parse https_proxy / HTTPS_PROXY into Telethon proxy dict."""
    proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        raise ValueError(f"Invalid proxy URL: {proxy_url}")

    scheme = (parsed.scheme or "http").lower()
    scheme_map = {
        "http": "http",
        "https": "http",
        "socks5": "socks5",
        "socks4": "socks4",
    }
    proxy_type = scheme_map.get(scheme)
    if not proxy_type:
        raise ValueError(f"Unsupported proxy scheme: {scheme}")

    default_ports = {"http": 8080, "socks5": 1080, "socks4": 1080}
    proxy = {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": parsed.port or default_ports[proxy_type],
    }
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def ensure_proxy_lib():
    """Telethon routes all proxies (including HTTP) through python-socks or PySocks."""
    try:
        import python_socks  # noqa: F401
        return
    except ImportError:
        pass
    try:
        import socks  # noqa: F401
        return
    except ImportError:
        print(
            "Proxy is set via https_proxy, but no proxy library is installed.\n"
            "Telethon uses python-socks/PySocks for HTTP proxies too (not urllib/requests).\n"
            "Install with: pip install 'python-socks[asyncio]'"
        )
        sys.exit(1)


def is_tg_url(arg: str) -> bool:
    return bool(TG_URL_RE.match(arg))


def parse_url(url: str):
    """Разбираем https://t.me/channel/id"""
    m = TG_URL_RE.match(url)
    if not m:
        raise ValueError(f"Некорректная ссылка: {url}")
    return m.group(1), int(m.group(2))


def parse_cli_args(argv):
    """Разбираем URL-ы и необязательный NIKOLA_ROOT."""
    urls = []
    nikola_root = None
    for arg in argv[1:]:
        if is_tg_url(arg):
            urls.append(arg)
        elif urls:
            nikola_root = os.path.abspath(arg)
            break
    if not urls:
        return None, None
    return urls, nikola_root or os.path.abspath(NIKOLA_ROOT)


def format_date_for_header(dt: datetime) -> str:
    """Дата в формате для Nikola: YYYY-MM-DD HH:MM:SS UTC+hh:mm"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.tzlocal())
    offset = dt.utcoffset() or timedelta(0)
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    hh = abs(total_seconds) // 3600
    mm = (abs(total_seconds) % 3600) // 60
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f" UTC{sign}{hh:02d}:{mm:02d}"


def filename_from_dt(dt: datetime) -> str:
    """Имя файла поста: 2025-06-05_14-00-12-223.rst"""
    ms = dt.microsecond // 1000
    return dt.strftime(f"%Y-%m-%d_%H-%M-%S-{ms:03d}.rst")


def entity_title(entity) -> str:
    if entity is None:
        return ""
    return (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or "Unknown"
    )


def tme_link(entity, msg_id: int, fallback_channel: str = "") -> str:
    username = getattr(entity, "username", None) if entity else None
    if username:
        return f"https://t.me/{username}/{msg_id}"
    if fallback_channel and not fallback_channel.startswith("c/"):
        return f"https://t.me/{fallback_channel}/{msg_id}"
    peer_id = utils.get_peer_id(entity) if entity else None
    if peer_id is not None:
        cid = str(peer_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        return f"https://t.me/c/{cid}/{msg_id}"
    return ""


def indent_quote(text: str) -> str:
    lines = text.strip().splitlines()
    return "\n".join(f"  {line}" if line else "" for line in lines)


def extract_youtube_ids(text: str) -> list:
    seen = set()
    ids = []
    for match in YOUTUBE_RE.finditer(text or ""):
        vid = match.group(1)
        if vid not in seen:
            seen.add(vid)
            ids.append(vid)
    return ids


def is_link_only(text: str) -> bool:
    """True if the whole body is a single http(s)/YouTube URL (legacy youtube-repost style)."""
    non_empty = [line for line in (text or "").splitlines() if line.strip()]
    if len(non_empty) != 1:
        return False
    line = non_empty[0].strip()
    return bool(
        YOUTUBE_RE.search(line)
        or line.startswith("http://")
        or line.startswith("https://")
    )


def format_repost_source(source: RepostSource, *, force_quote: bool = False) -> str:
    text = (source.text or "").strip()
    if not text:
        return ""
    # Only skip quote block for bare YouTube/link-only bodies (old Repost style).
    if not force_quote and is_link_only(text):
        return text

    if source.source_url and source.source_title:
        header = (
            f'Цитируемое сообщение (оригинал в '
            f'`Telegram "{source.source_title}" <{source.source_url}>`_)'
        )
    elif source.source_url:
        header = (
            f'Цитируемое сообщение (оригинал в '
            f'`{source.source_url} <{source.source_url}>`_)'
        )
    else:
        header = "Цитируемое сообщение"
    return f"{header}\n\n{indent_quote(text)}"


def pick_title(bundle: PostBundle) -> str:
    for source in bundle.sources:
        for line in (source.text or "").splitlines():
            line = line.strip()
            if line and len(line) < 80 and not line.startswith("http"):
                return line
    for line in (bundle.comment or "").splitlines():
        line = line.strip()
        if line and len(line) < 80:
            return line
    return "Telegram import"


async def save_message_media(client, msg, channel, msg_id, images_dir):
    saved_files = []

    async def save_msg_media(m):
        saved = await m.download_media(file=images_dir)
        if isinstance(saved, list):
            return [os.path.abspath(p) for p in saved if p]
        if saved:
            return [os.path.abspath(saved)]
        return []

    if getattr(msg, "grouped_id", None):
        gid = msg.grouped_id
        nearby = await client.get_messages(
            channel, ids=range(max(1, msg_id - 50), msg_id + 51)
        )
        group_msgs = [m for m in nearby if getattr(m, "grouped_id", None) == gid]
        group_msgs = sorted(group_msgs, key=lambda m: m.id)
        for m in group_msgs:
            if m.media:
                saved_files.extend(await save_msg_media(m))
    elif msg.media:
        saved_files.extend(await save_msg_media(msg))

    seen = set()
    final_files = []
    for p in saved_files:
        if not p:
            continue
        bname = os.path.basename(p)
        target = os.path.join(images_dir, bname)
        if os.path.abspath(p) != os.path.abspath(target):
            os.makedirs(images_dir, exist_ok=True)
            try:
                shutil.move(p, target)
            except Exception:
                try:
                    shutil.copy2(p, target)
                except Exception:
                    continue
        if target not in seen:
            seen.add(target)
            final_files.append(os.path.abspath(target))
    return final_files


async def resolve_forward_source(client, msg):
    """Return (orig_msg_or_None, entity_or_None, orig_msg_id_or_None, title)."""
    if not msg or not msg.fwd_from:
        return None, None, None, ""

    fwd = msg.fwd_from
    entity = None
    orig_id = None

    if fwd.channel_post:
        orig_id = fwd.channel_post
        peer = fwd.from_id or fwd.saved_from_peer
        if peer:
            try:
                entity = await client.get_entity(peer)
            except Exception:
                entity = None
    elif fwd.saved_from_msg_id and fwd.saved_from_peer:
        orig_id = fwd.saved_from_msg_id
        try:
            entity = await client.get_entity(fwd.saved_from_peer)
        except Exception:
            entity = None

    title = ""
    if entity:
        title = entity_title(entity)
    elif fwd.from_name:
        title = fwd.from_name
    elif msg.forward:
        title = entity_title(msg.forward.chat or msg.forward.sender)

    orig = None
    if entity is not None and orig_id is not None:
        try:
            orig = await client.get_messages(entity, ids=orig_id)
        except Exception:
            orig = None
    return orig, entity, orig_id, title


async def fetch_post(client, url: str, images_dir, *, resolve_forward=True) -> PostBundle:
    channel_name, msg_id = parse_url(url)
    try:
        msg = await client.get_messages(channel_name, ids=msg_id)
    except (ChannelInvalidError, MessageIdInvalidError) as e:
        raise RuntimeError(f"Не удалось получить сообщение {url}: {e}")

    if not msg:
        raise ValueError(f"Сообщение не найдено: {url}")

    text = msg.message or ""
    media_files = await save_message_media(client, msg, channel_name, msg_id, images_dir)
    bundle = PostBundle(
        comment=text.strip(),
        comment_media=media_files,
        dt=msg.date,
        is_repost=bool(msg.fwd_from),
    )

    if resolve_forward and msg.fwd_from:
        orig, entity, orig_id, title = await resolve_forward_source(client, msg)
        source_url = ""
        if entity and orig_id is not None:
            source_url = tme_link(entity, orig_id)
        # Prefer original message body; fall back to text carried by the forward itself.
        if orig and (orig.message or "").strip():
            source_text = orig.message.strip()
            source_media = await save_message_media(
                client, orig, entity, orig.id, images_dir
            )
        else:
            source_text = text.strip()
            source_media = list(media_files)
            # Forward body is the source, not a separate comment.
            bundle.comment = ""
            bundle.comment_media = []
        bundle.sources.append(
            RepostSource(
                text=source_text,
                source_url=source_url,
                source_title=title,
                media_files=source_media,
            )
        )
    return bundle


async def source_from_url(client, url: str, images_dir) -> RepostSource:
    """Build a RepostSource from an explicit CLI URL.

    If the message is itself a forward, link/title/text come from the *original*
    (fwd_from), not from the URL that was passed.
    """
    channel_name, msg_id = parse_url(url)
    post = await fetch_post(client, url, images_dir, resolve_forward=True)

    if post.sources:
        source = post.sources[0]
        if not source.text and post.comment:
            source.text = post.comment
        if not source.media_files and post.comment_media:
            source.media_files = list(post.comment_media)
        return source

    try:
        entity = await client.get_entity(channel_name)
    except Exception:
        entity = None
    return RepostSource(
        text=post.comment,
        source_url=tme_link(entity, msg_id, channel_name) or url,
        source_title=entity_title(entity) or channel_name,
        media_files=list(post.comment_media),
    )


async def enrich_source_metadata(client, bundle: PostBundle):
    for source in bundle.sources:
        if not source.source_url:
            continue
        try:
            entity = await client.get_entity(source.source_url)
        except Exception:
            continue
        if not source.source_title:
            source.source_title = entity_title(entity)
        m = TG_URL_RE.search(source.source_url)
        if m:
            source.source_url = tme_link(entity, int(m.group(2))) or source.source_url
        elif "/c/" in source.source_url:
            msg_id = int(source.source_url.rsplit("/", 1)[-1])
            source.source_url = tme_link(entity, msg_id) or source.source_url


async def fetch_posts(client, urls: list[str], images_dir) -> PostBundle:
    # Single URL: auto-detect Telegram forward on that message.
    if len(urls) == 1:
        primary = await fetch_post(client, urls[0], images_dir, resolve_forward=True)
        if primary.sources:
            await enrich_source_metadata(client, primary)
        return primary

    # Multiple URLs: first = comment, rest = quoted sources.
    # Source link comes from each source message's original (fwd_from), not from
    # blindly using the CLI URL when that URL is a forward in your channel.
    primary = await fetch_post(client, urls[0], images_dir, resolve_forward=False)
    merged = PostBundle(
        comment=primary.comment,
        comment_media=list(primary.comment_media),
        dt=primary.dt,
        is_repost=True,
        sources=[],
    )
    for url in urls[1:]:
        merged.sources.append(await source_from_url(client, url, images_dir))
    if merged.sources:
        await enrich_source_metadata(client, merged)
    return merged


def render_body(bundle: PostBundle) -> str:
    if not bundle.is_repost:
        parts = [bundle.comment] if bundle.comment else []
        return "\n\n".join(p for p in parts if p)

    parts = []
    if bundle.comment:
        parts.append(bundle.comment)
    for source in bundle.sources:
        formatted = format_repost_source(source)
        if formatted:
            parts.append(formatted)
    return "\n\n".join(parts)


def create_rst(nikola_root, bundle: PostBundle, title=None):
    posts_dir = os.path.join(nikola_root, POSTS_SUBDIR)
    images_sub = IMAGES_SUBDIR
    os.makedirs(posts_dir, exist_ok=True)

    fname = filename_from_dt(bundle.dt)
    fullpath = os.path.join(posts_dir, fname)
    date_header = format_date_for_header(bundle.dt)
    category = "Repost" if bundle.is_repost else ""
    title = title or pick_title(bundle)
    body = render_body(bundle)

    lines = [
        f".. title: {title}",
        f".. slug: {bundle.dt.strftime('%Y-%m-%d')}",
        f".. date: {date_header}",
        f".. tags:",
        f".. category: {category}",
        f".. description:",
        f".. type: text",
        "",
    ]

    if bundle.is_repost and bundle.comment:
        lines.append(bundle.comment.strip())
        lines.append("")
        for path in bundle.comment_media:
            b = os.path.basename(path)
            lines.append(f".. thumbnail:: /{images_sub}/{b}")
        if bundle.comment_media:
            lines.append("")
        for source in bundle.sources:
            formatted = format_repost_source(source, force_quote=True)
            if formatted:
                lines.append(formatted)
                lines.append("")
                for vid in extract_youtube_ids(formatted):
                    lines.append(f".. youtube:: {vid}")
                for path in source.media_files:
                    b = os.path.basename(path)
                    lines.append(f".. thumbnail:: /{images_sub}/{b}")
                lines.append("")
    elif bundle.is_repost:
        for source in bundle.sources:
            formatted = format_repost_source(source)
            if formatted:
                lines.append(formatted)
                lines.append("")
                for vid in extract_youtube_ids(formatted):
                    lines.append(f".. youtube:: {vid}")
                for path in source.media_files:
                    b = os.path.basename(path)
                    lines.append(f".. thumbnail:: /{images_sub}/{b}")
                lines.append("")
    else:
        lines.append(body.strip())
        lines.append("")
        for vid in extract_youtube_ids(body):
            lines.append(f".. youtube:: {vid}")
        for path in bundle.comment_media:
            b = os.path.basename(path)
            lines.append(f".. thumbnail:: /{images_sub}/{b}")

    while lines and lines[-1] == "":
        lines.pop()

    with open(fullpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return fullpath


async def main(argv):
    urls, nikola_root = parse_cli_args(argv)
    if not urls:
        print("Использование: python import_tg.py <telegram_url> [ещё url ...] [NIKOLA_ROOT]")
        sys.exit(1)

    global NIKOLA_ROOT
    NIKOLA_ROOT = nikola_root
    posts_dir, images_dir = ensure_dirs(NIKOLA_ROOT)

    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print("Введите Telegram API credentials (можно сохранить в TG_API_ID и TG_API_HASH):")

    if not api_id:
        api_id = input("api_id: ").strip()
    else:
        print("api_id:", api_id)

    if not api_hash:
        api_hash = input("api_hash: ").strip()

    proxy = proxy_from_env()
    if proxy:
        ensure_proxy_lib()
        print(f"[i] Using proxy: {proxy['addr']}:{proxy['port']} ({proxy['proxy_type']})")

    session_file = f"{SESSION_PATH}.session"
    print(f"[i] Session file: {session_file}")

    client = TelegramClient(SESSION_PATH, int(api_id), api_hash, proxy=proxy)
    await client.connect()
    if not await client.is_user_authorized():
        print("[!] No authorized session found, starting interactive login...")
        await client.start()
    else:
        me = await client.get_me()
        name = me.first_name if me and me.first_name else "unknown"
        phone = me.phone if me and me.phone else "unknown"
        print(f"[i] Logged in as {name} ({phone})")

    try:
        bundle = await fetch_posts(client, urls, images_dir)
    finally:
        await client.disconnect()

    if bundle.is_repost:
        print("[i] Detected repost")

    rst_path = create_rst(NIKOLA_ROOT, bundle)
    print(f"[+] RST saved: {rst_path}")

    all_media = list(bundle.comment_media)
    for source in bundle.sources:
        all_media.extend(source.media_files)
    if all_media:
        print("[+] Media saved:")
        for p in all_media:
            print("   -", p)
    else:
        print("[i] No media saved for this post.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
