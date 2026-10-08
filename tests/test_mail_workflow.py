"""Incoming mail needs one reviewed snapshot and one gateway send claim."""

from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from hashlib import sha256

import pytest

from agent.buyer_email import draft_buyer_reply, parse_buyer_email
from agent.mail_workflow import MailWorkflow


def incoming(sender='buyer@example.test'):
    return {'sender': sender, 'subject': 'Delivery question',
            'body': 'What is the delivery time for Widget A?',
            'email_sha256': 'source-body-hash', 'thread_id': 'gmail-thread-7',
            'message_id': '<original@example.test>', 'references': '<earlier@example.test>'}


def compose(_):
    return {'status': 'review_required', 'fingerprint': 'verified-draft-hash',
            'subject': 'Re: Delivery question',
            'email': 'Could you share your target delivery date?\nRegards,\nSeller',
            'review': {'complete': True, 'pending_count': 0}}


def workflow(tmp_path, composer=compose):
    sender = Mock(return_value='outgoing-123')
    return MailWorkflow(tmp_path / 'mail.sqlite3', composer, sender), sender


def send(work, row, **changes):
    values = {'expected_revision': row['revision'], 'fingerprint': row['fingerprint'],
              'confirmed_recipient': row['recipient'], 'confirmed_subject': row['subject'],
              'confirmed_body': row['body'], 'facts_confirmed': True}
    values.update(changes)
    return work.confirm_and_send('owner-a', 'mailbox-a', 'incoming-1', **values)


def test_duplicate_receipt_and_user_mailbox_isolation(tmp_path):
    composer = Mock(side_effect=compose)
    work, gateway = workflow(tmp_path, composer)
    first = work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    assert first['state'] == 'review_required' and first['revision'] == 1
    assert first['draft']['fingerprint'] == 'verified-draft-hash'
    assert first['incoming']['thread_id'] == 'gmail-thread-7'
    assert first['incoming']['message_id'] == '<original@example.test>'
    assert first['incoming']['body_sha256'] == sha256(incoming()['body'].encode()).hexdigest()
    assert work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming()) == first
    composer.assert_called_once()
    with pytest.raises(ValueError, match='다른 원문'):
        work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming('other@example.test'))
    with pytest.raises(KeyError):
        work.get('owner-b', 'mailbox-a', 'incoming-1')
    with pytest.raises(KeyError):
        work.get('owner-a', 'mailbox-b', 'incoming-1')
    assert work.list_inbox('owner-b', 'mailbox-a') == []
    assert len(work.list_inbox('owner-a', 'mailbox-a')) == 1
    gateway.assert_not_called()


def test_edit_invalidates_old_approval_and_sends_exact_new_snapshot_once(tmp_path):
    work, gateway = workflow(tmp_path)
    first = work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    edited = work.edit('owner-a', 'mailbox-a', 'incoming-1',
                       expected_revision=first['revision'], recipient='buyer@example.test',
                       subject=first['subject'], body='Please confirm the target date.\nSeller')
    assert edited['revision'] == 2 and edited['fingerprint'] != first['fingerprint']
    with pytest.raises(ValueError, match='확인한 초안'):
        send(work, first)
    with pytest.raises(ValueError, match='확인한 초안'):
        send(work, edited, confirmed_body=first['body'])
    with pytest.raises(ValueError, match='확인한 초안'):
        send(work, edited, confirmed_recipient='stranger@example.test')
    with pytest.raises(ValueError, match='근거'):
        send(work, edited, facts_confirmed=False)
    gateway.assert_not_called()
    result = send(work, edited)
    assert result['state'] == 'sent' and result['provider_message_id'] == 'outgoing-123'
    assert gateway.call_count == 1
    kwargs = gateway.call_args.kwargs
    assert kwargs['mailbox_id'] == 'mailbox-a' and kwargs['incoming_id'] == 'incoming-1'
    assert kwargs['incoming']['thread_id'] == 'gmail-thread-7'
    assert kwargs['incoming']['references'] == '<earlier@example.test>'
    assert kwargs['recipient'] == 'buyer@example.test'
    assert kwargs['subject'] == edited['subject'] and kwargs['body'] == edited['body']
    assert len(kwargs['idempotency_key']) == 64
    with pytest.raises(ValueError, match='확인한 초안'):
        send(work, edited)
    gateway.assert_called_once()
    restarted = MailWorkflow(tmp_path / 'mail.sqlite3', compose, gateway)
    assert restarted.get('owner-a', 'mailbox-a', 'incoming-1')['state'] == 'sent'


