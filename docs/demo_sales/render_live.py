"""Build an 82-second demo from verified, privacy-redacted app screenshots.

Run ``python -m docs.demo_sales.record_ui --live`` first to capture a new
Gemini-backed run. This renderer never calls a model or reads an API key.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DEMO = Path(__file__).resolve().parent
RAW = ROOT / ".runtime" / "demo_sales_frames"
CAPTURE = DEMO / "capture"
WIDTH, HEIGHT, FPS, SECONDS = 1280, 720, 12, 82
FONT = Path(r"C:\Windows\Fonts\malgun.ttf")
BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")
TIMELINE = [
    (0, 6, None, "받은 문의를 보낼 수 있는 초안으로"),
    (6, 11, 0, "실제 해외영업 작업실에서 시작"),
    (11, 15, 1, "가상 바이어 이메일 입력"),
    (15, 19, 2, "견적 요청 내용 입력"),
    (19, 23, 3, "품목과 수량 확인"),
    (23, 27, 4, "납기와 인도조건 요청 확인"),
    (27, 31, 5, "이메일 본문 등록 준비"),
    (31, 35, 6, "가상 회사 원자료 TXT 첨부"),
    (35, 40, 7, "원자료 읽기와 출처 연결"),
    (40, 45, 8, "영어 회신·정중한 톤 선택"),
    (45, 49, 9, "실제 Gemini 작성 요청"),
    (49, 54, 10, "요청 분석과 독립 검수 진행"),
    (54, 59, 11, "검수한 초안 조립 완료"),
    (59, 65, 12, "답변별 원자료 출처 표시"),
    (65, 74, 13, "영문 회신 초안 확인 · TXT 다운로드 가능"),
    (74, 82, None, "담당자가 확인한 뒤 발송 결정"),
]


def _text(draw: ImageDraw.ImageDraw, xy, value, size, color, *, bold=False):
    draw.text(xy, value, font=ImageFont.truetype(str(BOLD if bold else FONT), size), fill=color)


def publish_capture() -> None:
    provenance = json.loads((RAW / "provenance.json").read_text(encoding="utf-8"))
    if provenance.get("live_gemini") is not True:
        raise ValueError("Only an actual Gemini-backed capture may be published as a live demo")
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    if len(manifest) != 14:
        raise ValueError("The complete UI sequence is required")
    CAPTURE.mkdir(exist_ok=True)
    for i, item in enumerate(manifest):
        with Image.open(RAW / item["file"]) as original:
            image = original.convert("RGB")
        if i in {9, 10, 11}:
            # The original is a password input, but suppress even its masked length.
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((109, 278, 1171, 321), radius=10,
                                   fill=(246, 250, 253), outline=(188, 206, 225), width=1)
            _text(draw, (130, 285), "Gemini 키 입력 완료 · 값 비공개", 17, (44, 74, 110))
        image.save(CAPTURE / f"{i:03d}.png", optimize=True)
    (DEMO / "sample_live_reply_draft.txt").write_text(
        (RAW / "model_reply.txt").read_text(encoding="utf-8"), encoding="utf-8")
    (CAPTURE / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")


def _opening(t: float) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (5, 16, 34))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 70, 1225, 650), radius=36, fill=(14, 32, 61), outline=(37, 104, 154), width=2)
    _text(draw, (98, 121), "DOCUMENT STANDARDIZATION AI AGENT", 24, (61, 224, 240), bold=True)
    _text(draw, (98, 217), "받은 문의를", 64, (245, 249, 255), bold=True)
    _text(draw, (98, 314), "보낼 수 있는 초안으로", 64, (108, 191, 255), bold=True)
    _text(draw, (104, 478), "실제 작업실 화면  ·  실제 Gemini 작성·검수", 28, (199, 225, 244))
    _text(draw, (104, 559), "가상 메일·가상 회사 자료 사용  |  이메일 미발송", 22, (169, 196, 221))
    return image


def _closing() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (5, 16, 34))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((65, 85, 1215, 637), radius=36, fill=(14, 32, 61), outline=(37, 104, 154), width=2)
    _text(draw, (105, 133), "초안 완성 · 출처 연결", 55, (235, 248, 255), bold=True)
    _text(draw, (108, 267), "Gemini가 가상 원자료의 단가·납기·인도조건을 반영", 29, (103, 206, 243))
    _text(draw, (108, 344), "담당자가 수신인·가격·서명·거래조건을 최종 확인", 27, (210, 230, 245))
    _text(draw, (108, 435), "실제 Gmail 발송은 수행하지 않았습니다.", 25, (255, 202, 116))
    _text(draw, (108, 556), "실제 Gemini 호출 결과 · 가상 데이터 · 사람이 검토할 초안", 22, (158, 186, 214))
    return image


def frame(t: float, screens: dict[int, Image.Image]) -> Image.Image:
    step = next((item for item in TIMELINE if item[0] <= t < item[1]), TIMELINE[-1])
    start, end, index, caption = step
    if index is None:
        return _opening(t) if start == 0 else _closing()
    image = Image.new("RGB", (WIDTH, HEIGHT), (5, 16, 34))
    screenshot = screens[index]
    image.paste(screenshot, (64, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 640, WIDTH, HEIGHT), fill=(5, 19, 39))
    draw.rectangle((0, 640, 12, HEIGHT), fill=(38, 216, 225))
    _text(draw, (42, 656), caption, 27, (244, 248, 255), bold=True)
    _text(draw, (1020, 666), "실제 화면 · 미발송", 17, (134, 190, 216))
    draw.rectangle((0, 715, int(WIDTH * t / SECONDS), 720), fill=(38, 216, 225))
    return image


def main() -> None:
    publish_capture()
    sys.path.insert(0, str(ROOT / ".runtime" / "video_tools"))
    import imageio_ffmpeg

    screens = {i: Image.open(CAPTURE / f"{i:03d}.png").convert("RGB").resize((1152, 648))
               for i in range(14)}
    output = DEMO / "buyer_email_live_demo.mp4"
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for n in range(FPS * SECONDS):
            process.stdin.write(frame(n / FPS, screens).tobytes())
        process.stdin.close()
        assert process.stderr is not None
        errors = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() != 0:
            raise RuntimeError(errors[-3000:])
    finally:
        if process.poll() is None:
            process.kill()
    frame(68, screens).save(DEMO / "buyer_email_live_poster.png")
    # GitHub README previews the key interactions; the linked MP4 is 82 seconds.
    times = [6, 15, 23, 31, 36, 42, 47, 52, 56, 60, 68, 69, 70, 71, 74, 76]
    preview = [frame(t, screens).resize((800, 450)).quantize(colors=64, method=2) for t in times]
    preview[0].save(DEMO / "buyer_email_live_preview.gif", save_all=True,
                    append_images=preview[1:], duration=1000, loop=0, optimize=True, disposal=2)
    print(output)


if __name__ == "__main__":
    main()
