"""The downloadable example is synthetic, parsed, filled, and re-read end to end."""

from pathlib import Path

from agent.ctd import prepare_ctd_package
from agent.ctd_template import fill_ctd_working_template
from agent.multimodal_intake import collect_multimodal, generation_sources
from parsers import parse_file


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'samples'
SECTION_IDS = ['3.2.P.3.2', '3.2.P.5.4', '3.2.P.8.3']


def test_synthetic_ctd_demo_fills_selected_sections_without_original_changes(tmp_path):
    paths = [DEMO / name for name in ('ctd_demo_batch_formula.txt',
                                     'ctd_demo_batch_analysis.txt',
                                     'ctd_demo_stability.txt')]
    template = DEMO / 'ctd_demo_template.docx'
    before = template.read_bytes()
    sources = generation_sources(collect_multimodal(paths))
    package = prepare_ctd_package(sources, product_name='예시정',
                                  product_variant='정제 5 mg',
                                  selected_sections=SECTION_IDS)
    assert [section['status'] for section in package['sections']] == ['proposed'] * 3
    output = fill_ctd_working_template(template, tmp_path / 'filled.docx', package,
                                       sources=sources, product_name='예시정',
                                       product_variant='정제 5 mg')
    assert output['output_check']['status'] == 'passed'
    assert template.read_bytes() == before
    text = parse_file(output['output_path'])['본문']
    assert 'DEMO-B01' in text and '98.7%' in text and '98.2%' in text
    assert all('{{' + section + '}}' not in text for section in SECTION_IDS)
    assert not output['submission_ready']
