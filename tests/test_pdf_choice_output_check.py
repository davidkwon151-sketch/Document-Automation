"""Choice evidence is checked against native PDF codes and rendered labels separately."""
from copy import deepcopy

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ContentStream, NameObject, NumberObject, TextStringObject
from reportlab.pdfgen import canvas

from agent.output_check import verify_output
from templates import analyze_template, fill_compatible_template


def source_pdf(tmp_path, *, multi=False, editable=False, labels=None, blanks=False, identical=False,
               empty_options=None, color=None):
    path = tmp_path / 'source.pdf'
    document = canvas.Canvas(str(path))
    document.drawString(40, 760, 'Pinned choice original')
    options = [('Alpha', 'A'), ('Beta', 'B'), ('Gamma', 'C')]
    if multi:
        document.acroForm.listbox(name='Selection', x=40, y=520, width=260, height=110,
                                  options=options, value=['A'], fieldFlags='multiSelect', fontSize=10)
    else:
        document.acroForm.choice(name='Selection', x=40, y=630, width=260, height=25,
                                 options=options, value='A', fieldFlags='combo edit' if editable else 'combo', fontSize=10)
    document.acroForm.textfield(name='Untouched', x=40, y=440, width=260, height=25,
                                value='Original other field')
    document.save()
    if labels or blanks or identical or empty_options or color:
        writer = PdfWriter(clone_from=path)
        node = writer.get_fields()['Selection'].indirect_reference.get_object()
        if labels:
            node[NameObject('/Opt')] = ArrayObject([ArrayObject([TextStringObject(code), TextStringObject(label)])
                                                   for code, label in zip(('A', 'B', 'C'), labels)])
        if identical:
            node['/Opt'][1][1] = TextStringObject('Alpha')
        if blanks:
            node['/Opt'].insert(0, TextStringObject(' '))
        if empty_options == 'absent':
            node.pop('/Opt')
        elif empty_options == 'empty':
            node[NameObject('/Opt')] = ArrayObject()
        if color:
            node[NameObject('/DA')] = TextStringObject('/Helv 10 Tf ' + color)
        with path.open('wb') as handle:
            writer.write(handle)
    return path


def filled_pdf(tmp_path, value='B', **kwargs):
    source = source_pdf(tmp_path, **kwargs)
    profile = analyze_template(source)
    values = {'Selection': value}
    output = tmp_path / 'filled.pdf'
    before = source.read_bytes()
    fill_compatible_template(source, values, output, profile=profile)
    assert source.read_bytes() == before
    return source, output, values, profile


def mutate(path, change):
    writer = PdfWriter(clone_from=path)
    node = writer.get_fields()['Selection'].indirect_reference.get_object()
    change(writer, node)
    with path.open('wb') as handle:
        writer.write(handle)


def change_ap(writer, node, change):
    appearance = node['/AP']['/N'].get_object()
    stream = ContentStream(appearance, writer)
    stream.operations = change(stream.operations)
    appearance.set_data(stream.get_data())


@pytest.mark.parametrize('value,kwargs', [
    ('B', {}), ('Custom literal', {'editable': True}),
    ('["C","A"]', {'multi': True}), ('B', {'blanks': True}),
    ('B', {'identical': True}), ('B', {'labels': ['첫째', '변경 신청', '셋째']}),
    ('["C","B"]', {'multi': True, 'blanks': True}),
    ('Custom literal', {'editable': True, 'empty_options': 'absent'}),
    ('Custom literal', {'editable': True, 'empty_options': 'empty'}),
    ('B', {'color': '.4 g'}), ('B', {'color': '.1 .3 .2 .05 k'}),
])
def test_correct_code_display_pair_and_selection_indices_pass(tmp_path, value, kwargs):
    source, output, values, profile = filled_pdf(tmp_path, value, **kwargs)
    supplied = deepcopy(values)
    report = verify_output(source, output, values, profile=profile)
    assert report['status'] == 'passed'
    assert values == supplied
    assert next(check for check in report['checks'] if check['name'] == 'pdf_choice_export_display_indices')['fields'] == 1
    node = PdfReader(output).get_fields()['Selection'].indirect_reference.get_object()
    if kwargs.get('multi'):
        assert list(node['/V']) == ['A', 'C'] if value == '["C","A"]' else list(node['/V']) == ['B', 'C']
    if kwargs.get('blanks') and not kwargs.get('multi'):
        assert list(node['/I']) == [2]


