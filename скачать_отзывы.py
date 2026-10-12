"""Скачивает все видео-отзывы из Telegram-канала в папку «Отзывы».

Источники: пост-оглавление «Отзывы наших клиентов» (t.me/AVTOBLOGRF/4204) и посты с тегом #отзыв.
Запуск:  .venv/bin/python скачать_отзывы.py
При первом запуске Telegram попросит номер телефона и код — вводишь сама в окне терминала.
Можно прервать и запустить снова: уже скачанные файлы пропускаются.
"""
import asyncio
import csv
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

CHANNEL = "AVTOBLOGRF"
HUB_POST = 4204
OUT = ROOT / "Отзывы"
LINK = re.compile(rf"t\.me/{CHANNEL}/(\d+)", re.I)


def safe(name: str) -> str:
    name = re.sub(r"[^\w\s.,!-]", "", name, flags=re.U).strip()
    return re.sub(r"\s+", " ", name)[:60] or "Отзыв"


def max_caption(caption: str, title: str) -> str:
    """Подпись для Max: убираем телеграмные ссылки «Смотреть все отзывы», оставляем суть."""
    keep = []
    for line in caption.splitlines():
        low = line.lower()
        if any(w in low for w in ("все отзывы", "смотреть все", "#отзыв", "t.me/")):
            continue
        keep.append(line)
    text = "\n".join(keep).strip() or title
    return text + "\n\n#отзыв #автоблог #казань"


def hub_links(msg) -> dict[int, str]:
    """Ссылки из поста-оглавления: {id поста: название отзыва}."""
    found = {}
    for ent, text in msg.get_entities_text():
        url = ent.url if isinstance(ent, MessageEntityTextUrl) else text if isinstance(ent, MessageEntityUrl) else ""
        m = LINK.search(url or "")
        if m:
            found[int(m.group(1))] = text.strip(" 🔵")
    return found


async def main():
    OUT.mkdir(exist_ok=True)
    client = TelegramClient(str(ROOT / "tg_session"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.start()
    channel = await client.get_entity(CHANNEL)

    titles = hub_links(await client.get_messages(channel, ids=HUB_POST))
    async for m in client.iter_messages(channel, search="#отзыв"):
        titles.setdefault(m.id, "")
    print(f"Нашла постов с отзывами: {len(titles)}")

    msgs = [m for m in await client.get_messages(channel, ids=sorted(titles)) if m]
    msgs.sort(key=lambda m: m.date)

    rows = []
    for n, m in enumerate(msgs, 1):
        caption = (m.message or "").strip()
        title = titles.get(m.id) or caption.split("\n")[0] or "Отзыв"
        # видео может лежать в альбоме рядом с постом
        media = [m] if m.video else []
        if not media and m.grouped_id:
            near = await client.get_messages(channel, ids=list(range(m.id - 10, m.id + 11)))
            media = [x for x in near if x and x.grouped_id == m.grouped_id and x.video]
        if not media:
            print(f"  {n:02d}. {title} — видео нет, пропускаю")
            continue
        files = []
        for k, v in enumerate(media):
            suffix = f"_{k + 1}" if len(media) > 1 else ""
            path = OUT / f"{n:02d}_{m.date:%Y-%m-%d}_{safe(title)}{suffix}.mp4"
            if not path.exists() or path.stat().st_size < (v.file.size or 1):
                print(f"  {n:02d}. {title} — скачиваю {v.file.size / 1e6:.0f} МБ…")
                await client.download_media(v, file=str(path))
            files.append(path.name)
        rows.append({"№": n, "дата": f"{m.date:%d.%m.%Y}", "название": title,
                     "файлы": "; ".join(files), "ссылка_tg": f"https://t.me/{CHANNEL}/{m.id}", "подпись": caption})

    with open(OUT / "список_отзывов.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "подписи_для_Max.txt", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"===== {r['№']:02d} · файл: {r['файлы']} =====\n{max_caption(r['подпись'], r['название'])}\n\n")
    print(f"\nГотово: {len(rows)} отзывов в папке {OUT}")
    print("Подписи для Max — в файле подписи_для_Max.txt")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
