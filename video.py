"""Автомонтаж: вертикальный формат 9:16, склейка клипов, вырезание пауз, субтитры, хук в начале."""
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
YELLOW = (255, 221, 0, 255)
WHITE = (255, 255, 255, 255)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")  # small — быстро, medium — точнее
_whisper = None


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-1500:]}")
    return r


def duration(path) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(r.stdout)["format"]["duration"])


def _has_audio(path) -> bool:
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)])
    return bool(r.stdout.strip())


# ---------- 1. Приводим каждый клип к 1080x1920 (горизонтальные — с размытым фоном) ----------
def to_vertical(src, dst):
    vf = (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:3,eq=brightness=-0.08[bg];"
        f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not _has_audio(src):  # у клипа без звука добавляем тишину, иначе склейка ломается
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "0:a?" if _has_audio(src) else "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(dst)]
    _run(cmd)


def concat(parts, dst):
    lst = Path(dst).with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dst)])


# ---------- 2. Вырезаем паузы (джамп-каты) ----------
def cut_silence(src, dst, noise_db=-35, min_silence=0.6, keep=0.15):
    r = subprocess.run(["ffmpeg", "-i", str(src), "-af", f"silencedetect=n={noise_db}dB:d={min_silence}",
                        "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    total = duration(src)
    if not starts:
        return False
    segs, cur = [], 0.0
    for s, e in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s + keep - cur > 0.2:
            segs.append((cur, s + keep))
        cur = max(cur, e - keep)
    if total - cur > 0.2:
        segs.append((cur, total))
    # клип почти без речи (проезд, детали под музыку) не трогаем
    if not segs or sum(b - a for a, b in segs) < 0.3 * total:
        return False
    fc = "".join(
        f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}];"
        for i, (a, b) in enumerate(segs)
    ) + "".join(f"[v{i}][a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    _run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(dst)])
    return True


# ---------- 3. Распознаём речь ----------
@dataclass
class Word:
    start: float
    end: float
    text: str


def transcribe(path) -> list[Word]:
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(str(path), language="ru", word_timestamps=True, vad_filter=True)
    return [Word(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or []) if w.word.strip()]


def transcribe_text(path) -> str:
    return " ".join(w.text for w in transcribe(path))


def frames(path, n=5) -> list[bytes]:
    """Несколько кадров из ролика (JPEG), чтобы нейросеть «увидела», что в нём показано."""
    total = duration(path)
    out = []
    for i in range(n):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{total * (i + 0.5) / n:.2f}", "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=720:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    return out


# ---------- 4. Рисуем субтитры и хук картинками ----------
def _wrap(draw, words, font, max_w):
    lines, cur = [], []
    for w in words:
        if cur and draw.textlength(" ".join(t for t, _ in cur + [w]), font=font) > max_w:
            lines.append(cur)
            cur = []
        cur.append(w)
    return lines + ([cur] if cur else [])


def _caption_png(chunk: list[str], active: int, path, y_center=int(H * 0.72)):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 78)
    words = [(t.upper(), i == active) for i, t in enumerate(chunk)]
    lines = _wrap(d, words, font, W - 140)
    lh = 100
    y = y_center - len(lines) * lh // 2
    for line in lines:
        text_w = d.textlength(" ".join(t for t, _ in line), font=font)
        x = (W - text_w) / 2
        for t, on in line:
            d.text((x, y), t, font=font, fill=YELLOW if on else WHITE, stroke_width=7, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(t + " ", font=font)
        y += lh
    img.save(path)


def _hook_png(text, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = 96
    while True:
        font = ImageFont.truetype(FONT, size)
        lines = _wrap(d, [(t, 0) for t in text.upper().split()], font, W - 180)
        if len(lines) <= 3 or size <= 56:
            break
        size -= 8
    lh = int(size * 1.25)
    y = int(H * 0.14)
    for line in lines:
        s = " ".join(t for t, _ in line)
        tw = d.textlength(s, font=font)
        x = (W - tw) / 2
        d.rounded_rectangle((x - 30, y - 14, x + tw + 30, y + lh - 4), radius=22, fill=YELLOW)
        d.text((x, y), s, font=font, fill=(0, 0, 0, 255))
        y += lh + 10
    img.save(path)


def _blank_png(path):
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(path)


def _chunks(words: list[Word], max_words=3, max_gap=0.5):
    chunk = []
    for w in words:
        if chunk and (len(chunk) >= max_words or w.start - chunk[-1].end > max_gap
                      or re.search(r"[.!?,]$", chunk[-1].text)):
            yield chunk
            chunk = []
        chunk.append(w)
    if chunk:
        yield chunk


def build_overlay(words: list[Word], hook: str, total: float, tmp: Path) -> Path:
    """Собирает дорожку-оверлей: список PNG с длительностями для concat-демуксера."""
    events = []  # (start, end, png)
    n = 0

    def png(fn, *a):
        nonlocal n
        n += 1
        p = tmp / f"o{n:05d}.png"
        fn(*a, p)
        return p

    hook_end = min(3.0, total) if hook else 0
    for ch in _chunks(words):
        texts = [re.sub(r"[.,!?…]+$", "", w.text) for w in ch]
        for i, w in enumerate(ch):
            end = ch[i + 1].start if i + 1 < len(ch) else w.end + 0.25
            events.append((max(w.start, hook_end), end, png(_caption_png, texts, i)))
    blank = png(_blank_png)
    hook_png = png(_hook_png, hook) if hook else None

    lines, t = [], 0.0
    if hook_png:
        lines.append((hook_png, hook_end))
        t = hook_end
    for s, e, p in sorted(events):
        if e <= t:
            continue
        if s > t + 0.01:
            lines.append((blank, s - t))
            t = s
        lines.append((p, e - t))
        t = e
    if total > t:
        lines.append((blank, total - t))
    lst = tmp / "overlay.txt"
    body = "".join(f"file '{p}'\nduration {d:.3f}\n" for p, d in lines)
    lst.write_text(body + f"file '{lines[-1][0]}'\n")
    return lst


# ---------- Главная функция ----------
def prepare(clips: list[str], workdir: Path, jumpcut=True) -> Path:
    """Вертикальный формат + склейка + вырезание пауз. Возвращает путь к промежуточному ролику."""
    parts = []
    for i, c in enumerate(clips):
        p = workdir / f"part{i}.mp4"
        to_vertical(c, p)
        if jumpcut:
            cut = workdir / f"part{i}_cut.mp4"
            if cut_silence(p, cut):
                p = cut
        parts.append(p)
    if len(parts) == 1:
        return parts[0]
    base = workdir / "joined.mp4"
    concat(parts, base)
    return base


def render(base: Path, words: list[Word], hook: str, out: str, workdir: Path, music: str | None = None):
    """Накладывает субтитры и хук, выравнивает громкость."""
    total = duration(base)
    overlay = build_overlay(words, hook, total, workdir)
    cmd = ["ffmpeg", "-y", "-i", str(base), "-f", "concat", "-safe", "0", "-i", str(overlay)]
    fc = "[0:v][1:v]overlay=0:0:format=auto:shortest=1,format=yuv420p[v];"
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        fc += "[2:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.5[a]"
    else:
        fc += "[0:a]loudnorm=I=-14:TP=-1.5[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", out]
    _run(cmd)
    return total


def montage(clips: list[str], out: str, hook: str = "", subtitles=True, jumpcut=True, music: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = prepare(clips, tmp, jumpcut)
        words = transcribe(base) if subtitles else []
        total = render(base, words, hook, out, tmp, music)
        return {"duration": total, "text": " ".join(w.text for w in words)}


if __name__ == "__main__":
    # Для больших файлов (>20 МБ, которые бот не может скачать):
    #   .venv/bin/python video.py "ХУК НА ОБЛОЖКУ" клип1.mp4 клип2.mp4
    import sys
    hook, *files = sys.argv[1:]
    res = montage(files, "готово.mp4", hook=hook)
    print("Готово: готово.mp4,", round(res["duration"]), "сек")
