"""Calendar dates are not three opposing quantities; facts/offsets stay intact."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent.retrieve import _conflict_tokens, chunk_documents, find_conflicts

ROOT = Path(__file__).resolve().parents[1]


def source(text, identifier='S1', **metadata):
    return {'source_id': identifier, 'text': text, **metadata}


def document(text):
    return {'파일명': '원자료.docx', '본문': text, '표 목록': [],
            '페이지/시트 정보': [{'본문': text, '표 목록': [], '위치': '본문/문단 7'}]}


@pytest.mark.parametrize('text', [
    '합성약A 보고기간 2026-01-01 ~ 2026-06-30임',
    '보고기간 2026년 1월 1일 ~ 2026년 6월 30일임',
    '보고기간 2024-02-29부터 2024-03-31까지임',
    'Reporting period 2026-01-01 to 2026-06-30.',
    '제출 마감일 2024-02-29임',
])
def test_valid_dates_and_ranges_do_not_conflict_with_their_own_components(text):
    items = [source(text)]
    before = deepcopy(items)
    assert find_conflicts(items) == []
    assert items == before
    tokens = list(_conflict_tokens(items[0], set()))
    assert tokens and all(token['key'][1] == 'calendar_date' for token in tokens)
    for token in tokens:
        assert text[token['start']:token['end']] == token['raw']


@pytest.mark.parametrize('joined', [False, True])
@pytest.mark.parametrize(('first', 'second'), [
    ('보고기간 2026-01-01 ~ 2026-06-30임', '보고기간 2026-07-01 ~ 2026-12-31임'),
    ('보고기간 2025년 1월 1일 ~ 2025년 12월 31일임', '보고기간 2026년 1월 1일 ~ 2026년 12월 31일임'),
    ('2026년 1분기 마감일 2026-03-31임', '2026년 2분기 마감일 2026-06-30임'),
    ('상반기 제출 마감일 2026-06-30임', '하반기 제출 마감일 2026-12-31임'),
    ('보고기간 2026-01-01 ~ 2026-06-30임', '시험기간 2026-01-01 ~ 2026-05-31임'),
    ('시작일 2026-01-01임', '종료일 2026-06-30임'),
])
def test_explicit_different_reporting_windows_and_calendar_fields_remain_separate(first, second, joined):
    items = [source(first + '\n\n' + second)] if joined else [source(first), source(second, 'S2')]
    assert find_conflicts(items) == []


@pytest.mark.parametrize('joined', [False, True])
@pytest.mark.parametrize(('first', 'second', 'expected'), [
    ('제출 마감일 2026-01-01임', '제출 마감일 2027-01-01임', {'2026-01-01', '2027-01-01'}),
    ('제출 마감일 2026년 1월 1일임', '제출 마감일 2026년 1월 2일임', {'2026-01-01', '2026-01-02'}),
    ('Due date 2026-01-01.', 'Due date 2026-01-02.', {'2026-01-01', '2026-01-02'}),
    ('보고기간 2026-01-01 ~ 2026-06-30임', '보고기간 2026-01-01 ~ 2026-06-29임', {'2026-06-30', '2026-06-29'}),
    ('보고기간 2026-01-01 ~ 2026-06-30임', '보고기간 2026-01-02 ~ 2026-06-30임', {'2026-01-01', '2026-01-02'}),
])
def test_same_calendar_field_changed_value_still_conflicts(first, second, expected, joined):
    items = [source(first + '\n\n' + second)] if joined else [source(first), source(second, 'S2')]
    conflicts = find_conflicts(items)
    assert conflicts and conflicts[0]['unit'] == 'calendar_date'
    assert set(conflicts[0]['values']) == expected
    assert conflicts[0]['source_ids'] == (['S1'] if joined else ['S1', 'S2'])


def test_iso_and_explicit_korean_same_date_have_one_calendar_value():
    assert not find_conflicts([source('마감일 2026-01-01임'), source('마감일 2026년 1월 1일임', 'S2')])


@pytest.mark.parametrize('text', [
    '보고기간 2026-02-30 ~ 2026-06-30임',
    '마감일 2025년 2월 29일임',
    '마감일 2026-13-01임',
    '마감일 2026-00-01임',
    '마감일 2026-01-00임',
    '마감일 0000-01-01임',
    '마감일 01/02/26임',
    '마감일 2026/01/01임',
    '마감일 2026.01.01임',
    '마감일 2026-1-1임',
    '마감일 2026-01-011임',
    '시험번호 SYN-2026-01-01임',
    '시험번호 2026-01-01-A임',
    '마감일 2026-01-01mg',
    '마감일 2026년 1월 1일mg',
    '마감일 2026-01-01명',
    '마감일 2026-01-01건',
    '마감일 2026-01-01원',
    '마감일 2026-01-01℃',
    '마감일 2026-01-01°C',
    '마감일 2026-01-01%',
    '마감일 2026-01-01µg',
    '마감일 2026-01-01μg',
    '마감일 2026-01-01 mg',
])
def test_invalid_or_unrecognised_dates_keep_all_original_numeric_observations(text):
    from agent.review import number_tokens

    actual = list(_conflict_tokens(source(text), set()))
    original = number_tokens(text)
    numeric = [token for token in actual if not token['key'][1].startswith('calendar_')]
    assert [(token['start'], token['raw'], token['key']) for token in numeric] == [
        (token['start'], token['raw'], token['key']) for token in original
        if not any(date_token['start'] <= token['start'] < date_token['end'] for date_token in actual
                   if date_token['key'][1] == 'calendar_date')]
    assert numeric


def test_reversed_reporting_window_is_not_treated_as_a_safe_disjoint_interval():
    conflicts = find_conflicts([source('보고기간 2026-06-30 ~ 2026-01-01임')])
    assert conflicts and set(conflicts[0]['values']) == {'2026-06-30', '2026-01-01'}


def test_attached_dose_unit_cannot_hide_a_numeric_observation_as_a_date():
    conflicts = find_conflicts([source('용량 2026-01-01mg'), source('용량 2mg', 'S2')])
    assert any(conflict['unit'] == 'mg' and set(conflict['values']) == {'1', '2'} for conflict in conflicts)


@pytest.mark.parametrize('suffix', ['임', '까지', '부터'])
def test_korean_date_ending_does_not_hide_outside_quantity(suffix):
    text = f'마감일 2026-01-01{suffix}; 용량 12mg임'
    tokens = list(_conflict_tokens(source(text), set()))
    assert [(str(token['key'][0]), token['key'][1]) for token in tokens] == [
        ('2026-01-01', 'calendar_date'), ('12', 'mg')]
    assert not find_conflicts([source(text)])


def test_real_m1_context_chunk_preserves_offsets_and_single_range():
    text = '제품명: 합성약A\n합성약A 보고기간 2026-01-01 ~ 2026-06-30임'
    chunks = chunk_documents([document(text)])
    before = deepcopy(chunks)
    assert find_conflicts(chunks) == []
    assert chunks == before
    assert chunks[1]['context_start'] == 10
    assert chunks[1]['context_end'] == 44
    assert chunks[1]['context_text'][10:44] == chunks[1]['text']


def test_wrapped_components_keep_only_selected_numbers_and_changed_date_is_detected():
    text = '보고기간\n2026-\n01-\n01 ~\n2026-\n06-\n30임'
    chunks = chunk_documents([document(text)])
    before = deepcopy(chunks)
    assert find_conflicts(chunks) == []
    for item in chunks:
        for token in _conflict_tokens(item, set()):
            assert token['raw'] in item['text']
            assert token['key'][1] in {'calendar_year', 'calendar_month', 'calendar_day'}
    changed = chunk_documents([document(text.replace('30임', '29임'))])
    conflicts = find_conflicts([*chunks, *changed])
    assert len(conflicts) == 1
    assert conflicts[0]['unit'] == 'calendar_day'
    assert set(conflicts[0]['values']) == {'30', '29'}
    assert chunks == before


@pytest.mark.parametrize('mutation', [
    {'context_start': 1}, {'context_start': True}, {'context_end': 999},
    {'context_text': '마감일 2026-02-01임'},
])
def test_date_context_cannot_be_forged_to_hide_a_conflict(mutation):
    text = '2026-01-01'
    context = '마감일 ' + text
    item = source(text, context_text=context, context_start=4, context_end=len(context))
    with pytest.raises(ValueError, match='문자 범위'):
        find_conflicts([{**item, **mutation}])


@pytest.mark.parametrize(('first', 'second', 'unit'), [
    ('일정 2026-01-01; 대상자 수 12명임', '일정 2026-01-01; 대상자 수 120명임', '명'),
    ('매출 120만원임', '매출 130만원임', '원'),
    ('Adults: maximum dose is 15 mg once weekly.', 'Adults: maximum dose is 10 mg once weekly.', 'mg'),
    ('실온(1~30℃)보관', '실온(1~31℃)보관', '°C'),
])
def test_calendar_handling_does_not_skip_true_quantity_conflicts(first, second, unit):
    conflicts = find_conflicts([source(first), source(second, 'S2')])
    assert conflicts and conflicts[0]['unit'] == unit


def test_source_calendar_classification_does_not_relax_ra_changed_date_check():
    from agent.ra import inspect_ra_draft

    original = source('합성약A 보고기간 2026-01-01 ~ 2026-06-30임', product_name='합성약A')
    draft = {'제목': '보고기간 확인', '요약': '○ 보고기간을 확인함 [S1]',
             '본문': '○ 합성약A 보고기간 2026-07-01 ~ 2026-12-31임 [S1]'}
    issues = inspect_ra_draft(draft, [original], profile={'ra_workflow': 'safety_management', 'ra_product_name': '합성약A'})
    assert any(issue['code'] == 'ra_date_mismatch' for issue in issues['issues'])


@pytest.mark.parametrize('manifest_name', ['ra_public_sources.json', 'ra_korean_sources.json',
                                         'ra_additional_sources.json', 'ra_extended_public_sources.json'])
def test_actual_allowed_public_sources_keep_sha_and_normal_quantity_scopes(manifest_name):
    from evals.ra_public import read_source

    manifest = json.loads((ROOT / 'evals' / manifest_name).read_text(encoding='utf-8'))
    for record in manifest['sources']:
        path = ROOT / record['path']
        if not path.is_file():
            pytest.skip('공식 스냅샷이 로컬에 없음; pytest는 다운로드하지 않음')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record['sha256']
        try:
            _, items, _ = read_source(record)
        except ValueError as exc:
            if '원자료 PDF의 텍스트 추출 허용을 확인할 수 없음' in str(exc):
                from pypdf import PdfReader
                from pypdf.constants import UserAccessPermissions

                reader = PdfReader(path)
                assert reader.is_encrypted and not reader.user_access_permissions & UserAccessPermissions.EXTRACT
                continue
            raise
        before = deepcopy(items)
        assert find_conflicts(items) == []
        assert items == before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
