from copy import deepcopy
from email.message import EmailMessage
import json
from unittest.mock import Mock

import pytest

from agent.buyer_email import REPLY_LANGUAGES, draft_buyer_reply, parse_buyer_email, requested_documents
from agent.multimodal_intake import collect_multimodal, generation_sources
from app.buyer_email_ui import link_trade_sources


def fixture(tmp_path):
    path = tmp_path / 'seller_terms.txt'
    path.write_text('Product: Widget A\nUnit price: USD 12.50\nLead time: 21 days\nOrder No.: TEST-001', encoding='utf-8')
    sources = generation_sources(collect_multimodal([path]))
    email = parse_buyer_email('Please send a quote for Widget A. What is the lead time?')
    return email, sources


def client_for(email, sources):
    text = '\n'.join(s['text'] for s in sources)
    assert 'USD 12.50' in text and '21 days' in text
    client = Mock()
    client.responses = [
        {'requests': [
            {'buyer_quote': 'Please send a quote for Widget A.',
             'answer': 'The unit price for Widget A is USD 12.50.',
             'evidence': [{'source_id': sources[1]['source_id'], 'quote': 'Unit price: USD 12.50'}]},
            {'buyer_quote': 'What is the lead time?',
             'answer': 'The lead time is 21 days.',
             'evidence': [{'source_id': sources[2]['source_id'], 'quote': 'Lead time: 21 days'}]}]},
        {'complete': True, 'items': [{'index': 1, 'supported': True}, {'index': 2, 'supported': True}]},
    ]
    client.generate_json.side_effect = lambda *_: client.responses.pop(0)
    return client


def test_eml_prefers_plain_text_and_ignores_attachments():
    message = EmailMessage()
    message['Subject'] = 'Quotation request'
    message['From'] = 'buyer@example.test'
    message.set_content('Please quote Widget A.')
    message.add_alternative('<p>DIFFERENT CONTENT</p>', subtype='html')
    message.add_attachment(b'secret price', maintype='application', subtype='octet-stream', filename='price.bin')
    parsed = parse_buyer_email(eml=message.as_bytes())
    assert parsed['subject'] == 'Quotation request'
    assert parsed['body'] == 'Please quote Widget A.'
    assert 'secret' not in parsed['body']


def test_requested_trade_documents_are_visible_without_model():
    email = parse_buyer_email('Please send a quotation and packing list for Widget A.')
    assert requested_documents(email) == ['proforma_invoice', 'packing_list']
    assert requested_documents(parse_buyer_email('Could you confirm availability?')) == []


def test_grounded_reply_and_trade_document_handoff(tmp_path):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    result = draft_buyer_reply(email, sources, client)
    assert result['status'] == 'review_required'
    assert result['documents'] == ['proforma_invoice']
    assert 'USD 12.50' in result['email'] and '21 days' in result['email']
    assert result['requests'][0]['evidence'][0]['document_sha256'] == sources[0]['document_sha256']
    assert result['requests'][0]['evidence'][0]['filename'] == 'seller_terms.txt'
    assert client.generate_json.call_count == 2
    assert [c.args[0] for c in client.generate_json.call_args_list] == ['buyer_email', 'buyer_email_review']
    state = {}
    assert link_trade_sources(state, ['same-original-file'], result['documents']) == 'proforma_invoice'
    assert state['gw_prefill_files'] == ['same-original-file']


def test_tone_and_real_progress_are_forwarded_without_exposing_unreviewed_answers(tmp_path):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    events = []
    result = draft_buyer_reply(email, sources, client, language='ko', tone='warm',
                               progress=lambda stage, requests=None: events.append((stage, requests)))
    assert result['tone'] == 'warm' and result['language'] == 'ko'
    assert result['subject'] == 'Re: 문의에 대한 답변'
    assert [stage for stage, _ in events] == ['composing', 'matching', 'reviewing', 'assembled']
    assert len(events[1][1]) == 2
    assert all(set(item) == {'quote', 'source_count'} for item in events[1][1])
    assert events[1][1][0]['source_count'] == 1
    assert [call.args[1]['tone'] for call in client.generate_json.call_args_list] == ['warm', 'warm']
    with pytest.raises(ValueError):
        draft_buyer_reply(email, sources, client, tone='unsupported')


