"""Keep the public demo input and illustrative output internally consistent."""

from email import policy
from email.parser import BytesParser
from pathlib import Path

from docs.demo_sales.render import HEIGHT, WIDTH, frame


ROOT = Path(__file__).resolve().parents[1] / "docs" / "demo_sales"


def test_demo_email_reply_and_video_frames():
    message = BytesParser(policy=policy.default).parsebytes(
        (ROOT / "sample_buyer_email.eml").read_bytes()
    )
    request = message.get_content()
    reply = (ROOT / "sample_reply_draft.txt").read_text(encoding="utf-8")
    assert message["From"].addresses[0].domain == "example.com"
    assert "500 standard office chairs" in request
    assert "500 office chairs" in reply
    assert "Dallas" in request and "Dallas" in reply
    assert "verify pricing, lead time, and delivery terms" in reply
    assert "NOT SENT" in reply
    assert frame(1).size == frame(28).size == (WIDTH, HEIGHT)
    assert (ROOT / "buyer_email_demo.mp4").stat().st_size > 100_000
