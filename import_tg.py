#!/usr/bin/env python3
"""
Импорт сообщения из Telegram в Nikola.

Пример:
    python import_tg.py https://t.me/BORDER_NOT_PI/236 /path/to/nikola

Требования:
    pip install telethon python-dateutil
"""

import os
import re
import sys
import shutil
import asyncio
from datetime import datetime, timedelta
from dateutil import tz
from telethon import TelegramClient
from telethon.errors import ChannelInvalidError, MessageIdInvalidError


# === Настройки ===
SESSION_NAME = "telegram_session"  # где хранится сессия Telegram
NIKOLA_ROOT = "."  # по умолчанию
POSTS_SUBDIR = "posts"
IMAGES_SUBDIR = "images"


def ensure_dirs(root):
    posts_dir = os.path.join(root, POSTS_SUBDIR)
    images_dir = os.path.join(root, IMAGES_SUBDIR)
    os.makedirs(posts_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    return posts_dir, images_dir


def parse_url(url: str):
    """Разбираем https://t.me/channel/id"""
    m = re.match(r"https?://t\.me/([^/]+)/(\d+)", url)
    if not m:
        raise ValueError(f"Некорректная ссылка: {url}")
    return m.group(1), int(m.group(2))


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


async def fetch_and_save_media(client: TelegramClient, channel, msg_id, images_dir):
    """Получает текст, сохраняет медиа, возвращает (text, [files], datetime)"""
    try:
        msg = await client.get_messages(channel, ids=msg_id)
    except (ChannelInvalidError, MessageIdInvalidError) as e:
        raise RuntimeError(f"Не удалось получить сообщение: {e}")

    if not msg:
        raise ValueError("Сообщение не найдено")

    text = msg.message or ""
    dt = msg.date  # timezone-aware datetime
    saved_files = []

    async def save_msg_media(m):
        """Сохраняет медиа из одного сообщения"""
        saved = await m.download_media(file=images_dir)
        if isinstance(saved, list):
            return [os.path.abspath(p) for p in saved if p]
        elif saved:
            return [os.path.abspath(saved)]
        return []

    # Если сообщение — часть альбома
    if getattr(msg, "grouped_id", None):
        gid = msg.grouped_id
        nearby = await client.get_messages(channel, ids=range(max(1, msg_id - 50), msg_id + 51))
        group_msgs = [m for m in nearby if getattr(m, "grouped_id", None) == gid]
        group_msgs = sorted(group_msgs, key=lambda m: m.id)
        for m in group_msgs:
            if m.media:
                saved_files.extend(await save_msg_media(m))
    else:
        if msg.media:
            saved_files.extend(await save_msg_media(msg))

    # Убираем дубликаты и приводим пути
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

    return text, final_files, dt


def create_rst(nikola_root, text, media_files, msg_dt, title=None):
    posts_dir = os.path.join(nikola_root, POSTS_SUBDIR)
    images_sub = IMAGES_SUBDIR
    os.makedirs(posts_dir, exist_ok=True)

    fname = filename_from_dt(msg_dt)
    fullpath = os.path.join(posts_dir, fname)
    date_header = format_date_for_header(msg_dt)

    if not title:
        title = "Telegram import"

    lines = [
        f".. title: {title}",
        f".. slug: {msg_dt.strftime('%Y-%m-%d')}",
        f".. date: {date_header}",
        f".. tags:",
        f".. category:",
        f".. description:",
        f".. type: text",
        "",
        text.strip(),
        "",
    ]

    for p in media_files:
        b = os.path.basename(p)
        lines.append(f".. thumbnail:: /{images_sub}/{b}")

    with open(fullpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return fullpath


async def main(argv):
    if len(argv) < 2:
        print("Использование: python import_tg.py <telegram_url> [NIKOLA_ROOT]")
        sys.exit(1)

    url = argv[1]
    global NIKOLA_ROOT
    if len(argv) >= 3:
        NIKOLA_ROOT = os.path.abspath(argv[2])

    posts_dir, images_dir = ensure_dirs(NIKOLA_ROOT)
    channel, msg_id = parse_url(url)

    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print("Введите Telegram API credentials (можно сохранить в TG_API_ID и TG_API_HASH):")
        api_id = input("api_id: ").strip()
        api_hash = input("api_hash: ").strip()

    client = TelegramClient(SESSION_NAME, int(api_id), api_hash)
    await client.start()

    try:
        text, files, dt = await fetch_and_save_media(client, channel, msg_id, images_dir)
    finally:
        await client.disconnect()

    title = None
    first_line = (text.strip().splitlines()[0] if text else "").strip()
    if first_line and len(first_line) < 80:
        title = first_line

    rst_path = create_rst(NIKOLA_ROOT, text, files, dt, title=title)
    print(f"[+] RST saved: {rst_path}")
    if files:
        print("[+] Media saved:")
        for p in files:
            print("   -", p)
    else:
        print("[i] No media saved for this post.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
