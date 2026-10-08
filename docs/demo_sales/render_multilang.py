"""Edit verified English, Spanish and Hebrew Gemini captures into a brisk demo."""

from __future__ import annotations

import json
from hashlib import sha256
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from docs.demo_sales.record_multilang import CASES, check_reply
from docs.demo_sales.record_ui import ROOT


DEMO = Path(__file__).resolve().parent
RAW = ROOT / ".runtime" / "demo_sales_multilang"
STILLS = RAW / "stills"
OUTPUT = DEMO / "buyer_email_multilingual_live_demo.mp4"
SIZE = (1280, 720)
FONT = Path(r"C:\Windows\Fonts\malgun.ttf")
BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")
LANGUAGES = (("en", "ENGLISH", "영어 문의 → 영어 회신"),
             ("es", "ESPAÑOL", "스페인어 문의 → 스페인어 회신"),
             ("he", "HEBREW", "히브리어 문의 → 히브리어 회신"))
DURATIONS = (5, 6, 3, 13, 5, 6, 3, 13, 5, 6, 3, 13, 5, 7)


def text(draw, position, value, size, color, *, bold=False):
    draw.text(position, value, font=ImageFont.truetype(str(BOLD if bold else FONT), size), fill=color)


def card(title, sub, *, closing=False):
    image = Image.new("RGB", SIZE, "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.ellipse((815, -245, 1380, 320), outline="#e6f1ff", width=7)
    draw.ellipse((-140, 500, 320, 960), fill="#eef5ff")
    text(draw, (94, 100), "DOCUMENT STANDARDIZATION AI AGENT", 25, "#1469f4", bold=True)
    text(draw, (94, 220), title, 63, "#17324d", bold=True)
    text(draw, (98, 330), sub, 32, "#376082")
    if closing:
        text(draw, (100, 520), "가상 거래 자료 · 실제 Gemini 3.6 Flash · 미발송", 24, "#4c6a83")
    else:
        for n, label in enumerate(("EN", "ES", "HE")):
            x = 100 + 115 * n
            draw.ellipse((x, 470, x + 84, 554), fill="#1469f4" if n == 0 else "#e8f2ff")
            text(draw, (x + 21, 490), label, 28, "#ffffff" if n == 0 else "#1469f4", bold=True)
        text(draw, (100, 592), "화면 녹화·편집 재생  |  실제 API 처리 시간을 나타내지는 않음", 20, "#57718a")
    return image


def screenshot_frame(original, label, caption, *, zoom=False):
    with Image.open(original) as source:
        source = source.convert("RGB")
        if source.size != SIZE:
            raise ValueError(f"unexpected capture size: {original}")
        # Playwright masks the private API-key field in magenta by default.
        # Keep the mask, but use the demo palette so the edited frame is legible.
        source.putdata([(232, 242, 255) if pixel == (255, 0, 255) else pixel
                        for pixel in source.get_flattened_data()])
        if zoom:
            cropped = source.crop((100, 150, 1180, 595)).resize((1180, 486), Image.Resampling.LANCZOS)
            image = Image.new("RGB", SIZE, "#eef5ff")
            image.paste(cropped, (50, 100))
        else:
            image = source.copy()
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((22, 12, 376, 68), radius=22, fill="#1469f4")
    text(draw, (43, 26), label, 25, "#ffffff", bold=True)
    draw.rectangle((0, 625, 1280, 720), fill="#17324d")
    text(draw, (40, 644), caption, 31, "#ffffff", bold=True)
    text(draw, (1035, 681), "실제 화면 · 미발송", 16, "#c8e2ff")
    return image


def source_frames(language):
    folder = RAW / language
    provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    if (provenance.get("live_gemini") is not True or provenance.get("model") != "gemini-3.6-flash"
            or provenance.get("language") != language):
        raise ValueError(f"{language}: actual model provenance is required")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    def stage(label, *, last=False):
        matches = [folder / item["file"] for item in manifest if item["label"] == label]
        if not matches:
            raise ValueError(f"{language}: missing capture stage {label}")
        return matches[-1] if last else matches[0]
    return (stage("가상 바이어 문의 입력", last=True), stage("회신 언어·톤 선택"),
            stage("선택 언어 회신 초안 표시"))


def render():
    STILLS.mkdir(parents=True, exist_ok=True)
    images = [card("받은 언어 그대로, 같은 언어로 답장", "영어 · 스페인어 · 히브리어  |  3개의 실제 모델 응답")]
    proofs = []
    for code, badge, caption in LANGUAGES:
        inbound, selection, reply = source_frames(code)
        reply_text = (RAW / code / "model_reply.txt").read_text(encoding="utf-8")
        check_reply(code, reply_text)
        proofs.append({"language": code, "model": "gemini-3.6-flash",
                       "fictional_input_sha256": sha256(CASES[code].encode()).hexdigest(),
                       "reply_sha256": sha256(reply_text.encode()).hexdigest()})
        images.extend([
            screenshot_frame(inbound, badge, f"01  {caption.split(' → ')[0]} 입력"),
            screenshot_frame(selection, badge, "02  회신 언어 선택 · 실제 작업실"),
            screenshot_frame(reply, badge, f"03  Gemini 3.6 Flash가 {caption.split(' → ')[1]} 생성"),
            screenshot_frame(reply, badge, "04  답변 초안 확인 · 담당자 검토 전", zoom=True),
        ])
    images.append(card("세 가지 언어, 하나의 검토 흐름", "요청 분석 → 자료 대조 → 같은 언어의 회신 초안", closing=True))
    if len(images) != len(DURATIONS) or not 60 <= sum(DURATIONS) <= 120:
        raise ValueError("demo must contain every language and run for 1–2 minutes")
    for index, image in enumerate(images):
        image.save(STILLS / f"{index:02d}.png", optimize=True)
    concat = STILLS / "timeline.txt"
    lines = []
    for index, seconds in enumerate(DURATIONS):
        lines.extend((f"file '{index:02d}.png'", f"duration {seconds}"))
    lines.append(f"file '{len(images)-1:02d}.png'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import sys
    sys.path.insert(0, str(ROOT / ".runtime" / "video_tools"))
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", concat.name,
               "-t", str(sum(DURATIONS)), "-r", "12", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUTPUT)]
    subprocess.run(command, check=True, capture_output=True, cwd=STILLS)
    images[11].save(DEMO / "buyer_email_multilingual_live_poster.png", optimize=True)
    preview = [images[index].resize((800, 450)).quantize(colors=64, method=2)
               for index in (0, 3, 6, 7, 10, 11, 13)]
    preview[0].save(DEMO / "buyer_email_multilingual_live_preview.gif", save_all=True,
                    append_images=preview[1:], duration=1100, loop=0, optimize=True, disposal=2)
    for item in proofs:
        code = item['language']
        (DEMO / f"sample_live_reply_{code}.txt").write_text(
            (RAW / code / "model_reply.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (DEMO / "multilingual_live_provenance.json").write_text(
        json.dumps({"capture": "actual Gemini API via the sales workspace",
                    "source": "fictional demo company facts",
                    "source_sha256": sha256((DEMO / "sample_company_facts.txt").read_bytes()).hexdigest(),
                    "responses": proofs}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUTPUT} (timeline target {sum(DURATIONS)} seconds)")


if __name__ == "__main__":
    render()
