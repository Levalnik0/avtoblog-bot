"""Шаблоны постов — ровно как в канале АВТОБЛОГ. Нейросеть только достаёт факты, пост собирается здесь.
Хочешь поменять формат (эмодзи, контакты, ссылки внизу) — меняй тексты в этом файле."""
import re

PHONE = "+79375900818"
TELEGRAM = "@sadykovrd"
DEFAULT_NAMES = "Ренат"

FOOTER = """Все актуальные авто в продаже вы можете посмотреть здесь⬇️

Авто ДО 2 млн🍋
Авто ОТ 2 млн🍋"""

STATUS_LINE = {
    "sale": "В продаже ✅",
    "order": "ПОД ЗАКАЗ ✅",
    "price_drop": "Снижение цены 🔥",
    "sold": "ПРОДАНО 🤝",
}


def price(value: str, from_: bool = False) -> str:
    """'1780000' / '1 780 000' / '1.78 млн' → '1.780.000 ₽'."""
    if not value:
        return ""
    v = value.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(млн|м\b)", v)
    if m:
        num = int(round(float(m.group(1)) * 1_000_000))
    else:
        digits = re.sub(r"\D", "", v)
        if not digits:
            return value
        num = int(digits)
        if num < 10_000:  # «2180» — скорее всего тысячи
            num *= 1000
    return ("от " if from_ else "") + f"{num:,}".replace(",", ".") + " ₽"


def mileage(value: str) -> str:
    """'128 тыс' / '128000' → '128 000 км'."""
    if not value:
        return ""
    v = value.lower()
    digits = re.sub(r"[^\d]", "", v.split("км")[0])
    if not digits:
        return value
    num = int(digits)
    if "тыс" in v or num < 1000:
        num *= 1000
    return f"{num:,}".replace(",", " ") + " км"


def contacts(names: str) -> str:
    return f"Контакты для связи ☎️:\n{PHONE} {names or DEFAULT_NAMES}\nТелеграм: {TELEGRAM}"


def render_post(c) -> str:
    """c — ai.CarData."""
    title = " ".join(x for x in (c.title, c.engine) if x).strip()
    head = (["ХИТ ПРОДАЖ 🔥", ""] if c.hit else []) + [STATUS_LINE.get(c.kind, STATUS_LINE["sale"]), ""]

    if c.kind == "order":  # новые авто под заказ из Кореи/Китая
        body = [title, c.year, c.powertrain]
        lines = head + [x for x in body if x]
        if c.price:
            lines += ["", f"Цена «под ключ» в Казань 🔑: {price(c.price, c.price_from)}"]
        return "\n".join(lines)

    body = [title, c.year, c.owners, mileage(c.mileage)]
    lines = head + [x for x in body if x]
    if c.advantages:
        lines += [""] + [f"{a.strip().rstrip('✅').strip()} ✅" for a in c.advantages]
    lines += ["", contacts(c.contact_names)]
    if c.price:
        label = "Новая цена" if c.kind == "price_drop" else "Цена"
        lines += ["", f"{label}: {price(c.price, c.price_from)}"]
    if c.kind != "sold":
        lines += ["", FOOTER]
    return "\n".join(lines)


def render_video_post(c) -> str:
    """Короткий пост к видео: «В продаже ✅ / KIA K5 / 2017 год 🔥 / ➡️ Смотреть объявление ! (Жми)»."""
    year = re.search(r"\d{4}\s*год", c.year or "")
    title = " / ".join(x for x in (c.title.upper(), year.group(0) if year else "") if x)
    return f"{STATUS_LINE.get(c.kind, STATUS_LINE['sale'])}\n\n{title} 🔥\n\n➡️ Смотреть объявление ! (Жми)"


def _drop_price(value: str) -> str:
    return price(value).replace(" ₽", "₽")


def render_price_drop(c) -> str:
    """Пост о снижении цены — как в канале. Публикуется ОТВЕТОМ на исходное объявление, с картинкой.
    Разметка HTML: жирный заголовок и зачёркнутая старая цена."""
    old = f"<s>{_drop_price(c.old_price)}</s>" if c.old_price else "<s>[старая цена]</s>"
    return f"<b>Снижение цены!</b>🔥\n\n{old} ➡️ {_drop_price(c.price)}\n\n➡️ Смотреть объявление ! (Жми)"


def price_edit_line(c) -> str:
    """Строка, на которую меняется цена в исходном объявлении."""
    old = _drop_price(c.old_price) if c.old_price else "[старая цена]"
    return f"Цена: {old} ➡️ {_drop_price(c.price)}"


def render_complectation(c) -> str:
    """Отдельное сообщение для комментариев под постом."""
    head = f"Комплектация {c.title}".strip() + " ⚙️"
    return head + "\n\n" + "\n".join(f"▪️ {x.strip()}" for x in c.complectation)


def cover_lines(c) -> list[str]:
    first = " · ".join(x for x in (c.year.replace(" год", ""), c.owners) if x)
    second = mileage(c.mileage) or c.powertrain
    extra = " ✓ ".join(a for a in c.advantages[:2]) + (" ✓" if c.advantages else "")
    return [x for x in (first, second, extra) if x][:3]


def story_lines(c) -> tuple[list[str], list[str]]:
    """Текст для картинки-сторис: (верхняя плашка, нижняя плашка) — как в сторис канала."""
    year = re.search(r"\d{4}\s*год", c.year or "")
    name = [x for x in (c.title, year.group(0) if year else "") if x]
    p = price(c.price, c.price_from)
    if c.kind == "price_drop":
        return ["Снижение цены 🔥"] + ([p.replace(" ₽", "₽")] if p else []), name
    status = {"order": "Под заказ ✅", "sold": "Продано 🤝"}.get(c.kind, "В продаже ✅")
    return [status] + name, ([p.replace("₽", "руб")] if p else [])
