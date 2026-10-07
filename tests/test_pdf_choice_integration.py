"""Choice input, confirmed mapping and UI preserve the user's exact decisions."""
from copy import deepcopy
import json

import pytest
from reportlab.pdfgen import canvas

from agent.template_learning import (load_learned_profile, save_learned_profile,
                                     _check_fields, _contexts)
from app.form_config import configure_profile, mapping_rows, selection_label, selection_options
from templates import analyze_template
from templates.profiles import refresh_pdf_choice_metadata
from templates.value_rules import inspect_form_values, validate_rule_profile


def selection_field(*, multiple=False, custom=False, key='구분', required=False):
    return {'id': 'pdf:test', 'kind': 'pdf_form', 'value_key': key, 'label': key,
            'control_type': 'combobox' if custom else 'choice',
            'options': ['AUTH', 'VAR', 'RENEW'],
            'choice_items': [{'value': 'AUTH', 'label': '허가'}, {'value': 'VAR', 'label': '변경'},
                             {'value': 'RENEW', 'label': '갱신'}],
            'allow_custom': custom, 'multiselect': multiple,
            **({'selection_encoding': 'json_array'} if multiple else {}),
            'pdf_choice_flags': 2097152 if multiple else 393216 if custom else 131072,
            'required': required, 'input_required': True, 'input_mode': 'user_provided',
            'max_chars': 1000, 'confidence': 1.0, 'narrative_style_required': False}


@pytest.mark.parametrize('value', ['["AUTH","RENEW"]', '["RENEW", "AUTH"] [Suser]', '["VAR"]'])
def test_multiple_values_remain_flat_strings_and_allow_exact_codes(value):
    field = selection_field(multiple=True)
    values = {'구분': value}
    before = deepcopy(values)
    assert inspect_form_values(values, {'fields': [field]}) == []
    assert values == before


@pytest.mark.parametrize('value', ['AUTH', '["허가"]', '["AUTH","AUTH"]', '["AUTH",5]',
                                  '{"value":"AUTH"}', '[]', '["UNKNOWN"]', '[true]',
                                  '["AUTH"] trailing', '["AUTH"', '["AUTH","[Sfake]"]'])
def test_bad_multiple_input_blocks_without_converting_or_guessing(value):
    issues = inspect_form_values({'구분': value}, {'fields': [selection_field(multiple=True)]})
    assert issues and all(issue['severity'] == 'error' for issue in issues)


def test_blank_multiple_is_optional_and_does_not_pick_an_option():
    field = selection_field(multiple=True)
    assert selection_options(field) == ['AUTH', 'VAR', 'RENEW']
    assert selection_label(field, 'VAR') == '변경'
    assert inspect_form_values({'구분': ''}, {'fields': [field]}) == []
    field['required'] = True
    assert inspect_form_values({'구분': ''}, {'fields': [field]})[0]['code'] == 'form_value_required'


@pytest.mark.parametrize('value', ['AUTH', '원자료 확인 요청', '품목 신규 분류 [Suser]'])
def test_editable_combo_allows_literal_one_line_user_values(value):
    assert inspect_form_values({'구분': value}, {'fields': [selection_field(custom=True)]}) == []


@pytest.mark.parametrize('value', ['first\nsecond', 'one\ttwo', {'code': 'AUTH'}, 1])
def test_editable_combo_blocks_non_string_and_control_characters(value):
    assert inspect_form_values({'구분': value}, {'fields': [selection_field(custom=True)]})


def test_one_key_cannot_mix_multiple_and_single_or_free_input():
    for other in (selection_field(), selection_field(custom=True)):
        other['id'] = 'pdf:other'
        with pytest.raises(ValueError, match='상충|혼합'):
            validate_rule_profile({'fields': [selection_field(multiple=True), other]})


def test_same_codes_with_different_meanings_cannot_share_one_input():
    first, other = selection_field(), selection_field()
    other['id'] = 'pdf:opposite'
    other['choice_items'][0]['label'] = '허가하지 않음'
    with pytest.raises(ValueError, match='상충'):
        validate_rule_profile({'fields': [first, other]})


def test_same_source_codes_and_display_contract_can_share_one_input():
    first, other = selection_field(), selection_field()
    other['id'] = 'pdf:matching'
    assert inspect_form_values({'구분': 'AUTH'}, {'fields': [first, other]}) == []


