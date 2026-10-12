"""Telegram-бот АВТОБЛОГ: посты, обложки, автомонтаж видео с субтитрами, идеи."""
import asyncio
import html
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from aiogram import Bot, Dispatcher, F  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.filters import Command, CommandStart  # noqa: E402
from aiogram.types import (BufferedInputFile, CallbackQuery, FSInputFile, KeyboardButton,  # noqa: E402
                           Message, ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder  # noqa: E402

import ai  # noqa: E402
import cover  # noqa: E402
import templates  # noqa: E402
import video  # noqa: E402
import web  # noqa: E402

logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).parent
OUT = ROOT / "готово"
OUT.mkdir(exist_ok=True)
HISTORY = ROOT / "история_авто.json"
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
CHANNEL = os.getenv("CHANNEL_ID", "")  # напр. @AVTOBLOGRF — бот должен быть админом канала
TG_LIMIT = 20 * 1024 * 1024  # бот может скачать файл не больше 20 МБ

bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"], default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# состояние каждого пользователя: последний пост, фото, режим
state: dict[int, dict] = {}
albums: dict[str, list[Message]] = {}

MENU = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="🚗 Пост про авто"), KeyboardButton(text="🔻 Снижение цены")],
    [KeyboardButton(text="🎬 Монтаж видео")],
    [KeyboardButton(text="⭐ Отзыв"), KeyboardButton(text="💡 Идеи"), KeyboardButton(text="📊 Опросы")],
])

HELP = """<b>Что я умею</b>

🚗 <b>Пост про авто</b> — пришли инфу о машине текстом, голосовым или фото с подписью
(можно альбом до 10 фото). Можно просто ссылку на объявление или <b>скриншот объявления</b>
с Авито / Авто.ру — данные возьму оттуда. Верну:
• пост строго по шаблону канала («В продаже ✅ …»)
• отдельным сообщением — комплектацию для комментариев (если она есть)
• картинку для сторис (три фото + текст, как в сторис канала) и обложки 4:5 и 9:16 — если были фото
Подпись для Instagram и хуки для Reels — по кнопке под постом.

🎬 <b>Монтаж видео</b> — пришли видео (или несколько одним альбомом). Сделаю:
• вертикальный формат 9:16 (горизонтальное — с размытым фоном)
• склейку клипов и вырезание пауз
• субтитры с подсветкой слов, исправлю названия машин
• крупный хук в первые 3 секунды
• подпись к Reels — смотрю и речь, и кадры, так что работает и для роликов без слов
В подписи к видео можно написать свой хук. Напишешь «-» — хука не будет.

🔻 <b>Снижение цены</b> — нажми кнопку, потом пришли машину, старую и новую цену (лучше с 3 фото).
Верну пост «Снижение цены!🔥» с зачёркнутой старой ценой (публикуется ответом на объявление),
новую строку цены для самого объявления и картинку.

⭐ <b>Отзыв</b> — нажми кнопку, потом пришли инфу о клиенте и машине.
💡 <b>Идеи</b> — контент-план на неделю с учётом машин, которые ты присылала.
📊 <b>Опросы</b> — идеи опросов для Telegram-канала.

Авито и Авто.ру часто не пускают программы по ссылке — тогда пришли скриншот объявления.

Видео больше 20 МБ бот скачать не может — сожми или обрежь его, или смонтируй на Mac:
<code>.venv/bin/python video.py "ХУК" клип.mp4</code>"""


def allowed(m: Message | CallbackQuery) -> bool:
    return not ALLOWED or m.from_user.id in ALLOWED


def st(uid: int) -> dict:
    return state.setdefault(uid, {})


def remember_car(title: str, status: str):
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    hist.append({"car": title, "status": status, "date": datetime.now().strftime("%d.%m.%Y")})
    HISTORY.write_text(json.dumps(hist[-40:], ensure_ascii=False, indent=1))


def stamp(name: str) -> Path:
    return OUT / f"{datetime.now():%Y-%m-%d_%H-%M}_{name}"


async def send_html(m: Message, text: str, **kw):
    try:
        return await m.answer(text, **kw)
    except TelegramBadRequest:  # если нейросеть ошиблась в разметке — шлём без неё
        return await m.answer(text, parse_mode=None, **kw)


