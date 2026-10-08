"""Render the public-data buyer-email walkthrough as a silent MP4.

Requires Pillow and imageio-ffmpeg. The email and reply are illustrative,
not an unreviewed live-model result or a sent message.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT, FPS, SECONDS = 1280, 720, 15, 30
BG = (5, 16, 34)
PANEL = (13, 30, 54)
PANEL_2 = (18, 42, 70)
CYAN = (39, 215, 224)
BLUE = (92, 130, 255)
WHITE = (239, 247, 255)
MUTED = (152, 176, 199)
AMBER = (255, 195, 102)
FONT = Path(r"C:\Windows\Fonts\NotoSansKR-VF.ttf")
if not FONT.exists():
    FONT = Path(r"C:\Windows\Fonts\malgun.ttf")


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    face = Path(r"C:\Windows\Fonts\malgunbd.ttf") if bold else FONT
    return ImageFont.truetype(str(face), size)


FONTS = {size: font(size) for size in (16, 18, 20, 22, 24, 26, 28, 32, 38, 42, 54, 68)}
BOLD = {size: font(size, bold=True) for size in FONTS}


def txt(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
        size: int = 24, color: tuple[int, int, int] = WHITE) -> None:
    draw.text(xy, value, font=BOLD[size] if size >= 28 else FONTS[size], fill=color)


def box(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int],
        fill: tuple[int, int, int] = PANEL, radius: int = 22,
        outline: tuple[int, int, int] | None = None) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)


def chrome(draw: ImageDraw.ImageDraw, active: int, t: float) -> None:
    box(draw, (32, 25, 1248, 89), (10, 26, 47), 18)
    txt(draw, (58, 39), "문서 표준화  AI AGENT", 24)
    txt(draw, (990, 44), "GLOBAL WORKSPACE", 16, CYAN)
    labels = ["문의", "요청 파악", "초안", "검수", "완료"]
    for i, label in enumerate(labels):
        x = 58 + 222 * i
        color = CYAN if i <= active else MUTED
        draw.ellipse((x, 664, x + 13, 677), fill=color)
        txt(draw, (x + 23, 655), label, 18, color)
        if i < 4:
            draw.line((x + 113, 671, x + 199, 671), fill=(45, 77, 103), width=3)
    progress = int(1216 * max(0, min(1, t / SECONDS)))
    draw.rectangle((32, 703, 32 + progress, 707), fill=CYAN)
    txt(draw, (1072, 619), "샘플 데이터 · 미발송", 16, MUTED)


def scene_0(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (66, 170), "받은 문의를", 68)
    txt(draw, (66, 270), "보낼 수 있는 초안으로", 68, CYAN)
    txt(draw, (72, 391), "이메일 입력  →  요청 파악  →  회신 초안  →  사람 검토", 28, MUTED)
    box(draw, (73, 489, 545, 552), (21, 55, 76), 18)
    txt(draw, (97, 504), "해외영업 · 바이어 회신 흐름 데모", 24)
    cx = 975
    for r in (74, 118, 163):
        draw.ellipse((cx-r, 330-r, cx+r, 330+r), outline=(23, 69, 94), width=2)
    x = cx + int(126 * math.cos(t * 1.6))
    y = 330 + int(126 * math.sin(t * 1.6))
    draw.ellipse((x-10, y-10, x+10, y+10), fill=CYAN)
    box(draw, (916, 293, 1037, 365), PANEL_2, 18, CYAN)
    draw.rounded_rectangle((941, 310, 1012, 350), radius=5, outline=CYAN, width=3)
    draw.line((942, 313, 976, 334, 1011, 313), fill=CYAN, width=3)


EMAIL = [
    "Hello Sales Team,",
    "Could you quote 500 standard office chairs",
    "for delivery to Dallas? Please include the",
    "estimated lead time and delivery terms.",
    "Thank you.",
]


def scene_1(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (64, 126), "01  바이어 문의를 입력", 42)
    txt(draw, (67, 185), "공개 견적 요청 예시의 항목을 바탕으로 재구성한 가상 이메일", 20, MUTED)
    box(draw, (64, 238, 1216, 598), PANEL)
    txt(draw, (93, 260), "INCOMING EMAIL", 20, CYAN)
    txt(draw, (93, 307), "Subject: Request for quotation · office chairs", 28)
    draw.line((93, 355, 1185, 355), fill=(47, 80, 105), width=2)
    visible = min(len(EMAIL), max(0, int((t - 4) * 2.5) + 1))
    for i, line in enumerate(EMAIL[:visible]):
        txt(draw, (94, 379 + 37*i), line, 24, WHITE if i < 4 else MUTED)


def scene_2(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (64, 126), "02  무엇을 답해야 하는지 분리", 42)
    txt(draw, (66, 183), "이메일에 적힌 요청만 추출하고, 없는 거래조건은 확인 대상으로 남김", 20, MUTED)
    items = [
        ("01", "견적 요청", "표준 사무용 의자 500개"),
        ("02", "납기", "예상 리드타임 요청"),
        ("03", "인도조건", "Dallas 배송조건 요청"),
    ]
    for i, (number, title, detail) in enumerate(items):
        y = 252 + 105*i
        box(draw, (64, y, 873, y+86), PANEL_2)
        txt(draw, (88, y+22), number, 28, CYAN)
        txt(draw, (160, y+19), title, 28)
        txt(draw, (390, y+24), detail, 22, MUTED)
    box(draw, (907, 252, 1216, 548), (55, 38, 28), outline=(139, 101, 58))
    txt(draw, (932, 278), "확인 대기", 28, AMBER)
    txt(draw, (932, 344), "단가", 24)
    txt(draw, (932, 393), "실제 납기", 24)
    txt(draw, (932, 442), "배송 조건", 24)
    txt(draw, (932, 504), "임의 약속 없음", 18, AMBER)


REPLY = [
    "Dear Demo Buyer,",
    "Thank you for your inquiry about 500 office chairs",
    "for delivery to Dallas.",
    "We can prepare a quotation. Could you confirm the",
    "required specifications and preferred delivery date?",
    "We will verify pricing, lead time, and delivery terms",
    "internally before sending a formal quote.",
    "Best regards,",
    "Sales Team",
]


def scene_3(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (64, 126), "03  검토 가능한 영어 회신 초안", 42)
    txt(draw, (67, 184), "확인되지 않은 단가와 납기를 만들어 넣지 않음", 20, MUTED)
    box(draw, (64, 235, 1216, 611), PANEL)
    txt(draw, (91, 253), "DRAFT · NOT SENT", 20, CYAN)
    txt(draw, (91, 291), "Re: Request for quotation · office chairs", 28)
    draw.line((91, 333, 1186, 333), fill=(47, 80, 105), width=2)
    visible = min(len(REPLY), max(0, int((t - 14) * 1.55) + 1))
    for i, line in enumerate(REPLY[:visible]):
        txt(draw, (94, 348 + 26*i), line, 22, WHITE)
    draw.rectangle((95, 590, 95 + min(1085, int(max(0, t-14)*120)), 594), fill=CYAN)


def scene_4(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (64, 126), "04  출처와 누락을 다시 확인", 42)
    txt(draw, (67, 182), "요청 3건을 회신과 대조 · 회사가 확인할 항목은 남겨 둠", 20, MUTED)
    checks = [
        ("✓", "견적 요청", "회신에 반영"),
        ("✓", "납기·인도조건", "확인 후 안내로 명시"),
        ("!", "가격·실제 납기", "담당자 확인 필요"),
    ]
    for i, (mark, title, status) in enumerate(checks):
        y = 256 + 105*i
        box(draw, (64, y, 1216, y+83), PANEL_2)
        color = AMBER if mark == "!" else CYAN
        txt(draw, (91, y+18), mark, 32, color)
        txt(draw, (157, y+21), title, 26)
        txt(draw, (891, y+24), status, 22, color)


def scene_5(draw: ImageDraw.ImageDraw, t: float) -> None:
    txt(draw, (64, 166), "회신 초안 준비 완료", 54)
    txt(draw, (66, 245), "담당자가 가격·납기·수신인을 확인한 뒤 발송 여부를 결정", 26, MUTED)
    box(draw, (65, 354, 568, 447), (18, 65, 76), 20, CYAN)
    txt(draw, (98, 376), "검토용 결과물: 영문 회신 초안", 28)
    box(draw, (596, 354, 1135, 447), PANEL_2, 20)
    txt(draw, (630, 376), "실제 발송: 수행하지 않음", 28, AMBER)
    txt(draw, (67, 518), "실제 모델·고객 성능을 주장하지 않는 시연용 예시", 20, MUTED)


SCENES = [scene_0, scene_1, scene_2, scene_3, scene_4, scene_5]
BOUNDS = [0, 4, 8, 14, 22, 26, 30]


def frame(t: float) -> Image.Image:
    index = next(i for i in range(6) if BOUNDS[i] <= t < BOUNDS[i+1])
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(im)
    chrome(draw, min(index, 4), t)
    SCENES[index](draw, t)
    age = t - BOUNDS[index]
    edge = min(age, BOUNDS[index+1] - t)
    if edge < 0.35:
        shade = Image.new("RGB", im.size, BG)
        im = Image.blend(shade, im, max(0, edge / 0.35))
    return im


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".runtime" / "video_tools"))
    import imageio_ffmpeg

    output = ROOT / "buyer_email_demo.mp4"
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-", "-an",
               "-c:v", "libx264", "-preset", "fast", "-crf", "24", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for n in range(FPS * SECONDS):
            process.stdin.write(frame(n / FPS).tobytes())
        process.stdin.close()
        assert process.stderr is not None
        errors = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() != 0:
            raise RuntimeError(errors[-3000:])
    finally:
        if process.poll() is None:
            process.kill()
    frame(16).save(ROOT / "buyer_email_demo_poster.png")
    print(output)


if __name__ == "__main__":
    main()
