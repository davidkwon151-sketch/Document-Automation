"""Publish a 55-second, source-backed RA CTD workspace demo."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from docs.demo_ra.record import RAW, ROOT, SECTIONS, checked_output


DEMO = Path(__file__).resolve().parent
CAPTURE = DEMO / "capture"
STILLS = DEMO / "stills"
OUTPUT = DEMO / "ra_ctd_short_demo.mp4"
WIDTH, HEIGHT, SECONDS = 1280, 720, 55
FONT = Path(r"C:\Windows\Fonts\malgun.ttf")
BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")
TIMELINE = (
    ("opening", 4, None, "원자료에서 CTD 작업초안까지"),
    ("workspace", 4, "workspace", "01  RA 작업실에서 시작"),
    ("files", 5, "files_selected", "02  양식 1개 + 합성 원자료 3개 업로드"),
    ("mapping", 4, "mapping", "03  양식 입력칸 자동 분석"),
    ("setup", 5, "ctd_setup", "04  제품·제형과 작성 범위 지정"),
    ("sections", 4, "sections_selected", "05  CTD 3.2.P 절 3개 선택"),
    ("preview", 6, "source_preview", "06  절별 원문 후보 3/3 연결"),
    ("evidence", 6, "source_evidence", "07  파일·출처 ID·문자 위치 대조"),
    ("export", 5, "export_ready", "08  담당자가 근거 확인 후 다운로드"),
    ("downloaded", 4, "downloaded", "09  검토용 DOCX와 출처 기록 생성"),
    ("result", 8, None, "저장 후 검수 통과 · 제출본 아님"),
)


def font(size: int, *, bold: bool = False):
    return ImageFont.truetype(str(BOLD if bold else FONT), size)


def draw_text(draw, xy, value, size, color, *, bold=False):
    draw.text(xy, value, font=font(size, bold=bold), fill=color)


def card(title: str, subtitle: str, *, result=False) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.ellipse((880, -180, 1430, 370), outline="#dceaff", width=7)
    draw.ellipse((-180, 455, 330, 960), fill="#ecf4ff")
    draw_text(draw, (94, 91), "DOCUMENT STANDARDIZATION AI AGENT  /  RA", 24, "#1469f4", bold=True)
    draw_text(draw, (92, 216), title, 57 if not result else 50, "#163858", bold=True)
    draw_text(draw, (96, 314), subtitle, 29, "#406687")
    if result:
        for i, (heading, value) in enumerate((("선택 절", "3 / 3"),
                                               ("배치번호", "DEMO-B01"),
                                               ("시험결과", "98.7% · 98.2%"))):
            x = 93 + 365 * i
            draw.rounded_rectangle((x, 435, x + 336, 568), radius=21,
                                   fill="#f0f6ff", outline="#d4e5ff", width=2)
            draw_text(draw, (x + 23, 453), heading, 22, "#426583")
            draw_text(draw, (x + 23, 491), value, 30, "#1469f4", bold=True)
    else:
        for i, label in enumerate(("양식 업로드", "원문 연결", "독립 검수", "DOCX 다운로드")):
            x = 95 + i * 288
            draw.ellipse((x, 465, x + 61, 526), fill="#1469f4")
            draw_text(draw, (x + 22, 477), str(i + 1), 25, "#ffffff", bold=True)
            draw_text(draw, (x, 548), label, 22, "#365a79")
    draw_text(draw, (94, 637), "합성 시험자료 · 원문 발췌 기입 · 실제 LLM 호출 0회 · 법정 제출본 아님",
              21, "#607a92")
    return image


def document_result() -> Image.Image:
    """Show the Word-rendered page of the checked synthetic DOCX output."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f5f9ff")
    draw = ImageDraw.Draw(image)
    draw_text(draw, (64, 47), "실제 생성 문서", 48, "#163858", bold=True)
    draw_text(draw, (66, 119), "CTD 3.2.P 작업초안 · Word 렌더", 26, "#406687")
    for i, (heading, value) in enumerate((("선택 절", "3 / 3"),
                                          ("배치번호", "DEMO-B01"),
                                          ("시험결과", "98.7% · 98.2%"))):
        y = 230 + 105 * i
        draw.rounded_rectangle((66, y, 651, y + 86), radius=18,
                               fill="#ffffff", outline="#d4e5ff", width=2)
        draw_text(draw, (87, y + 13), heading, 20, "#426583")
        draw_text(draw, (240, y + 16), value, 27, "#1469f4", bold=True)
    draw_text(draw, (66, 603), "저장 후 입력 위치 검수 통과", 23, "#1469f4", bold=True)
    draw_text(draw, (66, 664), "합성 시험자료 · 원문 기입 · LLM 0회 · 제출본 아님", 17, "#607a92")
    with Image.open(CAPTURE / "output_page.png") as original:
        page = original.convert("RGB")
    page.thumbnail((498, 643), Image.Resampling.LANCZOS)
    x, y = 710, 40
    draw.rounded_rectangle((x - 10, y - 10, x + page.width + 10, y + page.height + 10),
                           radius=12, fill="#dceaff")
    image.paste(page, (x, y))
    return image