async def download(file_id: str, size: int | None, dst: Path) -> bool:
    if size and size > TG_LIMIT:
        return False
    await bot.download(file_id, destination=dst)
    return True


# ---------- команды ----------
@dp.message(CommandStart())
@dp.message(Command("help"))
async def start(m: Message):
    note = "" if ALLOWED else (f"\n\n⚠️ Бот открыт для всех. Впиши свой ID <code>{m.from_user.id}</code> "
                               f"в файл .env в строку ALLOWED_USERS, чтобы им пользовалась только ты.")
    await m.answer(HELP + note, reply_markup=MENU)


@dp.message(F.text == "🚗 Пост про авто")
async def mode_post(m: Message):
    st(m.chat.id)["mode"] = "post"
    await m.answer("Жду инфу о машине: текст, голосовое или фото с подписью.\n\n"
                   "Например: <i>Kia K5 2.0 AT, 2017, 1 владелец, 128 тыс, без вложений, 1 780 000</i>")


@dp.message(F.text == "🔻 Снижение цены")
async def mode_price_drop(m: Message):
    st(m.chat.id)["mode"] = "price_drop"
    await m.answer("Пришли машину, СТАРУЮ и НОВУЮ цену — текстом, голосовым или фото с подписью "
                   "(лучше 3 фото: сбоку-спереди, спереди, салон).\n\n"
                   "Например: <i>Genesis G70 2018, было 1 570 000, стало 1 550 000</i>")


@dp.message(F.text == "⭐ Отзыв")
async def mode_review(m: Message):
    st(m.chat.id)["mode"] = "review"
    await m.answer("Пришли инфу для отзыва: машина, кто клиент, что понравилось. Можно голосовым.")


@dp.message(F.text == "🎬 Монтаж видео")
async def mode_video(m: Message):
    await m.answer("Пришли видео (или несколько одним альбомом). В подписи можно написать хук — "
                   "иначе придумаю сама. «-» — без хука.")


def recent_cars() -> list[str]:
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    return [f"{h['date']}: {h['status']} {h['car']}" for h in hist[-15:]]


@dp.message(F.text == "📊 Опросы")
@dp.message(Command("polls"))
async def polls(m: Message):
    if not allowed(m):
        return
    wait = await m.answer("📊 Придумываю опросы…")
    items = await ai.make_polls(recent_cars())
    await wait.delete()
    st(m.chat.id)["polls"] = items
    for n, p in enumerate(items, 1):
        text = (f"📊 <b>Опрос {n}</b>\n\n{html.escape(p.question)}\n"
                + "\n".join(f"▫️ {html.escape(o)}" for o in p.options)
                + (f"\n\n<i>{html.escape(p.note)}</i>" if p.note else ""))
        kb = None
        if CHANNEL:
            b = InlineKeyboardBuilder()
            b.button(text="📤 Запустить в канале", callback_data=f"poll:{n - 1}")
            kb = b.as_markup()
        await m.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("poll:"))
async def poll_publish(c: CallbackQuery):
    items = st(c.message.chat.id).get("polls") or []
    i = int(c.data.split(":")[1])
    if i >= len(items):
        return await c.answer("Запроси опросы заново", show_alert=True)
    p = items[i]
    try:
        await bot.send_poll(CHANNEL, p.question[:300], [o[:100] for o in p.options[:10]], is_anonymous=True)
        await c.answer("Опрос в канале ✅", show_alert=True)
    except Exception as e:  # noqa: BLE001
        await c.answer(f"Не получилось: {e}"[:190], show_alert=True)


@dp.message(F.text == "💡 Идеи")
@dp.message(Command("ideas"))
async def ideas(m: Message):
    if not allowed(m):
        return
    wait = await m.answer("💡 Придумываю контент-план…")
    text = await ai.make_ideas(recent_cars())
    await wait.delete()
    for i in range(0, len(text), 4000):
        await send_html(m, text[i:i + 4000])


