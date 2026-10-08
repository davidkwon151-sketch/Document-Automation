from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PRESENTATION = ROOT / "docs" / "prezi" / "index.html"


def test_prezi_archive_has_all_scenes_and_local_demo_images():
    html = PRESENTATION.read_text(encoding="utf-8")
    assert [int(n) for n in re.findall(r'<section class="slide(?: active)?" id="slide-(\d+)"', html)] == list(range(10))
    for image in re.findall(r'<img src="([^"]+)"', html):
        assert (PRESENTATION.parent / image).is_file()
    assert "prezi.com/craft/room/qooL8Ebq7TjeTamKQoBjjg" in html
    assert "실제 Google 계정 연결은 설정 대기" in html
