"""Генерация текстов через Claude API."""
import base64
import json

import anthropic
from pydantic import BaseModel, Field

import prompts

MODEL = "claude-opus-5"
client = anthropic.AsyncAnthropic()


class PostPack(BaseModel):
    status: str = Field(description="В продаже / ПОД ЗАКАЗ / Снижение цены / ПРОДАНО")
    car_title: str = Field(description="Марка и модель, коротко, напр. 'Kia K5'")
    telegram_post: str = Field(description="Готовый пост для Telegram с HTML-разметкой")
    instagram_caption: str = Field(description="Подпись для Instagram с хэштегами")
    reels_hooks: list[str] = Field(description="3 варианта текста на первые 2 секунды Reels, до 6 слов каждый")
    cover_title: str = Field(description="Крупный текст на обложку, 1–3 слова, напр. 'KIA K5'")
    cover_lines: list[str] = Field(description="2–3 короткие строки на обложку: год, пробег, фишка")
    cover_price: str = Field(description="Цена для обложки, напр. '1 780 000 ₽', пусто если нет")
    story_text: str = Field(description="Текст для сторис с опросом/стикером-вопросом")
    video_ideas: list[str] = Field(description="3 идеи коротких роликов именно про эту машину")
    missing: list[str] = Field(description="Каких важных данных не хватает во вводных")


class Ideas(BaseModel):
    ideas_text: str = Field(description="Контент-план на неделю, оформлен для Telegram HTML")


class Review(BaseModel):
    telegram_post: str
    instagram_caption: str
    reels_hooks: list[str]


async def _parse(content, schema, max_tokens=8000):
    resp = await client.beta.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        system=prompts.BRAND,
        messages=[{"role": "user", "content": content}],
        output_format=schema,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Нейросеть не смогла ответить, попробуй переформулировать")
    return resp.parsed_output


def _images(photos: list[bytes]):
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(p).decode()}}
        for p in photos[:5]
    ]


async def make_post(info: str, photos: list[bytes] | None = None, extra: str = "") -> PostPack:
    text = prompts.POST_TASK.format(contacts=prompts.CONTACTS, footer=prompts.FOOTER, info=info)
    if photos:
        text += "\n\nК вводным приложены фото машины — используй увиденное (цвет, салон, диски), но не придумывай техданные."
    if extra:
        text += f"\n\nДополнительная просьба: {extra}"
    return await _parse(_images(photos or []) + [{"type": "text", "text": text}], PostPack)


async def make_ideas(cars: list[str], extra: str = "") -> str:
    text = prompts.IDEAS_TASK.format(cars="\n".join(cars) or "нет данных", extra=extra)
    return (await _parse(text, Ideas, max_tokens=12000)).ideas_text


async def make_review(info: str) -> Review:
    return await _parse(prompts.REVIEW_TASK.format(info=info), Review)


class FixedWords(BaseModel):
    words: list[str] = Field(description="Исправленные слова, ровно столько же, сколько во входе")


class VideoHook(BaseModel):
    hook: str = Field(description="Текст на первые 2–3 секунды ролика, до 6 слов, интрига/вопрос")
    caption: str = Field(description="Подпись к Reels с призывом и хэштегами")


async def fix_words(words: list[str]) -> list[str]:
    """Исправляет ошибки распознавания (марки, модели, цифры), сохраняя число слов для таймингов."""
    text = ("Это слова из автоматических субтитров ролика автоблога. Исправь ошибки распознавания: "
            "названия марок и моделей латиницей (Kia K5, BMW X5, Changan), цифры, опечатки. "
            "Не добавляй и не удаляй слова — верни ровно столько же слов в том же порядке.\n\n"
            + json.dumps(words, ensure_ascii=False))
    fixed = (await _parse(text, FixedWords, max_tokens=8000)).words
    return fixed if len(fixed) == len(words) else words


async def make_video_hook(transcript: str) -> VideoHook:
    text = ("По расшифровке ролика придумай цепляющий текст на первые секунды (как «РАЗВОД С ТАЧКОЙ?», "
            "«Дед смотал пробег?») и подпись для Reels. Не выдумывай фактов.\n\nРасшифровка:\n" + transcript)
    return await _parse(text, VideoHook)