# ---------- пост про авто ----------
async def handle_car(m: Message, info: str, photos: list[bytes], extra: str = "", screenshot: bool = False):
    s = st(m.chat.id)
    if s.get("mode") == "review":
        s["mode"] = "post"
        wait = await m.answer("⭐ Пишу отзыв…")
        r = await ai.make_review(info)
        await wait.delete()
        await send_html(m, r.telegram_post)
        await m.answer("📸 <b>Instagram:</b>\n\n" + html.escape(r.instagram_caption))
        await m.answer("🎬 <b>Хуки для ролика:</b>\n" + "\n".join(f"• {html.escape(h)}" for h in r.reels_hooks))
        return

    drop = s.get("mode") == "price_drop"
    s["mode"] = "post"
    wait = await m.answer("✍️ Пишу пост…")
    note = ai.SCREENSHOT_NOTE if screenshot else ""
    if drop:
        note += " Это снижение цены: kind = price_drop, в price — НОВАЯ цена, в old_price — старая."
    car = await ai.extract_car(info, photos, note + extra)
    if drop:
        car.kind = "price_drop"
    await wait.delete()
    if car.kind == "price_drop":
        return await send_price_drop(m, car, photos, screenshot)
    post = templates.render_post(car)
    s.update(info=info, photos=photos, pack=car, post=post, screenshot=screenshot, insta=None)
    remember_car(car.title, templates.STATUS_LINE.get(car.kind, ""))

    kb = InlineKeyboardBuilder()
    kb.button(text="📸 Подпись для Instagram", callback_data="insta")
    kb.button(text="🎬 Хуки для Reels", callback_data="hooks")
    if CHANNEL:
        kb.button(text="📤 Опубликовать в канал", callback_data="publish")
    kb.adjust(2)
    await m.answer(post, parse_mode=None, reply_markup=kb.as_markup())
    if car.complectation:
        await m.answer(templates.render_complectation(car), parse_mode=None)
    if car.kind != "sold":  # короткий пост к видео; на последнюю строку ставится ссылка на объявление
        await m.answer(templates.render_video_post(car), parse_mode=None)
    if car.missing:
        await m.answer("⚠️ Не хватает: " + html.escape(", ".join(car.missing)) + ". Допиши и пришли заново.")
    if photos and not screenshot:  # на скриншоте сайта обложку не сделать
        await send_story(m, photos, car)
        await send_covers(m, photos[0], car)
    elif screenshot:
        await m.answer("🖼 Для обложки пришли фото машины с подписью «обложка».")


async def send_price_drop(m: Message, car, photos: list[bytes], screenshot: bool):
    """Снижение цены, как в канале: пост-ответ на объявление + новая строка цены для самого объявления."""
    s = st(m.chat.id)
    post = templates.render_price_drop(car)
    s.update(pack=car, post=post, photos=photos, screenshot=screenshot, insta=None)
    remember_car(car.title, "Снижение цены")
    await m.answer(post)  # HTML: жирный заголовок и зачёркнутая старая цена сохранятся при пересылке
    await m.answer("☝️ Опубликуй это <b>ответом на объявление</b> с картинкой ниже, на «Смотреть объявление» "
                   "поставь ссылку.\n\nА в самом объявлении замени строку цены на:")
    await m.answer(templates.price_edit_line(car), parse_mode=None)
    if not car.old_price:
        await m.answer("⚠️ Не вижу старую цену — допиши её вместо [старая цена] или пришли заново: «было …, стало …».")
    if photos and not screenshot:
        await send_story(m, photos, car)
    else:
        await m.answer("🖼 Для картинки «Снижение цены 🔥» пришли 1–3 фото машины с подписью «обложка».")


COVER_STATUS = {"sale": "В продаже", "order": "Под заказ", "price_drop": "Снижение цены", "sold": "Продано"}


async def send_covers(m: Message, photo: bytes, car):
    price = templates.price(car.price, car.price_from)
    for fmt, label in (("post", "Обложка 4:5 — лента / Telegram"), ("reels", "Обложка 9:16 — Reels / сторис")):
        img = await asyncio.to_thread(cover.make_cover, photo, COVER_STATUS.get(car.kind, "В продаже"),
                                      car.title, templates.cover_lines(car), price, fmt)
        path = stamp(f"{car.title}_{fmt}.jpg".replace(" ", "_"))
        path.write_bytes(img)
        await m.answer_document(BufferedInputFile(img, path.name), caption=label)