@pytest.mark.parametrize('mutation', ['wrong_code', 'display_as_code', 'wrong_index', 'missing_index', 'flags', 'options', 'rectangle'])
def test_single_canonical_or_native_metadata_mutation_blocks(tmp_path, mutation):
    source, output, values, profile = filled_pdf(tmp_path)
    def change(writer, node):
        if mutation == 'wrong_code':
            node[NameObject('/V')] = TextStringObject('C')
        elif mutation == 'display_as_code':
            node[NameObject('/V')] = TextStringObject('Beta')
        elif mutation == 'wrong_index':
            node[NameObject('/I')] = ArrayObject([NumberObject(0)])
        elif mutation == 'missing_index':
            node.pop('/I')
        elif mutation == 'flags':
            node[NameObject('/Ff')] = NumberObject(393216)
        elif mutation == 'options':
            node['/Opt'][1][1] = TextStringObject('Changed label')
        elif mutation == 'rectangle':
            node['/Rect'][0] = NumberObject(70)
    mutate(output, change)
    with pytest.raises(ValueError, match='canonical|인덱스|위치·종류·옵션'):
        verify_output(source, output, values, profile=profile)


@pytest.mark.parametrize('mutation', ['scalar', 'missing_code', 'wrong_order', 'wrong_indices'])
def test_multi_requires_full_canonical_array_and_full_native_indices(tmp_path, mutation):
    source, output, values, profile = filled_pdf(tmp_path, '["C","A"]', multi=True)
    def change(writer, node):
        if mutation == 'scalar':
            node[NameObject('/V')] = TextStringObject('["A","C"]')
        elif mutation == 'missing_code':
            node[NameObject('/V')] = ArrayObject([TextStringObject('A')])
        elif mutation == 'wrong_order':
            node[NameObject('/V')] = ArrayObject([TextStringObject('C'), TextStringObject('A')])
        else:
            node[NameObject('/I')] = ArrayObject([NumberObject(0), NumberObject(1)])
    mutate(output, change)
    with pytest.raises(ValueError, match='canonical|인덱스'):
        verify_output(source, output, values, profile=profile)


@pytest.mark.parametrize('replacement', ['A', 'Gamma', ''])
def test_correct_code_with_wrong_or_missing_display_blocks(tmp_path, replacement):
    source, output, values, profile = filled_pdf(tmp_path)
    def change(writer, node):
        change_ap(writer, node, lambda operations: [(ArrayObject([TextStringObject(replacement)]), operator)
                                                   if operator == b'Tj' else (operands, operator)
                                                   for operands, operator in operations])
    mutate(output, change)
    with pytest.raises(ValueError, match='표시 문구|표시 속성|영역 누락'):
        verify_output(source, output, values, profile=profile)


@pytest.mark.parametrize('mutation', ['highlight', 'missing_highlight', 'top_index', 'invisible', 'position'])
def test_multi_correct_codes_but_wrong_visible_selection_blocks(tmp_path, mutation):
    source, output, values, profile = filled_pdf(tmp_path, '["A","C"]', multi=True)
    def change(writer, node):
        if mutation == 'top_index':
            node[NameObject('/TI')] = NumberObject(1)
            return
        def operations(items):
            changed = []
            for operands, operator in items:
                if operator == b're' and mutation == 'highlight':
                    operands = ArrayObject([operands[0], NumberObject(0), operands[2], operands[3]])
                if operator in {b're', b'f', b'f*'} and mutation == 'missing_highlight':
                    continue
                if operator == b'Tj' and mutation == 'invisible':
                    changed.append((ArrayObject([NumberObject(3)]), b'Tr'))
                if operator == b'cm' and mutation == 'position':
                    operands = ArrayObject([NumberObject(1), NumberObject(0), NumberObject(0), NumberObject(1), NumberObject(25), NumberObject(0)])
                changed.append((operands, operator))
            return changed
        change_ap(writer, node, operations)
    mutate(output, change)
    with pytest.raises(ValueError, match='강조|표시 구간|숨기거나|표시 변환'):
        verify_output(source, output, values, profile=profile)


def test_same_label_does_not_allow_wrong_known_export_code(tmp_path):
    source, output, values, profile = filled_pdf(tmp_path, identical=True)
    mutate(output, lambda writer, node: node.update({NameObject('/V'): TextStringObject('A'),
                                                    NameObject('/I'): ArrayObject([NumberObject(0)])}))
    with pytest.raises(ValueError, match='canonical'):
        verify_output(source, output, values, profile=profile)


def test_editable_custom_must_not_retain_known_selection_index(tmp_path):
    source, output, values, profile = filled_pdf(tmp_path, 'Custom literal', editable=True)
    mutate(output, lambda writer, node: node.update({NameObject('/I'): ArrayObject([NumberObject(0)])}))
    with pytest.raises(ValueError, match='/I가 남음'):
        verify_output(source, output, values, profile=profile)


