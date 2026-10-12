"""Генерация текстов. Работает с Gemini (Google), ChatGPT (OpenAI) или DeepSeek —
выбирается в .env строкой AI_PROVIDER. Все они понимают один и тот же формат запросов."""
import asyncio
import base64
import json
import logging
import os
import re
import typing

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

import prompts

PROVIDERS = {
    # base_url, модель по умолчанию, понимает ли фото
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.6-flash", True),
    "openai": (None, "", True),
    "deepseek": ("https://api.deepseek.com", "", False),
    # Qwen (Alibaba), международный регион Singapore. Видит ли модель фото — проверяется по ключу, см. BACKUP_VISION
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen-plus", False),
}

PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
BASE_URL, DEFAULT_MODEL, VISION = PROVIDERS[PROVIDER]
MODEL = os.getenv("AI_MODEL") or DEFAULT_MODEL
# запасные модели через запятую — если основная перегружена (503) или кончился бесплатный лимит (429)
FALLBACKS = [m.strip() for m in (os.getenv("AI_FALLBACK_MODEL")
             or ("gemini-3.5-flash,gemini-3.5-flash-lite" if PROVIDER == "gemini" else "")).split(",") if m.strip()]
if os.getenv("AI_VISION"):
    VISION = os.getenv("AI_VISION") == "1"
if not MODEL:
    raise RuntimeError(f"Для {PROVIDER} укажи модель в .env: AI_MODEL=...")

client = AsyncOpenAI(api_key=os.environ["AI_API_KEY"], base_url=BASE_URL)

# Запасная нейросеть: если основная отказала (страна, лимит, перегрузка) — бот сам переходит на неё.
# Включается, когда в .env заполнен BACKUP_API_KEY.
BACKUP_PROVIDER = os.getenv("BACKUP_PROVIDER", "qwen").lower()
backup_client, BACKUP_MODEL, BACKUP_VISION = None, "", False
if os.getenv("BACKUP_API_KEY"):
    _url, _model, BACKUP_VISION = PROVIDERS[BACKUP_PROVIDER]
    if BACKUP_PROVIDER == "qwen" and os.getenv("QWEN_WORKSPACE_ID"):
        _url = f"https://{os.environ['QWEN_WORKSPACE_ID']}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    _url = os.getenv("BACKUP_BASE_URL") or _url
    BACKUP_MODEL = os.getenv("BACKUP_MODEL") or _model
    if os.getenv("BACKUP_VISION"):
        BACKUP_VISION = os.getenv("BACKUP_VISION") == "1"
    backup_client = AsyncOpenAI(api_key=os.environ["BACKUP_API_KEY"], base_url=_url)