def screen(path: Path, number: int, caption: str) -> Image.Image:
    with Image.open(path) as original:
        image = original.convert("RGB")
    if image.size != (WIDTH, HEIGHT):
        raise ValueError("RA capture size changed")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((22, 18, 252, 64), radius=18, fill="#1469f4")
    draw_text(draw, (42, 26), f"RA  /  {number:02d}", 21, "#ffffff", bold=True)
    draw.rectangle((0, 627, WIDTH, HEIGHT), fill="#153a60")
    draw.rectangle((0, 627, 12, HEIGHT), fill="#53c5f2")
    draw_text(draw, (38, 646), caption, 29, "#ffffff", bold=True)
    draw_text(draw, (965, 685), "합성 자료 · 원문 기입", 16, "#b9daff")
    return image


def publish_capture() -> dict:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((RAW / "provenance.json").read_text(encoding="utf-8"))
    expected_stages = [item[2] for item in TIMELINE if item[2]]
    if ([item["stage"] for item in manifest] != expected_stages
            or provenance["mode"] != "deterministic exact-source excerpt filling; no LLM call"
            or provenance["sections"] != list(SECTIONS)
            or provenance["coverage"] != "3/3 selected sections"):
        raise ValueError("RA capture provenance or stages do not match the video")
    output = checked_output((RAW / "output.zip").read_bytes())
    if output["output_sha256"] != provenance["output_sha256"]:
        raise ValueError("The video output changed after capture")
    page = CAPTURE / "output_page.png"
    page_proof = json.loads((CAPTURE / "output_page.json").read_text(encoding="utf-8"))
    if (page_proof["output_sha256"] != output["output_sha256"]
            or page_proof["image_sha256"] != sha256(page.read_bytes()).hexdigest()):
        raise ValueError("The Word-rendered page does not match the checked DOCX output")
    CAPTURE.mkdir(exist_ok=True)
    for item in manifest:
        shutil.copyfile(RAW / item["file"], CAPTURE / item["file"])
    shutil.copyfile(RAW / "output.zip", DEMO / "CTD_demo_output.zip")
    (DEMO / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {item["stage"]: CAPTURE / item["file"] for item in manifest}


def render() -> None:
    sources = publish_capture()
    if sum(item[1] for item in TIMELINE) != SECONDS or not 0 < SECONDS < 60:
        raise ValueError("RA demo must be under one minute")
    STILLS.mkdir(exist_ok=True)
    slides = []
    for number, (stage, seconds, source, caption) in enumerate(TIMELINE):
        image = (screen(sources[source], number, caption) if source else
                 card("원자료에서 CTD 작업초안까지", "RA 문서 표준화, 55초 시연") if stage == "opening" else
                 document_result())
        name = f"{number:02d}.png"
        image.save(STILLS / name, optimize=True)
        slides.append((name, seconds))
    sys.path.insert(0, str(ROOT / ".runtime" / "video_tools"))
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
               "-r", "12", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUTPUT)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for name, duration in slides:
            with Image.open(STILLS / name) as frame:
                pixels = frame.convert("RGB").tobytes()
            for _ in range(duration * 12):
                process.stdin.write(pixels)
        process.stdin.close()
        assert process.stderr is not None
        errors = process.stderr.read().decode("utf-8", errors="replace")
        if process.wait() != 0:
            raise RuntimeError(errors[-2000:])
    finally:
        if process.poll() is None:
            process.kill()
    shutil.copyfile(STILLS / "10.png", DEMO / "ra_ctd_poster.png")
    frames = [Image.open(STILLS / f"{i:02d}.png").convert("RGB").resize((800, 450))
              .quantize(colors=64, method=2) for i in (0, 2, 4, 6, 7, 9, 10)]
    frames[0].save(DEMO / "ra_ctd_preview.gif", save_all=True, append_images=frames[1:],
                   duration=900, loop=0, optimize=True, disposal=2)
    print(f"{OUTPUT} ({SECONDS}s)")


if __name__ == "__main__":
    render()
