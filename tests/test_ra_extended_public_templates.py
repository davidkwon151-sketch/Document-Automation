"""Official external forms: selected work fields, never a full submission."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject
import pytest

from agent.output_check import verify_output
from parsers import parse_file
from templates import TemplateError, fill_compatible_template, load_form_profile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT/'evals/ra_extended_public_templates.json').read_text(encoding='utf-8'))
ENTRIES = MANIFEST['documents']


def assets(entry):
    source = ROOT/entry['source_path']
    if not source.is_file(): pytest.skip('Official public PDF corpus is not present')
    profile = load_form_profile(source, entry['profile_id'])
    assert profile is not None
    return source, profile


def widget_map(reader):
    result = {}
    for page in reader.pages:
        for reference in page.get('/Annots', []):
            node = reference.get_object()
            if node.get('/Subtype') != '/Widget': continue
            names, ancestor = [], node
            while ancestor is not None:
                if '/T' in ancestor: names.insert(0, str(ancestor['/T']))
                parent = ancestor.get('/Parent')
                ancestor = parent.get_object() if parent else None
            result['.'.join(names)] = node
    return result


@pytest.mark.parametrize('entry', ENTRIES, ids=lambda e: e['id'])
def test_official_sources_full_parse_sha_binding_and_private_scope(entry):
    source, profile = assets(entry); before = source.read_bytes(); reader = PdfReader(source)
    assert sha256(before).hexdigest() == entry['sha256'] == profile['source_sha256']
    assert len(reader.pages) == entry['page_count']
    assert len(reader.get_fields() or {}) == entry['canonical_field_count']
    parsed = parse_file(source)
    assert len(parsed['페이지/시트 정보']) == len(reader.pages)
    def normalize(text):
        for a,b in [('ﬁ','fi'),('ﬂ','fl'),('ﬀ','ff'),('ﬃ','ffi'),('ﬄ','ffl')]: text=text.replace(a,b)
        return re.sub(r'\s+', '', text)
    for number, (block, page) in enumerate(zip(parsed['페이지/시트 정보'], reader.pages), 1):
        if 'rtp' in entry['id'] and number == 1:
            # A source ffi glyph has no reliable Unicode mapping. Record this
            # known guidance-page difference, rather than certify whole text.
            assert '(cid:431)' in block['본문'] and 'Ư' in page.extract_text()
        else:
            assert Counter(normalize(block['본문'])) == Counter(normalize(page.extract_text() or ''))
    assert profile['schema_version'] == 1 and profile['citation_mode'] == 'sidecar'
    assert profile['company_internal'] is profile['submission_ready'] is profile['current_version_verified'] is False
    assert len(profile['fields']) == entry['registered_field_count']
    assert not {'demo_values', 'qa_values', 'test_values'} & profile.keys()
    names = {f['original_field_name'] for f in profile['fields']}
    assert not any('signature' in name.lower() or 'approval' in name.lower() for name in names)
    for field in profile['fields']:
        native = reader.get_fields()[field['original_field_name']]
        assert field['required'] == bool(int(native.get('/Ff', 0)) & 2)
        assert field['narrative_style_required'] is False and field['author_role'] == 'sender'
        assert field['input_mode'] == ('user_provided' if field['input_required'] else 'source_grounded')
    assert source.read_bytes() == before


@pytest.mark.parametrize('entry', ENTRIES, ids=lambda e: e['id'])
def test_selected_synthetic_values_saved_and_verified_with_all_other_values_untouched(entry, tmp_path):
    source, profile = assets(entry); before = source.read_bytes(); old = PdfReader(source)
    values = entry['synthetic_values']
    output = fill_compatible_template(source, values, tmp_path/'partial.pdf', profile=profile)
    check = verify_output(source, output, values, profile=profile)
    assert check['status'] == 'passed'
    new = PdfReader(output); assert len(new.pages) == len(old.pages)
    assert set(old.get_fields()) == set(new.get_fields())
    selected = {f['original_field_name']: values[f['value_key']] for f in profile['fields']}
    widgets = widget_map(new)
    for name, value in selected.items():
        assert str(new.get_fields()[name].get('/V', '')) == value
        assert widgets[name]['/AP']['/N'].get_object().get_data()
    for name in set(old.get_fields())-set(selected):
        assert str(new.get_fields()[name].get('/V', '')) == str(old.get_fields()[name].get('/V', ''))
    assert source.read_bytes() == before


@pytest.mark.parametrize('entry', ENTRIES, ids=lambda e: e['id'])
def test_wrong_source_binding_and_overlong_registered_values_do_not_destroy_files(entry, tmp_path):
    source, profile = assets(entry); original = source.read_bytes()
    output = tmp_path/'existing.pdf'; output.write_bytes(b'EXISTING USER FILE')
    wrong = deepcopy(profile); wrong['source_sha256'] = '0'*64
    with pytest.raises(TemplateError):
        fill_compatible_template(source, entry['synthetic_values'], output, profile=wrong)
    field = next(f for f in profile['fields'] if f.get('max_chars') and not f.get('validation'))
    with pytest.raises(TemplateError, match='길이|분량|제한|넘'):
        fill_compatible_template(source, {field['value_key']: 'W'*(field['max_chars']+1)}, output, profile=profile)
    assert source.read_bytes() == original and output.read_bytes() == b'EXISTING USER FILE'


def test_rtp_recipient_only_page_and_unresolved_calibri_are_not_selected():
    entry = next(e for e in ENTRIES if 'rtp' in e['id']); _, profile = assets(entry)
    assert all(f['location_evidence']['page'] == 2 for f in profile['fields'])
    assert 'pdf:Project Title' not in {f['id'] for f in profile['fields']}
    assert any('Calibri' in warning for warning in profile['warnings'])


def test_cambrex_blank_signature_print_metadata_and_native_storage_options_preserved(tmp_path):
    entry = next(e for e in ENTRIES if 'cambrex' in e['id']); source, profile = assets(entry)
    old = PdfReader(source); values = entry['synthetic_values']
    output = fill_compatible_template(source, values, tmp_path/'cambrex.pdf', profile=profile)
    new = PdfReader(output)
    assert old.get_fields()['Client Signature and Date']['/FT'] == '/Sig'
    assert '/V' not in old.get_fields()['Client Signature and Date']
    assert '/V' not in new.get_fields()['Client Signature and Date']
    assert old.get_fields()['Dropdown1']['/Opt'] == new.get_fields()['Dropdown1']['/Opt']
    assert int(old.get_fields()['Dropdown1']['/Ff']) == int(new.get_fields()['Dropdown1']['/Ff'])
    field = next(f for f in profile['fields'] if f['id'] == 'pdf:Dropdown1')
    assert field['input_required'] and not field['allow_custom']
    with pytest.raises(TemplateError):
        fill_compatible_template(source, {field['value_key']: 'FAKE OPTION'}, tmp_path/'bad.pdf', profile=profile)


def test_cambrex_public_product_identity_is_only_a_preview_not_a_submitted_sample(tmp_path):
    entry = next(e for e in ENTRIES if 'cambrex' in e['id']); source, profile = assets(entry)
    evidence = json.loads((ROOT/'evals/ra_public_sources.json').read_text(encoding='utf-8'))
    record = next(s for s in evidence['sources'] if s['id'] == 'ra-public-keppra')
    product_source = ROOT/record['path']
    if not product_source.is_file(): pytest.skip('Official EMA source is not present')
    assert sha256(product_source.read_bytes()).hexdigest() == record['sha256']
    fact = next(f for f in record['facts'] if f['field_key'] == '제품명')
    assert re.sub(r'\s+', '', fact['exact_quote']) in re.sub(r'\s+', '', PdfReader(product_source).pages[fact['page']-1].extract_text())
    values = {'제품명': fact['value']}
    output = fill_compatible_template(source, values, tmp_path/'identity.pdf', profile=profile)
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'
    assert str(PdfReader(output).get_fields()['Sample DescriptionRow1']['/V']) == fact['value']
    assert profile['submission_ready'] is False and profile['company_internal'] is False


def test_independent_verifier_rejects_moved_real_input_position(tmp_path):
    entry = next(e for e in ENTRIES if 'cambrex' in e['id']); source, profile = assets(entry)
    values = entry['synthetic_values']
    output = fill_compatible_template(source, values, tmp_path/'good.pdf', profile=profile)
    writer = PdfWriter(); writer.clone_document_from_reader(PdfReader(output))
    widget = widget_map(writer)['QtyRow1']
    widget[NameObject('/Rect')] = ArrayObject([FloatObject(float(v)+20) for v in widget['/Rect']])
    wrong = tmp_path/'moved.pdf'
    with wrong.open('wb') as stream: writer.write(stream)
    with pytest.raises(ValueError, match='위치|연결|속성'):
        verify_output(source, wrong, values, profile=profile)


def test_company_and_unique_source_counts_are_separate_and_no_real_use_is_claimed():
    assert MANIFEST['summary']['company_count'] == 2
    assert MANIFEST['summary']['unique_source_sha256'] == MANIFEST['summary']['source_count'] == 3
    assert MANIFEST['summary']['actual_model_calls'] == MANIFEST['summary']['human_kpi_observations'] == 0
    assert MANIFEST['summary']['private_internal_template_verified'] == MANIFEST['summary']['full_form_filled'] == 0
