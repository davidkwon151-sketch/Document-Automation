"""Preserved native formulas pass; changing their range/text/type is blocked."""
from hashlib import sha256
from zipfile import ZipFile

from lxml import etree
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
import pytest

from agent.output_check import verify_output
from templates import analyze_template, fill_compatible_template


NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
PART = 'xl/worksheets/sheet1.xml'


def fixture(tmp_path, kind):
    source = tmp_path / (kind + '.xlsx')
    workbook = Workbook()
    sheet = workbook.active
    sheet['A1'] = '입력칸'
    sheet['B1'] = ''
    sheet['E2'] = (ArrayFormula(ref='E2:E3', text='=SUM(A2:A3)') if kind == 'array'
                   else DataTableFormula(ref='E2:F3', r1='A2'))
    workbook.save(source)
    profile = analyze_template(source)
    field = next(field for field in profile['fields'] if field['id'].endswith(':B1'))
    mapping = {field['id']: '값'}
    values = {'값': '검증'}
    output = fill_compatible_template(source, values, tmp_path / 'output.xlsx',
                                      profile=profile, mapping=mapping)
    return source, output, profile, mapping, values


@pytest.mark.parametrize('kind', ['array', 'dataTable'])
def test_unchanged_native_formula_object_is_not_a_changed_value(tmp_path, kind):
    source, output, profile, mapping, values = fixture(tmp_path, kind)
    digest = sha256(source.read_bytes()).hexdigest()
    assert verify_output(source, output, values, profile=profile, mapping=mapping)['status'] == 'passed'
    assert sha256(source.read_bytes()).hexdigest() == digest
    with ZipFile(source) as before, ZipFile(output) as after:
        old = etree.fromstring(before.read(PART)).find(f'.//{{{NS}}}f')
        new = etree.fromstring(after.read(PART)).find(f'.//{{{NS}}}f')
        assert etree.tostring(old, method='c14n') == etree.tostring(new, method='c14n')


@pytest.mark.parametrize('kind', ['array', 'dataTable'])
@pytest.mark.parametrize('damage', ['text', 'range', 'type'])
def test_native_formula_damage_is_not_hidden_by_object_comparison(tmp_path, kind, damage):
    source, output, profile, mapping, values = fixture(tmp_path, kind)
    with ZipFile(output) as archive:
        parts = [(item, archive.read(item.filename)) for item in archive.infolist()]
    with ZipFile(output, 'w') as archive:
        for item, raw in parts:
            if item.filename == PART:
                root = etree.fromstring(raw)
                formula = root.find(f'.//{{{NS}}}f')
                if damage == 'text':
                    formula.text = 'SUM(A9:A10)'
                elif damage == 'range':
                    formula.set('ref', 'E2:E9')
                else:
                    formula.set('t', 'normal')
                raw = etree.tostring(root)
            archive.writestr(item, raw)
    with pytest.raises(ValueError, match='수식'):
        verify_output(source, output, values, profile=profile, mapping=mapping)
