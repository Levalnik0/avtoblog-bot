"""Чтение страниц по ссылке: текст объявления + фото. Капчи не обходим — если сайт не пускает, так и говорим."""
import html
import json
import re

import aiohttp

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
URL_RE = re.compile(r"https?://\S+")
BLOCK_MARKERS = ("captcha", "доступ ограничен", "проблема с ip", "подтвердите, что вы не робот", "are you a robot")


class Blocked(Exception):
    """Сайт не пустил программу (капча, блокировка, таймаут)."""


def find_urls(text: str) -> list[str]:
    return [u.rstrip(").,;»\"'") for u in URL_RE.findall(text or "")]


def _telegram_preview(url: str) -> str:
    # t.me/канал/123 → встраиваемая версия одного поста
    m = re.match(r"https?://t\.me/(?:s/)?([\w_]+)/(\d+)", url)
    return f"https://t.me/{m.group(1)}/{m.group(2)}?embed=1&mode=tme" if m else url


def _meta(page: str, key: str) -> list[str]:
    pat = rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)'
    pat2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']'
    return [html.unescape(x) for x in re.findall(pat, page, re.I) + re.findall(pat2, page, re.I)]


def _json_ld(page: str) -> list[str]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            out.append(json.dumps(json.loads(block), ensure_ascii=False)[:4000])
        except ValueError:
            pass
    return out


def _visible_text(page: str) -> str:
    page = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if len(l) > 1)


async def fetch_listing(url: str, max_images=4) -> tuple[str, list[bytes]]:
    """Возвращает (текст для нейросети, фото). Бросает Blocked, если страницу не открыть."""
    url = _telegram_preview(url)
    headers = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as s:
            async with s.get(url, allow_redirects=True) as r:
                page = (await r.content.read(3_000_000)).decode(r.charset or "utf-8", "ignore")
                status = r.status
            low = page.lower()
            if status in (401, 403, 429) or (status >= 400) or any(m in low for m in BLOCK_MARKERS):
                raise Blocked(f"сайт ответил {status}" + (" и показал капчу" if "captcha" in low else ""))

            title = _meta(page, "og:title") or re.findall(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            desc = _meta(page, "og:description") + _meta(page, "description")
            parts = [f"Ссылка: {url}"]
            if title:
                parts.append("Заголовок: " + html.unescape(title[0]).strip())
            if desc:
                parts.append("Описание: " + desc[0].strip())
            parts += [f"Структурированные данные: {j}" for j in _json_ld(page)[:3]]
            parts.append("Текст страницы:\n" + _visible_text(page)[:12000])

            images = []
            candidates = _meta(page, "og:image") + _meta(page, "twitter:image")
            if "t.me/" in url:  # у Telegram фото поста лежат в стилях, а og:image — аватарка канала
                candidates = re.findall(r"message_photo_wrap[^>]+background-image:url\('([^']+)'\)", page)
            for img_url in dict.fromkeys(candidates):
                if len(images) >= max_images:
                    break
                try:
                    async with s.get(img_url) as ir:
                        if ir.status == 200 and ir.content_type.startswith("image/"):
                            images.append(await ir.read())
                except aiohttp.ClientError:
                    continue
            return "\n".join(parts), images
    except (aiohttp.ClientError, TimeoutError) as e:
        raise Blocked(f"сайт не ответил ({type(e).__name__})") from e