def native_pdf(tmp_path):
    path = tmp_path / 'choice-source.pdf'
    document = canvas.Canvas(str(path))
    document.drawString(30, 770, 'Choice source')
    document.acroForm.choice(name='Single', x=40, y=670, width=260, height=24,
                             options=[('Approval', 'AUTH'), ('Variation', 'VAR')], value='AUTH')
    document.acroForm.choice(name='Editable', x=40, y=610, width=260, height=24,
                             options=['Known', 'Other'], value='Known', fieldFlags='combo edit')
    document.acroForm.listbox(name='Multiple', x=40, y=430, width=260, height=140,
                              options=[('Approval', 'AUTH'), ('Variation', 'VAR'), ('Renewal', 'RENEW')],
                              value=['AUTH'], fieldFlags='multiSelect')
    document.save()
    return path


def configured_native_profile(path):
    profile = analyze_template(path)
    profile['configured'] = True
    for field in profile['fields']:
        field.update(max_chars=1000, confidence=1.0, input_mode='user_provided')
    return profile


def test_confirmed_multiple_mapping_reuses_native_metadata_and_no_values(tmp_path):
    path = native_pdf(tmp_path)
    profile = configured_native_profile(path)
    configured, mapping = configure_profile(profile, mapping_rows(profile))
    before = deepcopy(configured)
    configured['values'] = {'Multiple': '["AUTH","RENEW"]'}
    configured['answers'] = {'작성자 확인': 'PRIVATE_EMPLOYEE_NOTE_42'}
    cache = save_learned_profile(path, configured, mapping, cache_dir=tmp_path/'cache', user_confirmed=True)
    loaded = load_learned_profile(path, cache_dir=tmp_path/'cache')
    for original, restored in zip(before['fields'], loaded['fields'], strict=True):
        assert all(restored[key] == value for key, value in original.items())
        if restored['control_type'] == 'choice':
            assert restored['validation'] == {'type': 'choice', 'options': original['options']}
    cached_text = cache.read_text(encoding='utf-8')
    assert 'values' not in json.loads(cached_text)['profile'] and 'answers' not in loaded
    assert 'PRIVATE_EMPLOYEE_NOTE_42' not in cached_text
    assert configured['fields'] == before['fields']
    context = next(item for item in _contexts(path, loaded['fields']) if item['id'] == 'pdf:Multiple')
    assert context['multiselect'] is True and context['selection_encoding'] == 'json_array'
    assert context['choice_items'][0] == {'value': 'AUTH', 'label': 'Approval'}


@pytest.mark.parametrize('key,new_value', [('control_type','combobox'), ('options',['AUTH','OTHER']),
                                         ('multiselect',False), ('allow_custom',True),
                                         ('selection_encoding','comma'), ('pdf_choice_flags',131072),
                                         ('choice_items',[{'value':'AUTH','label':'Incorrect'}])])
def test_source_permissions_and_option_labels_cannot_be_relaxed_in_mapping(tmp_path, key, new_value):
    path = native_pdf(tmp_path)
    profile = configured_native_profile(path)
    field = next(item for item in profile['fields'] if item['id'] == 'pdf:Multiple')
    original = deepcopy(field)
    field[key] = new_value
    with pytest.raises(ValueError):
        _check_fields([field], [original])
    with pytest.raises(ValueError):
        save_learned_profile(path, profile, cache_dir=tmp_path/'cache', user_confirmed=True)
    assert not (tmp_path/'cache').exists()


def test_old_source_metadata_can_be_completed_but_not_decided_for_user(tmp_path):
    path = native_pdf(tmp_path)
    profile = analyze_template(path)
    original = deepcopy(profile)
    for field in profile['fields']:
        for key in ('choice_items','allow_custom','pdf_choice_flags','selection_encoding'):
            field.pop(key, None)
    refreshed = refresh_pdf_choice_metadata(path, profile)
    assert refreshed['fields'] == original['fields']
    assert all('default_value' not in field and 'value' not in field for field in refreshed['fields'])


def test_streamlit_multiple_choice_is_empty_then_serializes_selected_codes(tmp_path):
    from streamlit.testing.v1 import AppTest
    path = native_pdf(tmp_path)
    script = tmp_path / 'ui.py'
    script.write_text(
        'from pathlib import Path\nfrom app.ui import form_options\nfrom templates import analyze_template\nimport streamlit as st\n'
        f'path = Path({str(path)!r})\nprofile = analyze_template(path)\nprofile["configured"] = True\n'
        f'profile, mapping, values = form_options(path, Path({str(tmp_path/"ui-data")!r}), seed_profile=profile)\n'
        'st.json(values)\n', encoding='utf-8')
    ui = AppTest.from_file(str(script)).run()
    assert not ui.exception
    multi = ui.multiselect(key='form_input_Multiple')
    assert multi.value == []
    multi.select('RENEW').select('AUTH').run()
    assert not ui.exception
    output = json.loads(ui.json[-1].value)
    assert output['Multiple'] == '["AUTH","RENEW"]'