class Pack(BaseModel):
    """Если модель вернула список одной строкой («1. …\n2. …») — разбиваем на пункты."""

    @model_validator(mode="before")
    @classmethod
    def _split_lists(cls, data):
        if isinstance(data, dict):
            for name, f in cls.model_fields.items():
                if typing.get_origin(f.annotation) is list and isinstance(data.get(name), str):
                    parts = [re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", p).strip() for p in data[name].split("\n")]
                    data[name] = [p for p in parts if p]
        return data


class CarData(Pack):
    kind: str = Field(description='"sale" / "order" / "price_drop" / "sold"')
    title: str = Field(description="Марка и модель латиницей: 'Kia K5'")
    engine: str = Field(description="Двигатель/коробка/привод: '2.0 АТ', пусто если нет")
    year: str = Field(description="'2017 год', пусто если нет")
    owners: str = Field(description="'1 владелец', пусто если нет")
    mileage: str = Field(description="Пробег как во вводных, пусто если нет")
    powertrain: str = Field(description="Только для order: 'Электромобиль' / 'Двигатель: Гибрид'")
    advantages: list[str] = Field(description="Преимущества дословно из вводных: 'Без вложений', 'Родной окрас'")
    complectation: list[str] = Field(description="Опции комплектации, каждая отдельно; пусто если нет")
    price: str = Field(description="Цена как во вводных, пусто если нет")
    price_from: bool = Field(description="true, если цена «от»")
    old_price: str = Field(default="", description="Старая цена при снижении («было»), иначе пусто")
    contact_names: str = Field(description="Другие имена для контактов, иначе пусто")
    hit: bool = Field(description="true, если «хит продаж»")
    missing: list[str] = Field(description="Чего критично не хватает: цена, год, пробег")

    def facts(self) -> str:
        return "\n".join(f"{k}: {v}" for k, v in self.model_dump(exclude={"missing", "contact_names"}).items() if v)


class InstaPack(Pack):
    caption: str = Field(description="Подпись для Instagram Reels с призывом и хэштегами")
    hooks: list[str] = Field(description="3 хука на первые 2 секунды, до 6 слов")


class Poll(Pack):
    question: str = Field(description="Вопрос до 100 символов")
    options: list[str] = Field(description="2–5 вариантов, каждый до 40 символов")
    note: str = Field(description="Зачем этот опрос или правильный ответ для «угадай цену»; можно пусто")


class Polls(Pack):
    polls: list[Poll] = Field(description="6 опросов")


class Ideas(Pack):
    ideas_text: str = Field(description="Контент-план на неделю, оформлен для Telegram HTML")


class Review(Pack):
    telegram_post: str
    instagram_caption: str
    reels_hooks: list[str]


class FixedWords(Pack):
    words: list[str] = Field(description="Исправленные слова, ровно столько же, сколько во входе")


class VideoHook(Pack):
    hook: str = Field(description="Текст на первые 2–3 секунды ролика, до 6 слов, интрига/вопрос")
    caption: str = Field(description="Подпись к Reels с призывом и хэштегами")


def _fields_hint(schema: type[BaseModel]) -> dict:
    fields = {}
    for name, f in schema.model_fields.items():
        args = typing.get_args(f.annotation)
        if typing.get_origin(f.annotation) is list and args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            fields[name] = [_fields_hint(args[0])]  # массив объектов
        elif typing.get_origin(f.annotation) is list:
            fields[name] = f"МАССИВ строк [\"…\", \"…\"]. {f.description or ''}".strip()
        elif f.annotation is bool:
            fields[name] = f"true/false. {f.description or ''}".strip()
        else:
            fields[name] = f"строка. {f.description or ''}".strip()
    return fields


def _schema_hint(schema: type[BaseModel]) -> str:
    return ("\n\nОтветь ТОЛЬКО JSON-объектом с полями (все обязательны):\n"
            + json.dumps(_fields_hint(schema), ensure_ascii=False, indent=1))


async def _parse(content, schema: type[BaseModel], max_tokens=8000):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    content = content[:-1] + [{"type": "text", "text": content[-1]["text"] + _schema_hint(schema)}]
    messages = [{"role": "system", "content": prompts.BRAND}, {"role": "user", "content": content}]

    for attempt in range(2):  # если модель ошиблась с форматом — просим ещё раз
        resp = await _create(messages, max_tokens)
        text = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(text.strip().removeprefix("```json").removesuffix("```"))
        except ValidationError as e:
            logging.warning("Ответ не по формату (%s), попытка %s", e.error_count(), attempt + 1)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "Формат неверный. Верни только корректный JSON со всеми полями."}]
    raise RuntimeError("Нейросеть вернула ответ не в том формате, попробуй ещё раз")


def _without_images(messages):
    """Убирает картинки из запроса — для запасной модели, которая их не понимает."""
    out = []
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            c = [p for p in c if p.get("type") != "image_url"]
        out.append({**m, "content": c})
    return out


async def _create(messages, max_tokens):
    """Сначала основная нейросеть и её запасные модели; если отказала совсем — запасная нейросеть."""
    attempts = [(client, m, messages) for m in [MODEL] + [m for m in FALLBACKS if m != MODEL]]
    if backup_client:
        attempts.append((backup_client, BACKUP_MODEL, messages if BACKUP_VISION else _without_images(messages)))
    last = None
    for cl, model, msgs in attempts:
        try:
            return await cl.chat.completions.create(
                model=model, messages=msgs,
                response_format={"type": "json_object"}, max_completion_tokens=max_tokens)
        except (APIStatusError, APIConnectionError) as e:
            last = e
        code = getattr(last, "status_code", "сеть")
        logging.warning("%s недоступна (%s): %s", model, code, str(last)[:120])
        # отказ по стране/ключу/доступу — остальные модели этой же нейросети пробовать бессмысленно
        if cl is client and (code in (400, 401, 403) or "location" in str(last).lower()):
            if not backup_client:
                break
            attempts = [a for a in attempts if a[0] is not client]
            return await _create_from(attempts, max_tokens, last)
        await asyncio.sleep(1)
    raise RuntimeError(_friendly(last)) from last