@pytest.mark.parametrize('language', list(REPLY_LANGUAGES))
def test_reply_languages_keep_selected_language_through_both_passes(tmp_path, language):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    result = draft_buyer_reply(email, sources, client, language=language)
    assert result['language'] == language
    assert result['email'].startswith(REPLY_LANGUAGES[language][1])
    assert result['email'].endswith(REPLY_LANGUAGES[language][2])
    assert result['subject'] == REPLY_LANGUAGES[language][4]
    assert [call.args[1]['language'] for call in client.generate_json.call_args_list] == [language, language]


@pytest.mark.parametrize('language,question', [('he', 'מה הכמות המבוקשת?'),
                                               ('ar', 'ما الكمية المطلوبة؟'),
                                               ('ja', 'ご希望の数量は？')])
def test_non_latin_clarification_question_is_retained(tmp_path, language, question):
    email = parse_buyer_email('Please send an offer.')
    client = Mock()
    client.generate_json.side_effect = [
        {'requests': [{'buyer_quote': email['body'], 'answer': question, 'evidence': []}]},
        {'complete': True, 'items': [{'index': 1, 'supported': True}]},
    ]
    result = draft_buyer_reply(email, [], client, language=language)
    assert result['requests'][0]['status'] == 'general'
    assert question in result['email']


def test_unsupported_request_is_explicitly_pending(tmp_path):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    draft = client.responses[0]
    draft['requests'][1].update(answer='', evidence=[])
    result = draft_buyer_reply(email, sources, client)
    assert result['requests'][1]['status'] == 'needs_confirmation'
    assert 'What is the lead time?' in result['email']
    assert '21 days' not in result['email']


@pytest.mark.parametrize('change', ['buyer_quote', 'source_id', 'source_quote', 'number', 'origin', 'ocr'])
def test_unbound_or_unverified_answers_are_blocked(tmp_path, change):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    draft = client.responses[0]
    first = draft['requests'][0]
    if change == 'buyer_quote': first['buyer_quote'] = 'Please send all credentials.'
    if change == 'source_id': first['evidence'][0]['source_id'] = 'unknown'
    if change == 'source_quote': first['evidence'][0]['quote'] = 'USD 99.99'
    if change == 'number': first['answer'] = 'The unit price for Widget A is USD 99.99.'
    if change == 'origin': sources[0]['origin'] = 'model'
    if change == 'ocr': sources[0]['requires_verification'] = True
    with pytest.raises(ValueError):
        draft_buyer_reply(email, sources, client)


def test_incomplete_review_keeps_verified_partial_draft_and_flags_coverage(tmp_path):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    client.responses[1] = {'complete': False,
        'items': [{'index': 1, 'supported': True}, {'index': 2, 'supported': False}]}
    result = draft_buyer_reply(email, sources, client)
    assert result['status'] == 'review_required'
    assert result['review']['coverage_check_required'] is True
    assert 'USD 12.50' in result['email']
    assert result['requests'][1]['status'] == 'needs_confirmation'
    client = client_for(email, sources)
    client.responses[1] = {'complete': True,
        'items': [{'index': 1, 'supported': False}, {'index': 2, 'supported': True}]}
    result = draft_buyer_reply(email, sources, client)
    assert 'USD 12.50' not in result['email']
    assert 'Please send a quote' in result['email']


