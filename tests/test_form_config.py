from pathlib import Path
from copy import deepcopy

import pytest

from app.form_config import mapping_rows, configure_profile
from templates import analyze_template
from agent.pipeline import build_downloads
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def test_blank_company_forms_use_explicit_selected_mapping():
    profile = analyze_template(ROOT / 'samples' / 'sample_company_form.docx')
    rows = mapping_rows(profile)
    configured, mapping = configure_profile(profile, rows)
    assert {field['value_key'] for field in configured['fields']} == {'제목', '요약', '본문'}
    assert mapping and profile != configured
    rows[0]['채울 값'] = '없는\n항목'
    with pytest.raises(ValueError):
        configure_profile(profile, rows)


def test_required_mapping_and_unregistered_input_are_rejected():
    profile = analyze_template(ROOT / 'templates' / 'result_report.docx')
    rows = mapping_rows(profile)
    rows[0]['채울 값'] = ''
    with pytest.raises(ValueError, match='필수'):
        configure_profile(profile, rows)
    rows[0]['입력칸 ID'] = 'bad'
    with pytest.raises(ValueError):
        configure_profile(profile, rows)


def test_company_xlsx_export_can_keep_citations_in_sidecar_without_printing_ids(tmp_path):
    template = ROOT / 'samples' / 'sample_company_form.xlsx'
    profile, mapping = configure_profile(analyze_template(template), mapping_rows(analyze_template(template)))
    profile['citation_mode'] = 'sidecar'
    result = {'status': 'ready', 'draft': {'제목': '실적 보고서', '요약': '□ 매출 120만원임 [S1]', '본문': '○ 매출 120만원임 [S1]'},
              'sources': [{'source_id': 'S1', 'text': '매출 120만원임'}], 'template_profile': profile}
    output = build_downloads(result, confirmed=True, template_paths={'xlsx': template}, template_profiles={'xlsx': profile}, mappings={'xlsx': mapping})
    path = tmp_path / '보고서.xlsx'
    path.write_bytes(output['xlsx'])
    workbook = load_workbook(path, data_only=False)
    try:
        text = '\n'.join(str(cell.value) for sheet in workbook for row in sheet for cell in row if cell.value is not None)
        assert '120만원' in text and '[S1]' not in text
        assert any(cell.data_type == 'f' for sheet in workbook for row in sheet for cell in row)
    finally:
        workbook.close()
    assert '[S1]' in result['draft']['본문']
    edited = deepcopy(result)
    edited['draft']['본문'] = ''
    with pytest.raises(ValueError):
        build_downloads(edited, confirmed=True, template_paths={'xlsx': template})