@pytest.mark.parametrize('saved,valid', [('["RENEW","AUTH"]', True),
                                       ('["AUTH","UNKNOWN"]', False),
                                       ('["AUTH","AUTH"]', False), ('not-json', False)])
def test_streamlit_resumed_choices_are_validated_before_reuse(tmp_path, saved, valid):
    from streamlit.testing.v1 import AppTest
    path = native_pdf(tmp_path)
    script = tmp_path / 'resume-ui.py'
    script.write_text(
        'from pathlib import Path\nfrom app.ui import form_options\nfrom templates import analyze_template\nimport streamlit as st\n'
        f'path = Path({str(path)!r})\nprofile = analyze_template(path)\nprofile["configured"] = True\n'
        f'if "seeded" not in st.session_state:\n    st.session_state["form_input_Multiple"] = {saved!r}\n    st.session_state["seeded"] = True\n'
        f'profile, mapping, values = form_options(path, Path({str(tmp_path/"ui-data")!r}), seed_profile=profile)\n'
        'st.json(values)\n', encoding='utf-8')
    ui = AppTest.from_file(str(script)).run()
    if valid:
        assert not ui.exception
        assert json.loads(ui.json[-1].value)['Multiple'] == '["AUTH","RENEW"]'
    else:
        assert ui.exception and ui.error
        assert not ui.multiselect
        ui.button(key='reset_choice_Multiple').click().run()
        assert not ui.exception
        assert ui.multiselect(key='form_input_Multiple').value == []
        assert json.loads(ui.json[-1].value)['Multiple'] == ''


@pytest.mark.parametrize('core_key', ['제목', '요약', '본문'])
def test_direct_choice_mapped_to_a_core_field_still_has_user_input(tmp_path, core_key):
    from streamlit.testing.v1 import AppTest
    path = native_pdf(tmp_path)
    script = tmp_path / 'core-ui.py'
    script.write_text(
        'from pathlib import Path\nfrom app.ui import form_options\nfrom templates import analyze_template\nimport streamlit as st\n'
        f'path = Path({str(path)!r})\nprofile = analyze_template(path)\nprofile["configured"] = True\n'
        f'next(f for f in profile["fields"] if f["id"] == "pdf:Single")["value_key"] = {core_key!r}\n'
        f'profile, mapping, values = form_options(path, Path({str(tmp_path/"ui-data")!r}), seed_profile=profile)\n'
        'st.json(values)\n', encoding='utf-8')
    ui = AppTest.from_file(str(script)).run()
    assert not ui.exception
    selected = ui.selectbox(key=f'form_input_{core_key}')
    assert selected.value == ''
    selected.select('VAR').run()
    assert not ui.exception
    assert json.loads(ui.json[-1].value)[core_key] == 'VAR'


def choice_pipeline(path, values):
    from agent.pipeline import run_pipeline
    from evals.run import MockEvaluationClient
    profile = configured_native_profile(path)
    profile['citation_mode'] = 'sidecar'
    fixture = {'mock_brief': {'목적': '신청 유형 확인', '보고 대상': '팀장', '보고서 유형': '결과보고서',
                             '마감': '', '분량': '', '부족한 정보': [], '질문': []}}
    return run_pipeline('선택한 신청 유형을 양식에 기입해줘', documents=[],
                        client=MockEvaluationClient(fixture), template_profile=profile,
                        field_values=values)


def test_pdf_choices_complete_mock_pipeline_without_model_changing_user_decisions(tmp_path):
    from agent.pipeline import build_downloads
    from pypdf import PdfReader
    path = native_pdf(tmp_path)
    result = choice_pipeline(path, {'Single': 'VAR', 'Editable': 'Specific request',
                                    'Multiple': '["AUTH","RENEW"]'})
    assert result['status'] == 'ready', result['review']
    before = deepcopy(result['draft'])
    output = build_downloads(result, template_paths={'pdf': path}, confirmed=True)
    written = tmp_path/'filled.pdf'
    written.write_bytes(output['pdf'])
    fields = PdfReader(written).get_fields()
    assert fields['Single']['/V'] == 'VAR'
    assert fields['Editable']['/V'] == 'Specific request'
    assert fields['Multiple']['/V'] == ['AUTH', 'RENEW']
    raw = next(item.get_object() for item in PdfReader(written).pages[0]['/Annots']
               if item.get_object().get('/T') == 'Multiple')
    assert raw['/I'] == [0, 2]
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert result['draft'] == before
    assert all(result['locked_fields'][key]['source_id'] in result['draft'][key]
               for key in result['locked_fields'])


