"""The public RA video must match its synthetic, checked document output."""

from hashlib import sha256
import json
from pathlib import Path

from PIL import Image

from docs.demo_ra.record import SECTIONS, SOURCES, checked_output
from docs.demo_ra.render import SECONDS, TIMELINE


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo_ra"


def test_ra_video_is_short_and_uses_checked_synthetic_output():
    assert SECONDS == sum(item[1] for item in TIMELINE) == 55 < 60
    assert (DEMO / "ra_ctd_short_demo.mp4").stat().st_size > 100_000
    with Image.open(DEMO / "ra_ctd_preview.gif") as preview:
        assert preview.size == (800, 450)
        assert preview.n_frames >= 6
    proof = json.loads((DEMO / "provenance.json").read_text(encoding="utf-8"))
    assert proof["mode"] == "deterministic exact-source excerpt filling; no LLM call"
    assert proof["sections"] == list(SECTIONS)
    assert proof["coverage"] == "3/3 selected sections"
    assert proof["template_sha256"] == sha256(
        (ROOT / "samples" / "ctd_demo_template.docx").read_bytes()).hexdigest()
    assert proof["source_sha256"] == {
        name: sha256((ROOT / "samples" / name).read_bytes()).hexdigest() for name in SOURCES}
    output = checked_output((DEMO / "CTD_demo_output.zip").read_bytes())
    assert output["output_sha256"] == proof["output_sha256"]
    assert proof["submission_ready"] is False
    assert len(list((DEMO / "capture").glob("[0-9][0-9].png"))) == 9
    page = DEMO / "capture" / "output_page.png"
    page_proof = json.loads((DEMO / "capture" / "output_page.json").read_text(encoding="utf-8"))
    assert page_proof["output_sha256"] == proof["output_sha256"]
    assert page_proof["image_sha256"] == sha256(page.read_bytes()).hexdigest()
    with Image.open(page) as rendered_page:
        assert rendered_page.width > 1000 and rendered_page.height > 1000