async def _create_from(attempts, max_tokens, last):
    for cl, model, msgs in attempts:
        try:
            return await cl.chat.completions.create(
                model=model, messages=msgs,
                response_format={"type": "json_object"}, max_completion_tokens=max_tokens)
        except (APIStatusError, APIConnectionError) as e:
            last = e
            logging.warning("%s недоступна: %s", model, str(e)[:120])
    raise RuntimeError(_friendly(last)) from last


def _friendly(err) -> str:
    text = str(err).lower()
    if "location is not supported" in text:
        return ("Gemini не принимает запросы с этого интернет-адреса (страна не поддерживается). "
                "Запасная нейросеть не подключена — добавь BACKUP_API_KEY в .env")
    if getattr(err, "status_code", None) == 429:
        return "Закончился бесплатный лимит нейросети на сегодня, попробуй позже"
    return "Нейросеть сейчас недоступна, попробуй через пару минут"


def _images(photos: list[bytes]):
    if not VISION:
        return []
    return [{"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + base64.standard_b64encode(p).decode()}}
            for p in photos[:5]]


async def extract_car(info: str, photos: list[bytes] | None = None, extra: str = "") -> CarData:
    """Достаёт факты о машине — пост потом собирается по шаблону канала (templates.py)."""
    text = prompts.EXTRACT_TASK.format(info=info)
    if photos and VISION:
        text += "\n\nПриложены изображения — бери с них только то, что явно написано или видно."
    if extra:
        text += f"\n\n{extra}"
    return await _parse(_images(photos or []) + [{"type": "text", "text": text}], CarData)


async def make_instagram(facts: str) -> InstaPack:
    return await _parse(prompts.INSTAGRAM_TASK.format(info=facts), InstaPack)


async def make_polls(cars: list[str], extra: str = "") -> list[Poll]:
    text = prompts.POLLS_TASK.format(cars="\n".join(cars) or "нет данных", extra=extra)
    return (await _parse(text, Polls)).polls


async def make_ideas(cars: list[str], extra: str = "") -> str:
    text = prompts.IDEAS_TASK.format(cars="\n".join(cars) or "нет данных", extra=extra)
    return (await _parse(text, Ideas, max_tokens=12000)).ideas_text


async def make_review(info: str) -> Review:
    return await _parse(prompts.REVIEW_TASK.format(info=info), Review)


async def fix_words(words: list[str]) -> list[str]:
    """Исправляет ошибки распознавания (марки, модели, цифры), сохраняя число слов для таймингов."""
    text = ("Это слова из автоматических субтитров ролика автоблога. Исправь ошибки распознавания: "
            "названия марок и моделей латиницей (Kia K5, BMW X5, Changan), цифры, опечатки. "
            "Не добавляй и не удаляй слова — верни ровно столько же слов в том же порядке.\n\n"
            + json.dumps(words, ensure_ascii=False))
    fixed = (await _parse(text, FixedWords, max_tokens=8000)).words
    return fixed if len(fixed) == len(words) else words


async def make_video_hook(transcript: str, frames: list[bytes] | None = None) -> VideoHook:
    text = ("Придумай цепляющий текст на первые секунды ролика (как «РАЗВОД С ТАЧКОЙ?», «Дед смотал пробег?») "
            "и подпись для Reels. Не выдумывай фактов: пробег, год, цену бери только из речи.\n\n"
            + ("Расшифровка речи:\n" + transcript if transcript else "В ролике нет речи — только видеоряд.")
            + ("\n\nПриложены кадры из ролика по порядку — опиши в подписи то, что реально видно "
               "(модель, цвет, салон, детали)." if frames and VISION else ""))
    return await _parse(_images(frames or []) + [{"type": "text", "text": text}], VideoHook)


SCREENSHOT_NOTE = ("На изображениях — скриншоты объявления (Авито, Авто.ру или другой сайт). "
                   "Возьми из них марку, модель, год, пробег, владельцев, цену и описание. "
                   "Интерфейс сайта и чужие контакты не бери.")
LINK_NOTE = ("Ниже данные со страницы по ссылке. Возьми из них характеристики и цену; "
             "чужие контакты, телефоны и название чужого салона не бери.")