async def send_story(m: Message, photos: list[bytes], car):
    """Картинка для сторис/Instagram: до трёх фото друг под другом + плашки с текстом."""
    top, bottom = templates.story_lines(car)
    img = await asyncio.to_thread(cover.make_story, photos, top, bottom)
    path = stamp(f"{car.title}_story.jpg".replace(" ", "_"))
    path.write_bytes(img)
    await m.answer_document(BufferedInputFile(img, path.name), caption="Сторис 9:16 — три фото с текстом")


@dp.callback_query(F.data.in_({"insta", "hooks"}))
async def insta(c: CallbackQuery):
    s = st(c.message.chat.id)
    car = s.get("pack")
    if not car:
        return await c.answer("Пришли инфу о машине заново", show_alert=True)
    await c.answer("Пишу…")
    if not s.get("insta"):
        s["insta"] = await ai.make_instagram(car.facts())
    ip = s["insta"]
    if c.data == "insta":
        await c.message.answer(ip.caption, parse_mode=None)
    else:
        await c.message.answer("🎬 Хуки для Reels:\n" + "\n".join(f"• {h}" for h in ip.hooks), parse_mode=None)


@dp.callback_query(F.data == "publish")
async def publish(c: CallbackQuery):
    s = st(c.message.chat.id)
    post = s.get("post")
    if not post or not CHANNEL:
        return await c.answer("Нечего публиковать", show_alert=True)
    photos = [] if s.get("screenshot") else (s.get("photos") or [])
    try:
        if photos and len(post) <= 1024:
            await bot.send_photo(CHANNEL, BufferedInputFile(photos[0], "car.jpg"), caption=post, parse_mode=None)
        else:
            await bot.send_message(CHANNEL, post, parse_mode=None)
        await c.answer("Опубликовано ✅", show_alert=True)
    except Exception as e:  # noqa: BLE001
        await c.answer(f"Не получилось: {e}"[:190], show_alert=True)


# ---------- видео ----------
async def handle_videos(m: Message, msgs: list[Message]):
    hook = next((x.caption for x in msgs if x.caption), None)
    wait = await m.answer("🎬 Скачиваю видео…")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clips = []
        for i, x in enumerate(msgs):
            f = x.video or x.document
            dst = tmp / f"in{i}.mp4"
            if not await download(f.file_id, f.file_size, dst):
                await wait.edit_text("❌ Видео больше 20 МБ — Telegram не даёт боту его скачать. "
                                     "Сожми/обрежь его или смонтируй на Mac командой из /help.")
                return
            clips.append(str(dst))

        await wait.edit_text("✂️ Формат 9:16, склейка, вырезаю паузы…")
        base = await asyncio.to_thread(video.prepare, clips, tmp)

        await wait.edit_text("🗣 Распознаю речь для субтитров…")
        words = await asyncio.to_thread(video.transcribe, base)
        if words:
            fixed = await ai.fix_words([w.text for w in words])
            for w, t in zip(words, fixed):
                w.text = t
        transcript = " ".join(w.text for w in words)

        caption = ""
        if hook is None or hook.strip() != "-":
            await wait.edit_text("👀 Смотрю кадры и придумываю подпись…")
            shots = await asyncio.to_thread(video.frames, base)
            h = await ai.make_video_hook(transcript, shots)
            caption = h.caption
            if hook is None:
                hook = h.hook
        if hook and hook.strip() == "-":
            hook = ""

        await wait.edit_text("🎨 Накладываю субтитры и хук…")
        out = stamp("reels.mp4")
        await asyncio.to_thread(video.render, base, words, hook or "", str(out), tmp)

    await wait.delete()
    size = out.stat().st_size
    info = f"🎬 Готово! Хук: <b>{html.escape(hook or 'без хука')}</b>"
    if size < 49 * 1024 * 1024:
        await m.answer_video(FSInputFile(out), caption=info, supports_streaming=True, width=1080, height=1920)
    else:
        await m.answer(info + f"\n\nФайл большой для Telegram, лежит на Mac:\n<code>{out}</code>")
    if caption:
        await m.answer("📸 <b>Подпись для Reels:</b>\n\n" + html.escape(caption))
    if transcript:
        await m.answer("📝 <b>Текст ролика:</b>\n" + html.escape(transcript[:3500]))


# ---------- приём сообщений ----------
async def collect_album(m: Message, handler):
    """Альбом приходит отдельными сообщениями — собираем их и обрабатываем вместе."""
    key = m.media_group_id
    if key in albums:
        albums[key].append(m)
        return
    albums[key] = [m]
    await asyncio.sleep(2)
    await handler(m, albums.pop(key))