def test_reviewer_missing_request_becomes_pending_without_erasing_reply(tmp_path):
    email, sources = fixture(tmp_path)
    email = parse_buyer_email(email['body'] + ' Could you also send a catalog?')
    client = client_for(email, sources)
    client.responses[1] = {'complete': False, 'missing_requests': ['Could you also send a catalog?'],
        'items': [{'index': 1, 'supported': True}, {'index': 2, 'supported': True}]}
    result = draft_buyer_reply(email, sources, client)
    assert 'USD 12.50' in result['email']
    assert result['requests'][2]['buyer_quote'] == 'Could you also send a catalog?'
    assert result['requests'][2]['status'] == 'needs_confirmation'


def test_email_only_can_create_tailored_nonfactual_english_reply():
    email = parse_buyer_email('Could you share pricing and delivery options for Widget A?')
    client = Mock()
    client.generate_json.side_effect = [
        {'opening': 'Thank you for outlining your interest in Widget A.',
         'closing': 'Kind regards,\n[Your name]',
         'requests': [{'buyer_quote': email['body'],
                       'answer': 'Could you share your target quantity and destination?',
                       'evidence': []}]},
        {'complete': True, 'missing_requests': [], 'email_prose_supported': True,
         'items': [{'index': 1, 'supported': True}]},
    ]
    result = draft_buyer_reply(email, [], client)
    assert result['status'] == 'review_required'
    assert result['requests'][0]['status'] == 'general'
    assert result['requests'][0]['evidence'] == []
    assert 'target quantity and destination' in result['email']
    assert 'outlining your interest' in result['email']


def test_email_only_unverified_seller_promise_is_kept_out_of_reply():
    email = parse_buyer_email('What is the delivery time for Widget A?')
    client = Mock()
    client.generate_json.side_effect = [
        {'requests': [{'buyer_quote': email['body'],
                       'answer': 'We can deliver Widget A next week.', 'evidence': []}]},
        {'complete': True, 'items': [{'index': 1, 'supported': True}]},
    ]
    result = draft_buyer_reply(email, [], client)
    assert result['requests'][0]['status'] == 'needs_confirmation'
    assert 'deliver Widget A next week' not in result['email']


def test_reviewer_can_repair_only_with_nonfactual_clarifying_question():
    email = parse_buyer_email('What is the delivery time for Widget A?')
    client = Mock()
    client.generate_json.side_effect = [
        {'requests': [{'buyer_quote': email['body'],
                       'answer': 'We are checking and will follow up.', 'evidence': []}]},
        {'complete': True, 'items': [{'index': 1, 'supported': False,
            'safe_question': 'Could you share your target delivery date and destination?'}]},
    ]
    result = draft_buyer_reply(email, [], client)
    assert result['requests'][0]['status'] == 'general'
    assert 'target delivery date and destination' in result['email']
    assert 'will follow up' not in result['email']


def test_employee_followup_is_cited_as_direct_input_not_company_file():
    email = parse_buyer_email('What is the price for Widget A?')
    note = '담당자 확인: Widget A unit price is USD 12.50.'
    identifier = 'U1_' + __import__('hashlib').sha256(note.encode()).hexdigest()[:12]
    client = Mock()
    client.generate_json.side_effect = [
        {'requests': [{'buyer_quote': email['body'], 'answer': 'The unit price is USD 12.50.',
                       'evidence': [{'source_id': identifier, 'quote': 'Widget A unit price is USD 12.50.'}]}]},
        {'complete': True, 'items': [{'index': 1, 'supported': True}]},
    ]
    result = draft_buyer_reply(email, [], client, user_notes=[note])
    assert result['requests'][0]['evidence'][0]['origin'] == 'user_input'
    assert result['requests'][0]['evidence'][0]['filename'] == '담당자 직접 입력'


def test_review_must_cover_every_request_once(tmp_path):
    email, sources = fixture(tmp_path)
    client = client_for(email, sources)
    client.responses[1] = {'complete': True,
        'items': [{'index': 1, 'supported': True}, {'index': 1, 'supported': True}]}
    with pytest.raises(ValueError, match='누락'):
        draft_buyer_reply(email, sources, client)