def test_manual_choice_change_requires_new_user_evidence_before_download(tmp_path):
    from agent.pipeline import build_downloads
    path = native_pdf(tmp_path)
    result = choice_pipeline(path, {'Single': 'VAR', 'Editable': 'Specific request',
                                    'Multiple': '["AUTH","RENEW"]'})
    assert result['status'] == 'ready'
    result['draft']['Multiple'] = result['draft']['Multiple'].replace('RENEW', 'VAR')
    with pytest.raises(ValueError):
        build_downloads(result, template_paths={'pdf': path}, confirmed=True)
    assert 'output_verification' not in result


def test_optional_blank_choices_leave_original_selected_values_untouched(tmp_path):
    from agent.pipeline import build_downloads
    from pypdf import PdfReader
    path = native_pdf(tmp_path)
    result = choice_pipeline(path, {'Single': '', 'Editable': 'Specific request', 'Multiple': ''})
    assert result['status'] == 'ready'
    output = build_downloads(result, template_paths={'pdf': path}, confirmed=True)
    written = tmp_path/'preserved.pdf'
    written.write_bytes(output['pdf'])
    fields = PdfReader(written).get_fields()
    assert fields['Single']['/V'] == 'AUTH'
    assert fields['Multiple']['/V'] == PdfReader(path).get_fields()['Multiple']['/V']
    assert 'Single' not in result['locked_fields'] and 'Multiple' not in result['locked_fields']


def citation_literal_pdf(tmp_path):
    path = tmp_path / 'literal-choice.pdf'
    document = canvas.Canvas(str(path))
    document.drawString(30, 770, 'Synthetic user selections; no actual approval')
    choices = [('Literal [S1] display', '[S1]'), ('Literal [S1, S2] display', '[S1, S2]')]
    document.acroForm.choice(name='Single', x=40, y=670, width=300, height=24,
                             options=choices, value='[S1]')
    document.acroForm.choice(name='Editable', x=40, y=610, width=300, height=24,
                             options=choices, value='[S1]', fieldFlags='combo edit')
    document.acroForm.listbox(name='Multiple', x=40, y=430, width=300, height=140,
                              options=choices, value=['[S1]'], fieldFlags='multiSelect')
    document.save()
    return path


def literal_choice_pipeline(path, values):
    from agent.pipeline import run_pipeline
    from evals.run import MockEvaluationClient
    class LiteralClient(MockEvaluationClient):
        def generate_json(self, name, payload):
            if name == 'draft':
                identifier = payload['sources'][0]['source_id']
                return {'제목': '사용자 선택 확인', '요약': f'□ 선택 요청을 기록함 [{identifier}]',
                        '본문': f'○ 사용자 입력을 확인함 [{identifier}]',
                        'Single': '[S1, S2]', 'Editable': 'AI must not decide', 'Multiple': '["[S1, S2]"]'}
            return super().generate_json(name, payload)
    profile = configured_native_profile(path)
    profile['citation_mode'] = 'sidecar'
    fixture = {'mock_brief': {'목적': '사용자 선택 확인', '보고 대상': '팀장', '보고서 유형': '결과보고서',
                             '마감': '', '분량': '', '부족한 정보': [], '질문': []}}
    return run_pipeline('명시한 선택을 기록해줘', documents=[], client=LiteralClient(fixture),
                        template_profile=profile, field_values=values)