async def photos_handler(m: Message, msgs: list[Message]):
    photos = []
    for x in sorted(msgs, key=lambda x: x.message_id):  # порядок фото — как в альбоме
        f = x.photo[-1] if x.photo else x.document
        if f:
            buf = await bot.download(f.file_id)
            photos.append(buf.read())
    info = "\n".join(x.caption for x in msgs if x.caption)
    s = st(m.chat.id)
    if info.strip().lower() == "обложка" and s.get("pack"):
        await send_story(m, photos, s["pack"])
        if s["pack"].kind == "price_drop":  # для снижения цены нужна только картинка-сторис
            return
        return await send_covers(m, photos[0], s["pack"])
    if not info:
        s["photos_wait"] = photos
        kb = InlineKeyboardBuilder()
        kb.button(text="📄 Это скриншот объявления", callback_data="shot")
        return await m.answer("📸 Фото получила. Пришли инфу о машине текстом или голосовым — "
                              "или нажми кнопку, если это скриншот объявления.", reply_markup=kb.as_markup())
    await handle_car(m, info, photos)


@dp.callback_query(F.data == "shot")
async def screenshot(c: CallbackQuery):
    photos = st(c.message.chat.id).pop("photos_wait", [])
    if not photos:
        return await c.answer("Пришли скриншот ещё раз", show_alert=True)
    await c.answer("Читаю объявление…")
    await handle_car(c.message, "Все данные — на скриншотах объявления.", photos, screenshot=True)


def is_video(m: Message) -> bool:
    return bool(m.video or (m.document and (m.document.mime_type or "").startswith("video/")))


@dp.message(F.photo | F.video | F.document)
async def media(m: Message):
    if not allowed(m):
        return
    if is_video(m):
        if m.media_group_id:
            return await collect_album(m, handle_videos)
        return await handle_videos(m, [m])
    if m.photo or (m.document and (m.document.mime_type or "").startswith("image/")):
        if m.media_group_id:
            return await collect_album(m, photos_handler)
        return await photos_handler(m, [m])


@dp.message(F.voice | F.audio)
async def voice(m: Message):
    if not allowed(m):
        return
    f = m.voice or m.audio
    wait = await m.answer("🎧 Слушаю голосовое…")
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / "voice.ogg"
        if not await download(f.file_id, f.file_size, dst):
            return await wait.edit_text("❌ Файл больше 20 МБ")
        text = await asyncio.to_thread(video.transcribe_text, dst)
    await wait.edit_text("🎧 Расслышала: <i>" + html.escape(text) + "</i>")
    await handle_car(m, text, st(m.chat.id).pop("photos_wait", []))


@dp.message(F.text)
async def text(m: Message):
    if not allowed(m):
        return
    photos = st(m.chat.id).pop("photos_wait", [])
    urls = web.find_urls(m.text)
    if not urls:
        return await handle_car(m, m.text, photos)

    wait = await m.answer("🔗 Открываю ссылку…")
    try:
        page, page_photos = await web.fetch_listing(urls[0])
    except web.Blocked as e:
        rest = m.text.replace(urls[0], "").strip()
        await wait.edit_text(f"🚫 Не смогла открыть ссылку: {html.escape(str(e))}. Авито и Авто.ру часто так делают.\n\n"
                             "Пришли <b>скриншот объявления</b> (можно 2–3, чтобы влезли характеристики и цена) "
                             "или скопируй текст объявления.")
        if rest and len(rest) > 30:  # кроме ссылки есть данные — делаем пост по ним
            await handle_car(m, rest, photos)
        return
    await wait.delete()
    info = f"{m.text}\n\n{ai.LINK_NOTE}\n{page}"
    await handle_car(m, info, photos + page_photos)


@dp.errors()
async def on_error(event):
    logging.exception(event.exception)
    upd = event.update
    msg = upd.message or (upd.callback_query.message if upd.callback_query else None)
    if msg:
        await msg.answer(f"❌ Ошибка: {html.escape(str(event.exception))[:500]}")
    return True


async def main():
    logging.info("Бот запущен. Остановить: Ctrl+C")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
