"""Build a user-confirmed email signature and an inline-logo EML draft."""
from email import policy
from email.message import EmailMessage
from html import escape
from io import BytesIO
import re

from PIL import Image, ImageOps, UnidentifiedImageError


FIELDS = ('name', 'title', 'company', 'phone', 'email')
PLACEHOLDER = re.compile(r'(?:\[Your name\]|\[보내는 사람 이름\])\s*$')


def signature_fields(values, *, required=True):
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise ValueError('서명 입력 항목을 확인해야 함')
    clean = {}
    for field in FIELDS:
        value = values.get(field, '')
        if not isinstance(value, str) or len(value) > 160 or any(ord(c) < 32 for c in value):
            raise ValueError('서명은 각 항목당 160자 이하의 한 줄이어야 함')
        clean[field] = value.strip()
    if required and (not clean['name'] or not clean['company']
                     or not (clean['phone'] or clean['email'])):
        raise ValueError('서명에 이름·회사명·전화 또는 이메일이 필요함')
    if clean['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', clean['email']):
        raise ValueError('서명 이메일 주소 형식을 확인해야 함')
    return clean


def normalized_image(data, *, max_bytes=2_000_000, max_pixels=12_000_000, max_edge=1600):
    """Re-encode PNG/JPEG without metadata; reject oversized/decompression images."""
    if not isinstance(data, bytes) or not 0 < len(data) <= max_bytes:
        raise ValueError('PNG/JPEG 이미지는 허용된 크기 이내이어야 함')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG'} or image.width * image.height > max_pixels:
                raise ValueError('PNG/JPEG 이미지의 크기를 확인해야 함')
            image.load()
            image = ImageOps.exif_transpose(image)
            image = image.convert('RGBA' if image.mode in {'RGBA', 'LA'} or
                                  'transparency' in image.info else 'RGB')
            image.thumbnail((max_edge, max_edge))
            output = BytesIO()
            image.save(output, format='PNG', optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('읽을 수 없는 PNG/JPEG 이미지임') from exc
    result = output.getvalue()
    if len(result) > max_bytes:
        raise ValueError('변환한 이미지가 허용 크기를 초과함')
    return result


def card_candidate(image, client):
    """A model proposes visible card text; the user must inspect every field."""
    response = client.read_image_json(normalized_image(image, max_bytes=5_000_000),
                                      mime='image/png', prompt_name='email_signature_card')
    if not isinstance(response, dict):
        raise ValueError('명함 읽기 결과가 올바르지 않음')
    return signature_fields(response, required=False)


def signed_body(email, fields):
    fields = signature_fields(fields)
    if not isinstance(email, str) or not email.strip():
        raise ValueError('회신 초안이 필요함')
    body = PLACEHOLDER.sub('', email).rstrip()
    signature = '\n'.join(value for value in (fields['name'], fields['title'], fields['company'],
                                              fields['phone'], fields['email']) if value)
    return body + '\n' + signature


def signed_eml(subject, email, fields, logo=None):
    """Return an unsent RFC 822 draft; CID keeps the logo attached to the HTML part."""
    fields = signature_fields(fields)
    if not isinstance(subject, str) or len(subject) > 300:
        raise ValueError('회신 제목을 확인해야 함')
    logo = normalized_image(logo) if logo is not None else None
    body = PLACEHOLDER.sub('', email).rstrip()
    plain = signed_body(email, fields)
    lines = ''.join(f'<div>{escape(value)}</div>' for value in
                    (fields['name'], fields['title'], fields['company'], fields['phone'], fields['email']) if value)
    logo_html = ('<div><img src="cid:company-logo@document-agent" alt="회사 로고" '
                 'style="max-width:160px;max-height:64px"></div>' if logo else '')
    html = ('<!doctype html><html><body><div style="white-space:pre-wrap">'
            + escape(body) + '</div><div style="margin-top:14px">' + lines + logo_html
            + '</div></body></html>')
    message = EmailMessage()
    message['Subject'] = re.sub(r'[\r\n\x00-\x1f]', ' ', subject)
    message['X-Unsent'] = '1'
    message.set_content(plain)
    message.add_alternative(html, subtype='html')
    if logo:
        message.get_payload()[-1].add_related(logo, maintype='image', subtype='png',
                                              cid='<company-logo@document-agent>')
    return message.as_bytes(policy=policy.SMTP)
