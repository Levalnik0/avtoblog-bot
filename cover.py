"""Обложки с текстом поверх фото машины (Pillow)."""
import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

SF = "/System/Library/Fonts/SFNS.ttf"  # системный шрифт macOS: есть кириллица, ₽ и ✓
BLACK = "Black"
BOLD = "Semibold"

LEMON = (255, 221, 0)
GREEN = (46, 204, 113)
RED = (231, 76, 60)
WHITE = (255, 255, 255)

SIZES = {"post": (1080, 1350), "reels": (1080, 1920)}


def _font(weight, size):
    f = ImageFont.truetype(SF, size)
    f.set_variation_by_name(weight)
    return f


def _fit(draw, text, weight, max_w, start):
    size = start
    while size > 30:
        f = _font(weight, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(weight, size)


def _pill(draw, xy, text, font, bg, fg=(0, 0, 0), pad=(28, 14)):
    x, y = xy
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    w, h = r - l + pad[0] * 2, b - t + pad[1] * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
    draw.text((x + pad[0] - l, y + pad[1] - t), text, font=font, fill=fg)
    return h


def _badge_color(status: str):
    s = status.lower()
    if "заказ" in s:
        return LEMON
    if "сниж" in s:
        return RED
    return GREEN


def _clean(text: str) -> str:
    """Эмодзи шрифт не умеет — заменяем галочки, остальное убираем."""
    text = text.replace("✅", "✓").replace("✔️", "✓").replace("✔", "✓")
    return "".join(ch for ch in text if ord(ch) < 0x2600 or ch in "✓₽").strip()


def make_cover(photo: bytes, status: str, title: str, lines: list[str], price: str, fmt="post") -> bytes:
    status, title, price = _clean(status), _clean(title), _clean(price)
    lines = [_clean(l) for l in lines]
    W, H = SIZES[fmt]
    img = ImageOps.fit(Image.open(io.BytesIO(photo)).convert("RGB"), (W, H), Image.LANCZOS)

    # затемнение снизу, чтобы текст читался на любом фото
    grad = Image.new("L", (1, H))
    for y in range(H):
        k = max(0, (y - H * 0.45) / (H * 0.55))
        grad.putpixel((0, y), int(235 * k ** 1.3))
    shade = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(shade, img, grad.resize((W, H)))
    top = Image.new("L", (1, H))
    for y in range(H):
        top.putpixel((0, y), int(150 * max(0, 1 - y / (H * 0.18))))
    img = Image.composite(shade, img, top.resize((W, H)))

    d = ImageDraw.Draw(img)
    m = 64
    d.text((m, 52), "АВТОБЛОГ · КАЗАНЬ", font=_font(BLACK, 34), fill=WHITE)

    y = H - 90
    if price:
        pf = _font(BLACK, 64)
        ph = d.textbbox((0, 0), price, font=pf)[3] + 30
        y -= ph
        _pill(d, (m, y), price, pf, LEMON, pad=(32, 16))
        y -= 34

    lf = _font(BOLD, 44)
    for line in reversed([l for l in lines if l][:3]):
        y -= 58
        d.text((m, y), line, font=lf, fill=WHITE)
    y -= 20

    tf = _fit(d, title.upper(), BLACK, W - 2 * m, 150)
    tb = d.textbbox((0, 0), title.upper(), font=tf)
    y -= tb[3]
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((m + 4, y + 6 - tb[1]), title.upper(), font=tf, fill=(0, 0, 0, 180))
    img.paste(shadow.filter(ImageFilter.GaussianBlur(6)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.text((m, y - tb[1]), title.upper(), font=tf, fill=WHITE)

    bf = _font(BLACK, 40)
    bh = d.textbbox((0, 0), status.upper(), font=bf)[3] + 28
    _pill(d, (m, y - bh - 24), status.upper(), bf, _badge_color(status))

    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


# ---------- Картинка для сторис: три фото друг под другом + белые плашки с текстом ----------
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
STORY = (1080, 1920)


def _is_emoji(ch: str) -> bool:
    return ord(ch) >= 0x2600 and ch not in "₽"


def _emoji_img(ch: str, size: int) -> Image.Image:
    f = ImageFont.truetype(EMOJI_FONT, 160)  # цветной шрифт Apple рисуется только в фиксированных размерах
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20, 20), ch, font=f, embedded_color=True)
    return im.crop(im.getbbox()).resize((size, size), Image.LANCZOS)


def _line_width(draw, text, font, size):
    return sum(size + 6 if _is_emoji(ch) else draw.textlength(ch, font=font) for ch in text if ch != "️")


def _label(img: Image.Image, lines: list[str], center_y: int, size=60):
    """Белые скруглённые плашки под каждой строкой, как текст в редакторе сторис."""
    d = ImageDraw.Draw(img)
    W = img.width
    while size > 36:
        font = _font("Semibold", size)
        widths = [_line_width(d, l, font, size) for l in lines]
        if max(widths) <= W - 160:
            break
        size -= 4
    lh = int(size * 1.22)
    pad_x, radius = int(size * 0.42), int(size * 0.3)
    top = center_y - lh * len(lines) // 2
    for i, w in enumerate(widths):  # сначала все плашки, потом текст — соседние плашки сливаются
        x0 = (W - w) / 2 - pad_x
        d.rounded_rectangle((x0, top + i * lh - 4, x0 + w + 2 * pad_x, top + (i + 1) * lh + 6), radius=radius, fill=WHITE)
    for i, (line, w) in enumerate(zip(lines, widths)):
        x, y = (W - w) / 2, top + i * lh
        for ch in line:
            if ch == "️":
                continue
            if _is_emoji(ch):
                em = _emoji_img(ch, size)
                img.paste(em, (int(x) + 3, y + int(size * 0.14)), em)
                x += size + 6
            else:
                d.text((x, y + int(size * 0.03)), ch, font=font, fill=(0, 0, 0))
                x += d.textlength(ch, font=font)


def make_story(photos: list[bytes], top_lines: list[str], bottom_lines: list[str]) -> bytes:
    """photos — 1–3 фото (лучше: вид сбоку-спереди, вид спереди, салон). Текст — на стыках фото."""
    W, H = STORY
    photos = photos[:3]
    n = len(photos)
    img = Image.new("RGB", (W, H), (0, 0, 0))
    edges = [round(H * i / n) for i in range(n + 1)]
    for i, p in enumerate(photos):
        part = ImageOps.exif_transpose(Image.open(io.BytesIO(p))).convert("RGB")
        img.paste(ImageOps.fit(part, (W, edges[i + 1] - edges[i]), Image.LANCZOS), (0, edges[i]))
    seams = edges[1:-1] if n > 1 else [int(H * 0.36), int(H * 0.66)]
    if len(seams) == 1:  # два фото: верхняя плашка на стыке, нижняя — в нижней трети
        seams = [seams[0], int(H * 0.8)]
    if top_lines:
        _label(img, top_lines, seams[0])
    if bottom_lines:
        _label(img, bottom_lines, seams[1])
    out = io.BytesIO()
    img.save(out, "JPEG", quality=93)
    return out.getvalue()