def test_unselected_control_is_not_exempt_from_native_preservation(tmp_path):
    source, output, values, profile = filled_pdf(tmp_path)
    def change(writer, node):
        writer.get_fields()['Untouched'].indirect_reference.get_object()[NameObject('/V')] = TextStringObject('Wrong other field')
    mutate(output, change)
    with pytest.raises(ValueError, match='대상 밖'):
        verify_output(source, output, values, profile=profile)


def test_empty_optional_choice_preserves_original_without_initializing(tmp_path):
    source = source_pdf(tmp_path)
    profile = analyze_template(source)
    output = tmp_path / 'empty.pdf'
    values = {'Selection': '', 'Untouched': 'New explicit other value'}
    fill_compatible_template(source, values, output, profile=profile)
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'
    assert PdfReader(output).get_fields()['Selection']['/V'] == 'A'


@pytest.mark.parametrize('mutation', ['font', 'background', 'outside_marker', 'offscreen_text', 'wrong_color', 'tiny_text'])
def test_correct_code_cannot_hide_changed_resources_or_missing_visible_text(tmp_path, mutation):
    source, output, values, profile = filled_pdf(tmp_path)
    def change(writer, node):
        appearance = node['/AP']['/N'].get_object()
        if mutation == 'font':
            original = appearance['/Resources']['/XObject']['/OriginalAppearance'].get_object()
            font = next(iter(original['/Resources']['/Font'].values())).get_object()
            font[NameObject('/BaseFont')] = NameObject('/Courier')
        elif mutation == 'background':
            original = appearance['/Resources']['/XObject']['/OriginalAppearance'].get_object()
            original.set_data(original.get_data() + b'0 0 1 rg 0 0 50 25 re f\n')
        else:
            def operations(items):
                changed = []
                for operands, operator in items:
                    if mutation == 'outside_marker' and operator == b'BMC':
                        changed.extend([(ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(25)]), b're'), ([], b'f')])
                    if mutation == 'offscreen_text' and operator == b'Tm':
                        operands = ArrayObject([*operands[:4], NumberObject(2000), NumberObject(2000)])
                    if mutation == 'wrong_color' and operator == b'rg':
                        operands = ArrayObject([NumberObject(1), NumberObject(1), NumberObject(1)])
                    if mutation == 'tiny_text' and operator == b'Tf':
                        operands = ArrayObject([operands[0], NumberObject(1)])
                    changed.append((operands, operator))
                return changed
            change_ap(writer, node, operations)
    mutate(output, change)
    with pytest.raises(ValueError, match='원본 배경|배경 자원|글꼴 자원|영역 밖|표시 영역에서 잘림|표시 속성'):
        verify_output(source, output, values, profile=profile)


def test_relaxed_profile_cannot_authorize_literal_on_original_closed_choice(tmp_path):
    source, output, values, profile = filled_pdf(tmp_path)
    field = next(field for field in profile['fields'] if field['id'] == 'pdf:Selection')
    field.update(control_type='combobox', allow_custom=True, pdf_choice_flags=393216)
    mutate(output, lambda writer, node: node.update({NameObject('/V'): TextStringObject('Not native option')}))
    with pytest.raises(ValueError, match='원본 목록 밖'):
        verify_output(source, output, {'Selection': 'Not native option'}, profile=profile)


def test_blank_placeholder_still_counts_in_native_index(tmp_path):
    source, output, values, profile = filled_pdf(tmp_path, blanks=True)
    mutate(output, lambda writer, node: node.update({NameObject('/I'): ArrayObject([NumberObject(1)])}))
    with pytest.raises(ValueError, match='인덱스'):
        verify_output(source, output, values, profile=profile)


@pytest.mark.parametrize('value,kwargs', [('B [Suser]', {}), ('["C","A"] [Suser]', {'multi': True})])
def test_expected_citations_do_not_become_choice_codes(tmp_path, value, kwargs):
    source, output, values, profile = filled_pdf(tmp_path, value, **kwargs)
    assert verify_output(source, output, values, profile=profile)['status'] == 'passed'
    assert values['Selection'] == value


def test_forged_profile_cannot_authorize_original_readonly_choice(tmp_path):
    from hashlib import sha256
    source, output, values, profile = filled_pdf(tmp_path)
    readonly = NumberObject(131073)
    for path in (source, output):
        mutate(path, lambda writer, node: node.update({NameObject('/Ff'): readonly}))
    profile['source_sha256'] = sha256(source.read_bytes()).hexdigest()
    next(field for field in profile['fields'] if field['id'] == 'pdf:Selection')['pdf_choice_flags'] = int(readonly)
    with pytest.raises(ValueError, match='읽기 전용'):
        verify_output(source, output, values, profile=profile)
