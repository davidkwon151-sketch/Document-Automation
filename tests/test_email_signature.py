from email import policy
from email.parser import BytesParser
from io import BytesIO
from unittest.mock import Mock

from PIL import Image
import pytest

from agent.email_signature import card_candidate, normalized_image, signature_fields, signed_body, signed_eml


def png():
    stream = BytesIO()
    Image.new('RGBA', (120, 40), (20, 80, 120, 255)).save(stream, format='PNG')
    return stream.getvalue()


def fields():
    return {'name': '김수진', 'title': '해외영업 팀장', 'company': '예시 주식회사',
            'phone': '+82 2 1234 5678', 'email': 'sujin@example.test'}


def test_signature_is_added_once_and_logo_is_inline_in_eml():
    original = 'Thank you for the inquiry.\n\nKind regards,\n[Your name]'
    message = BytesParser(policy=policy.default).parsebytes(
        signed_eml('Re: Quotation', original, fields(), png()))
    assert message['Subject'] == 'Re: Quotation'
    assert message['X-Unsent'] == '1'
    assert message['To'] is None and message['From'] is None
    plain = message.get_body(preferencelist=('plain',)).get_content()
    html = message.get_body(preferencelist=('html',)).get_content()
    assert '[Your name]' not in plain and plain.count('김수진') == 1
    assert '예시 주식회사' in plain and 'sujin@example.test' in plain
    assert 'cid:company-logo@document-agent' in html
    logo = next(part for part in message.walk() if part.get_content_maintype() == 'image')
    assert logo['Content-ID'] == '<company-logo@document-agent>'
    assert logo.get_payload(decode=True).startswith(b'\x89PNG')


def test_signature_escapes_html_and_rejects_incomplete_or_multiline_values():
    values = {**fields(), 'company': '<script>alert(1)</script>'}
    html = BytesParser(policy=policy.default).parsebytes(
        signed_eml('Re: Test', 'Regards,\n[Your name]', values)).get_body(
            preferencelist=('html',)).get_content()
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert signed_body('감사합니다.\n[보내는 사람 이름]', fields()).endswith('sujin@example.test')
    with pytest.raises(ValueError):
        signature_fields({**fields(), 'name': ''})
    with pytest.raises(ValueError):
        signature_fields({**fields(), 'phone': '123\nBcc: victim@example.test'})
    with pytest.raises(ValueError):
        normalized_image(b'<svg onload="alert(1)">')


def test_palette_logo_transparency_is_preserved():
    image = Image.new('P', (2, 2), 0)
    image.putpalette([0, 0, 0, 50, 100, 150] + [0] * 762)
    image.info['transparency'] = 0
    stream = BytesIO()
    image.save(stream, format='PNG')
    with Image.open(BytesIO(normalized_image(stream.getvalue()))) as normalized:
        assert normalized.mode == 'RGBA'
        assert normalized.getpixel((0, 0))[3] == 0


def test_phone_photo_orientation_is_applied_before_card_reading():
    image = Image.new('RGB', (20, 10), 'white')
    exif = image.getexif()
    exif[274] = 6
    stream = BytesIO()
    image.save(stream, format='JPEG', exif=exif)
    with Image.open(BytesIO(normalized_image(stream.getvalue()))) as normalized:
        assert normalized.size == (10, 20)


def test_card_photo_is_only_an_unconfirmed_extraction_candidate():
    client = Mock()
    client.read_image_json.return_value = fields()
    candidate = card_candidate(png(), client)
    assert candidate == fields()
    args, kwargs = client.read_image_json.call_args
    assert args[0].startswith(b'\x89PNG') and kwargs['prompt_name'] == 'email_signature_card'
    assert kwargs['mime'] == 'image/png'
    client.read_image_json.return_value = {**fields(), 'extra': 'invented'}
    with pytest.raises(ValueError):
        card_candidate(png(), client)
