"""Собирает текст закреплённого поста-оглавления для канала отзывов в Max.

1. Выложи отзывы в Max по порядку номеров.
2. У каждого поста нажми «Скопировать ссылку» и вставь ссылки в файл Отзывы/ссылки_max.txt —
   по одной на строку, в том же порядке (01, 02, 03…). Если ссылки нет — оставь строку пустой.
3. Запусти:  .venv/bin/python собрать_оглавление.py
Готовый текст появится в Отзывы/оглавление_для_Max.txt. Если он длинный, будет разбит на части.
"""
import csv
from pathlib import Path

DIR = Path(__file__).parent / "Отзывы"
LIMIT = 3800  # запас до лимита длины сообщения

rows = list(csv.DictReader(open(DIR / "список_отзывов.csv", encoding="utf-8-sig")))
links_file = DIR / "ссылки_max.txt"
links = links_file.read_text(encoding="utf-8").splitlines() if links_file.exists() else []

head = "Отзывы наших клиентов ✅\nКак мы работаем ⬇️\n\n"
lines = []
for i, r in enumerate(reversed(rows)):  # свежие отзывы сверху
    idx = len(rows) - 1 - i
    link = links[idx].strip() if idx < len(links) else ""
    lines.append(f"🔵 {r['название']}" + (f"\n{link}" if link else ""))

parts, cur = [], head
for line in lines:
    if len(cur) + len(line) + 2 > LIMIT:
        parts.append(cur.rstrip())
        cur = ""
    cur += line + "\n\n"
parts.append(cur.rstrip())

out = DIR / "оглавление_для_Max.txt"
with open(out, "w", encoding="utf-8") as f:
    for n, p in enumerate(parts, 1):
        if len(parts) > 1:
            f.write(f"===== Часть {n} из {len(parts)} =====\n")
        f.write(p + "\n\n")
print(f"Готово: {out} ({len(parts)} сообщ.)")
if not links:
    print("Ссылок пока нет — оглавление без ссылок. Добавь их в Отзывы/ссылки_max.txt и запусти снова.")