def test_unknown_send_result_never_retries_automatically(tmp_path):
    work, gateway = workflow(tmp_path)
    row = work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    gateway.side_effect = TimeoutError('provider timeout')
    with pytest.raises(TimeoutError):
        send(work, row)
    assert work.get('owner-a', 'mailbox-a', 'incoming-1')['state'] == 'send_uncertain'
    with pytest.raises(ValueError, match='확인한 초안'):
        send(work, row)
    gateway.assert_called_once()


def test_two_workers_cannot_send_the_same_approval(tmp_path):
    entered, release = Event(), Event()

    def gateway(**_):
        entered.set()
        assert release.wait(5)
        return 'outgoing-123'

    work = MailWorkflow(tmp_path / 'mail.sqlite3', compose, gateway)
    row = work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, work, row)
        assert entered.wait(5)
        with pytest.raises(ValueError, match='확인한 초안'):
            send(work, row)
        release.set()
        assert first.result(timeout=5)['state'] == 'sent'


def test_draft_failure_can_retry_and_header_or_signature_risk_blocks_send(tmp_path):
    composer = Mock(side_effect=[RuntimeError('model unavailable'), compose(incoming())])
    work, gateway = workflow(tmp_path, composer)
    with pytest.raises(RuntimeError):
        work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    assert work.get('owner-a', 'mailbox-a', 'incoming-1')['state'] == 'draft_failed'
    row = work.receive('owner-a', 'mailbox-a', 'incoming-1', incoming())
    assert row['state'] == 'review_required' and composer.call_count == 2
    with pytest.raises(ValueError, match='제목'):
        work.edit('owner-a', 'mailbox-a', 'incoming-1', expected_revision=row['revision'],
                  recipient=row['recipient'], subject='Re: Hi\nBcc: attacker@example.test',
                  body=row['body'])
    with pytest.raises(ValueError, match='수신인'):
        work.edit('owner-a', 'mailbox-a', 'incoming-1', expected_revision=row['revision'],
                  recipient='buyer@example.test, attacker@example.test',
                  subject=row['subject'], body=row['body'])
    with_signature = work.edit('owner-a', 'mailbox-a', 'incoming-1',
                               expected_revision=row['revision'], recipient=row['recipient'],
                               subject=row['subject'], body='Thanks.\n[Your name]')
    with pytest.raises(ValueError, match='서명'):
        send(work, with_signature)
    gateway.assert_not_called()


def test_existing_grounding_discards_unsupported_seller_promise(tmp_path):
    email = parse_buyer_email(incoming()['body'])
    email.update(sender='buyer@example.test', subject='Delivery question')
    client = Mock()
    client.generate_json.side_effect = [
        {'requests': [{'buyer_quote': email['body'],
                       'answer': 'We will deliver Widget A tomorrow.', 'evidence': []}]},
        {'complete': True, 'items': [{'index': 1, 'supported': True}]},
    ]
    composer = lambda message: draft_buyer_reply(message, [], client)
    work, gateway = workflow(tmp_path, composer)
    row = work.receive('owner-a', 'mailbox-a', 'incoming-1', email)
    assert 'deliver Widget A tomorrow' not in row['body']
    assert row['review']['pending_count'] == 1
    gateway.assert_not_called()
