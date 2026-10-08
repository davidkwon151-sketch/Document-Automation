"""Draft a buyer reply from verified company evidence without sending mail."""
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from html.parser import HTMLParser
import json
import re

from agent.multimodal_intake import validate_generation_source, validate_source_origin


# ISO-style reply codes are shared by the API and the local workspace.
# Each fallback is safe, nonfactual prose in the selected language.
REPLY_LANGUAGES = {
    'en': ('English', 'Thank you for your email.', 'Kind regards,\n[Your name]',
           'The following points need further confirmation:', 'Re: Your inquiry'),
    'ko': ('한국어', '문의해 주셔서 감사합니다.', '감사합니다.\n[보내는 사람 이름]',
           '다음 사항은 추가 확인이 필요합니다.', 'Re: 문의에 대한 답변'),
    'zh-CN': ('简体中文', '感谢您的来信。', '此致\n[Your name]',
              '以下事项需要进一步确认：', 'Re: 咨询'),
    'es': ('Español', 'Gracias por su mensaje.', 'Atentamente,\n[Your name]',
           'Los siguientes puntos requieren confirmación:', 'Re: Consulta'),
    'fr': ('Français', 'Merci pour votre message.', 'Cordialement,\n[Your name]',
           'Les points suivants nécessitent une confirmation :', 'Re: Demande de renseignements'),
    'de': ('Deutsch', 'Vielen Dank für Ihre Nachricht.', 'Mit freundlichen Grüßen\n[Your name]',
           'Die folgenden Punkte müssen noch bestätigt werden:', 'Re: Anfrage'),
    'ar': ('العربية', 'شكرًا على رسالتكم.', 'مع خالص التحية،\n[Your name]',
           'تحتاج النقاط التالية إلى تأكيد إضافي:', 'Re: استفسار'),
    'pt': ('Português', 'Agradecemos sua mensagem.', 'Atenciosamente,\n[Your name]',
           'Os pontos a seguir precisam de confirmação:', 'Re: Consulta'),
    'ja': ('日本語', 'お問い合わせいただきありがとうございます。', 'よろしくお願いいたします。\n[Your name]',
           '以下の点について、追加確認が必要です。', 'Re: お問い合わせ'),
    'he': ('עברית', 'תודה על פנייתכם.', 'בברכה,\n[Your name]',
           'הנושאים הבאים דורשים אישור נוסף:', 'Re: פנייה'),
}


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        elif tag in {'p', 'br', 'div', 'li'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_buyer_email(text='', *, eml=None):
    """Accept pasted message or an RFC 822 email; never load remote content."""
    if eml is not None:
        if not isinstance(eml, bytes) or not 0 < len(eml) <= 2_000_000:
            raise ValueError('EML 파일은 2 MB 이하이어야 함')
        message = BytesParser(policy=policy.default).parsebytes(eml)
        plain, html = [], []
        for part in message.walk():
            if part.is_multipart() or part.get_content_disposition() == 'attachment':
                continue
            if part.get_content_type() not in {'text/plain', 'text/html'}:
                continue
            try:
                content = part.get_content()
            except (UnicodeError, ValueError, LookupError):
                continue
            if isinstance(content, str):
                (plain if part.get_content_type() == 'text/plain' else html).append(content)
        text = '\n'.join(plain)
        if not text and html:
            parser = _TextHTML()
            parser.feed('\n'.join(html))
            text = ''.join(parser.parts)
        subject = str(message.get('Subject', '')).strip()
        sender = str(message.get('From', '')).strip()
    else:
        subject = sender = ''
    if not isinstance(text, str):
        raise ValueError('받은 이메일 본문은 텍스트이어야 함')
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not 3 <= len(text) <= 30_000:
        raise ValueError('받은 이메일 본문은 3~30,000자이어야 함')
    return {'subject': subject[:300], 'sender': sender[:300], 'body': text,
            'email_sha256': sha256(text.encode('utf-8')).hexdigest()}


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _numbers(text):
    return re.findall(r'(?<![\w])\d[\d,]*(?:\.\d+)?(?:\s*(?:%|USD|EUR|KRW|kg|g|mg|mL|L|pcs|days?))?', text, re.I)


def _safe_general(answer):
    """Without seller evidence, only a clarification question enters the reply."""
    return (answer.endswith(('?', '？', '؟')) and not _numbers(answer)
            and not re.search(r'[.!](?:\s|$)', answer)
            and not re.search(r'\b(?:we|our team|I)\s+(?:can|will|have|are|am|shall|could)\b',
                              answer, re.I))


def _documents(email, requests):
    haystack = '\n'.join([email['body'], *(item['buyer_quote'] for item in requests)]).casefold()
    names = []
    for words, name in ((('quotation', 'quote', 'proforma', '견적'), 'proforma_invoice'),
                        (('commercial invoice', '상업송장'), 'commercial_invoice'),
                        (('packing list', '포장명세'), 'packing_list')):
        if any(word in haystack for word in words):
            names.append(name)
    return names


def requested_documents(email):
    """Show explicit document keywords before any model request."""
    if not isinstance(email, dict) or not isinstance(email.get('body'), str):
        raise ValueError('받은 이메일 본문이 필요함')
    return _documents(email, [])


def _user_notes(notes):
    if not isinstance(notes, list) or len(notes) > 10:
        raise ValueError('담당자 추가 정보는 10개 이하이어야 함')
    result = []
    for number, note in enumerate(notes, 1):
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 20_000:
            raise ValueError('담당자 추가 정보는 비어 있지 않은 20,000자 이하 텍스트이어야 함')
        value = note.strip()
        digest = sha256(value.encode('utf-8')).hexdigest()
        result.append({'source_id': f'U{number}_{digest[:12]}', 'filename': '담당자 직접 입력',
                       'page': None, 'sheet': None, 'location': f'추가 정보 {number}',
                       'text': value, 'context_text': value, 'document_sha256': digest,
                       'origin': 'user_input'})
    return result


def draft_buyer_reply(email, sources, client, *, language='en', tone='professional',
                      user_notes=None, progress=None):
    """Two model passes: compose bound answers, then review coverage and meaning.

    The independent review must cover every item. Unsupported answers are held
    out of the reply. The returned evidence is a sidecar, never a sent email.
    """
    if (not isinstance(language, str) or language not in REPLY_LANGUAGES
            or not isinstance(tone, str) or tone not in {'professional', 'warm', 'concise'}
            or not isinstance(email, dict) or not email.get('body')):
        raise ValueError('받은 이메일과 답변 언어를 확인해야 함')
    if not isinstance(sources, list) or len(sources) > 400:
        raise ValueError('확인된 원자료가 너무 많거나 형식이 잘못됨')
    source_map = {}
    for source in sources:
        validate_source_origin(source)
        validate_generation_source(source)
        identifier = source.get('source_id')
        if not isinstance(identifier, str) or identifier in source_map:
            raise ValueError('중복되거나 없는 원자료 출처 ID')
        source_map[identifier] = source
    for note in _user_notes(user_notes or []):
        if note['source_id'] in source_map:
            raise ValueError('담당자 추가 정보 출처 ID가 중복됨')
        source_map[note['source_id']] = note
    clean_sources = [{key: source.get(key) for key in ('source_id', 'filename', 'page', 'sheet',
                      'location', 'text', 'context_text', 'document_sha256', 'origin')}
                     for source in source_map.values()]
    if progress:
        progress('composing')
    response = client.generate_json('buyer_email', {'email': email, 'sources': clean_sources,
                                                     'language': language, 'tone': tone})
    raw_items = response.get('requests') if isinstance(response, dict) else None
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 20:
        raise ValueError('바이어 요청을 1~20개 항목으로 분석해야 함')
    items, seen = [], set()
    for number, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            raise ValueError('요청 항목 형식이 잘못됨')
        quote, answer = item.get('buyer_quote'), item.get('answer')
        evidence = item.get('evidence', [])
        if (not isinstance(quote, str) or not quote.strip() or quote not in email['body']
                or quote in seen or not isinstance(answer, str) or len(answer) > 1200
                or not isinstance(evidence, list) or len(evidence) > 5):
            raise ValueError('요청 원문·답변·출처 범위를 대조할 수 없음')
        seen.add(quote)
        bound = []
        for proof in evidence:
            if not isinstance(proof, dict):
                raise ValueError('출처 연결 형식이 잘못됨')
            source = source_map.get(proof.get('source_id'))
            exact = proof.get('quote')
            if not source or not isinstance(exact, str) or not exact.strip() or exact not in source['text']:
                raise ValueError('답변 인용이 확인된 원문과 일치하지 않음')
            bound.append({'source_id': source['source_id'], 'quote': exact,
                          'filename': source['filename'], 'page': source.get('page'),
                          'sheet': source.get('sheet'), 'location': source.get('location'),
                          'document_sha256': source['document_sha256'],
                          'origin': source.get('origin', 'file')})
        answer = answer.strip()
        if not bound and answer and not _safe_general(answer):
            answer = ''
        if answer and any(token not in ('\n'.join(p['quote'] for p in bound) or quote)
                                  for token in _numbers(answer)):
            raise ValueError('답변 수치가 연결한 원자료 인용에 없음')
        items.append({'index': number, 'buyer_quote': quote, 'answer': answer,
                      'evidence': bound, 'status': ('draft' if bound else 'general')
                      if answer else 'needs_confirmation'})
    if progress:
        progress('matching', [{'quote': item['buyer_quote'][:500],
                               'source_count': len(item['evidence'])} for item in items])
        progress('reviewing')
    review = client.generate_json('buyer_email_review', {'email': email, 'requests': items,
                                                          'sources': clean_sources, 'language': language,
                                                          'tone': tone,
                                                          'opening': response.get('opening', ''),
                                                          'closing': response.get('closing', '')})
    if not isinstance(review, dict) or type(review.get('complete')) is not bool:
        raise ValueError('독립 검수의 요청 완결성 결과가 없음')
    verdicts = review.get('items')
    if (not isinstance(verdicts, list) or len(verdicts) != len(items)
            or {v.get('index') for v in verdicts if isinstance(v, dict)} != set(range(1, len(items)+1))
            or any(type(v.get('supported')) is not bool for v in verdicts if isinstance(v, dict))):
        raise ValueError('독립 검수에서 요청 항목이 누락·중복됨')
    verdict = {v['index']: v for v in verdicts}
    for item in items:
        if item['status'] in {'draft', 'general'} and not verdict[item['index']]['supported']:
            item['status'] = 'needs_confirmation'
            item['answer'] = ''
        if item['status'] == 'needs_confirmation':
            question = verdict[item['index']].get('safe_question')
            if isinstance(question, str) and _safe_general(question.strip()):
                item['answer'] = question.strip()
                item['status'] = 'general'
    missing = review.get('missing_requests', [])
    if not isinstance(missing, list):
        missing = []
    for quote in missing[:20]:
        if (isinstance(quote, str) and 0 < len(quote) <= 500
                and quote in email['body'] and quote not in seen):
            seen.add(quote)
            items.append({'index': len(items) + 1, 'buyer_quote': quote, 'answer': '',
                          'evidence': [], 'status': 'needs_confirmation'})
    pending = [item for item in items if item['status'] == 'needs_confirmation']
    _, intro_default, close_default, pending_heading, subject_default = REPLY_LANGUAGES[language]
    prose_ok = review.get('email_prose_supported') is True
    opening = response.get('opening') if prose_ok else None
    closing = response.get('closing') if prose_ok else None
    opening = opening.strip() if isinstance(opening, str) and 0 < len(opening.strip()) <= 250 else intro_default
    closing = closing.strip() if isinstance(closing, str) and 0 < len(closing.strip()) <= 250 else close_default
    if (_numbers(opening + '\n' + closing)
            or re.search(r'\bour\s+(?:\w+\s+){0,2}products?\b', opening, re.I)):
        opening, closing = intro_default, close_default
    lines = [opening, '']
    answers = [item['answer'] for item in items if item['status'] in {'draft', 'general'}]
    if answers:
        lines += ['\n\n'.join(answers)]
    if pending:
        lines += ['', pending_heading]
        lines += ['- ' + item['buyer_quote'] for item in pending]
    lines += ['', closing]
    subject = ('Re: ' + email['subject']) if email['subject'] and not email['subject'].casefold().startswith('re:') else (email['subject'] or subject_default)
    if progress:
        progress('assembled')
    return {'status': 'review_required', 'email': '\n'.join(lines), 'subject': subject,
            'language': language, 'tone': tone,
            'requests': items, 'documents': _documents(email, items),
            'review': {'complete': review['complete'], 'pending_count': len(pending),
                       'coverage_check_required': not review['complete'],
                       'message': '발송 전 담당자가 거래조건·원문·수신인을 확인해야 함'},
            'fingerprint': _fingerprint({'email': email, 'sources': clean_sources, 'requests': items})}