def test_literal_export_codes_custom_input_and_multiple_members_survive_mock_download(tmp_path):
    import hashlib
    from agent.pipeline import build_downloads
    from pypdf import PdfReader
    path = citation_literal_pdf(tmp_path)
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    requested = {'Single': '[S1]', 'Editable': '[Scustom]', 'Multiple': '["[S1]","[S1, S2]"]'}
    result = literal_choice_pipeline(path, requested)
    assert result['status'] == 'ready', result['review']
    before = deepcopy(result['draft'])
    outputs = build_downloads(result, template_paths={'pdf': path}, confirmed=True)
    output = tmp_path / 'literal-filled.pdf'
    output.write_bytes(outputs['pdf'])
    reader = PdfReader(output)
    actual = reader.get_fields()
    assert actual['Single']['/V'] == requested['Single']
    assert actual['Editable']['/V'] == requested['Editable']
    assert actual['Multiple']['/V'] == ['[S1]', '[S1, S2]']
    raw_multi = next(item.get_object() for item in reader.pages[0]['/Annots']
                     if item.get_object().get('/T') == 'Multiple')
    assert raw_multi['/I'] == [0, 1]
    assert result['output_verification']['pdf']['status'] == 'passed'
    assert result['draft'] == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_sha
    for key, value in requested.items():
        assert result['locked_fields'][key]['value'] == value
        assert result['locked_fields'][key]['source_id'] in result['draft'][key]


def test_literal_choice_manual_change_blocks_until_user_regenerates_with_new_evidence(tmp_path):
    from agent.pipeline import build_downloads
    from pypdf import PdfReader
    path = citation_literal_pdf(tmp_path)
    values = {'Single': '[S1]', 'Editable': '[Scustom]', 'Multiple': '["[S1]"]'}
    result = literal_choice_pipeline(path, values)
    assert result['status'] == 'ready', result['review']
    old_id = result['locked_fields']['Single']['source_id']
    result['draft']['Single'] = result['draft']['Single'].replace('[S1]', '[S1, S2]')
    with pytest.raises(ValueError):
        build_downloads(result, template_paths={'pdf': path}, confirmed=True)
    assert 'output_verification' not in result
    updated = literal_choice_pipeline(path, {**values, 'Single': '[S1, S2]'})
    assert updated['status'] == 'ready', updated['review']
    assert updated['locked_fields']['Single']['source_id'] != old_id
    output = tmp_path / 'literal-updated.pdf'
    output.write_bytes(build_downloads(updated, template_paths={'pdf': path}, confirmed=True)['pdf'])
    assert PdfReader(output).get_fields()['Single']['/V'] == '[S1, S2]'


def test_literal_choices_start_blank_and_only_user_selection_supplies_codes(tmp_path):
    from streamlit.testing.v1 import AppTest
    path = citation_literal_pdf(tmp_path)
    script = tmp_path / 'literal-ui.py'
    script.write_text(
        'from pathlib import Path\nfrom app.ui import form_options\nfrom templates import analyze_template\nimport streamlit as st\n'
        f'path = Path({str(path)!r})\nprofile = analyze_template(path)\nprofile["configured"] = True\n'
        f'profile, mapping, values = form_options(path, Path({str(tmp_path/"literal-ui-data")!r}), seed_profile=profile)\n'
        'st.json(values)\n', encoding='utf-8')
    ui = AppTest.from_file(str(script)).run()
    assert not ui.exception
    assert ui.selectbox(key='form_input_Single').value == ''
    assert ui.multiselect(key='form_input_Multiple').value == []
    ui.selectbox(key='form_input_Single').select('[S1]').run()
    ui.multiselect(key='form_input_Multiple').select('[S1, S2]').select('[S1]').run()
    assert not ui.exception
    output = json.loads(ui.json[-1].value)
    assert output['Single'] == '[S1]'
    assert output['Multiple'] == '["[S1]","[S1, S2]"]'


@pytest.mark.parametrize('replacement', ['', '[S1, S2]', '[Sghost]'])
def test_literal_actual_code_tampering_is_blocked_by_independent_checker(tmp_path, replacement):
    from agent.output_check import verify_output
    from agent.pipeline import build_downloads
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    path = citation_literal_pdf(tmp_path)
    values = {'Single': '[S1]', 'Editable': '[Scustom]', 'Multiple': '["[S1]"]'}
    result = literal_choice_pipeline(path, values)
    assert result['status'] == 'ready', result['review']
    original_output = tmp_path / 'literal-before-tamper.pdf'
    original_output.write_bytes(build_downloads(result, template_paths={'pdf': path}, confirmed=True)['pdf'])
    writer = PdfWriter(clone_from=PdfReader(original_output))
    for node in writer._root_object['/AcroForm']['/Fields']:
        widget = node.get_object()
        if widget.get('/T') == 'Single':
            widget[NameObject('/V')] = TextStringObject(replacement)
    tampered = tmp_path / 'literal-after-tamper.pdf'
    with tampered.open('wb') as stream:
        writer.write(stream)
    with pytest.raises(ValueError, match='canonical|선택'):
        verify_output(path, tampered, values, profile=result['template_profile'])
