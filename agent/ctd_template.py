"""Fill a reviewed CTD working template without printing citation markers."""

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

from lxml import etree

from agent.ctd import prepare_ctd_package
from agent.output_check import verify_output
from templates.compatibility import analyze_template, fill_compatible_template, _parts
from templates.fill import PLACEHOLDER, _segments


def _placeholders(path, kind):
    tokens = []
    with ZipFile(path) as archive:
        for part in _parts(archive, kind):
            root = etree.fromstring(archive.read(part))
            for paragraph in root.xpath(".//*[local-name()='p']"):
                text = ''.join(segment.value for segment in _segments(paragraph, kind))
                tokens.extend(match.group(0) for match in PLACEHOLDER.finditer(text))
    return tokens


def _untouched_blank_paragraph(field, kind):
    """A layout spacer is not a CTD input; leave it exactly as found."""
    return (field.get('kind') == kind + '_paragraph'
            and not field.get('required') and not field.get('input_required')
            and not field.get('control_type')
            and str(field.get('label', '')).startswith('빈 본문 문단 '))


def ctd_template_section_ids(profile):
    """Return only explicit CTD placeholders; leave layout spacers untouched."""
    kind = profile.get('format')
    fields = profile.get('fields') or []
    if (kind not in {'docx', 'hwpx'} or not profile.get('supported') or not fields
            or any((field['kind'] != 'placeholder'
                    and not _untouched_blank_paragraph(field, kind))
                   or field.get('input_required') or field.get('control_type')
                   for field in fields)):
        raise ValueError('절 자리표시자 외의 보호·선택·직접 입력칸은 자동 출력하지 않음')
    return [field['label'] for field in fields if field['kind'] == 'placeholder']


def fill_ctd_working_template(template_path, output_path, ctd_package, *, sources,
                              product_name, product_variant='', section_map=None,
                              confirmed_substance_links=None, confirmed_dmf_links=None):
    """Write a CTD working DOCX/HWPX and a separate source-evidence JSON.

    Recomputes the package against the current confirmed source records and
    product/section selections. The output is a review draft, not an eCTD or
    a regulatory suitability decision.
    """
    source, target = Path(template_path), Path(output_path)
    kind = source.suffix.lower().lstrip('.')
    sidecar = Path(str(target) + '.sources.json')
    if (kind not in {'docx', 'hwpx'} or target.suffix.lower() != source.suffix.lower()
            or source.resolve() == target.resolve() or target.exists() or sidecar.exists()):
        raise ValueError('새 DOCX/HWPX 출력 경로가 필요하며 원본·기존 결과는 덮어쓰지 않음')
    if (not isinstance(ctd_package, dict) or not isinstance(ctd_package.get('sections'), list)
            or not ctd_package['sections']):
        raise ValueError('현재 CTD 절별 작업 초안이 필요함')
    selected = [section.get('section_id') for section in ctd_package['sections']]
    kind_name = ctd_package.get('document_kind')
    if kind_name == 'ctd_module_2_3_s_dmf_working_draft':
        from agent.ctd_qos import prepare_qos_package
        current = prepare_qos_package(
            sources, product_name=product_name, product_variant=product_variant,
            selected_sections=selected, section_map=section_map,
            dmf_links=confirmed_dmf_links)
    else:
        current = prepare_ctd_package(sources, product_name=product_name,
                                      product_variant=product_variant,
                                      section_map=section_map,
                                      selected_sections=selected,
                                      confirmed_substance_links=confirmed_substance_links)
    if ctd_package != current:
        raise ValueError('원문·제품·양식 선택 또는 절 매핑이 바뀜; CTD 초안을 다시 작성해야 함')
    profile = analyze_template(source)
    if not profile['supported'] or profile['format'] != kind:
        raise ValueError('현재 DOCX/HWPX 양식의 입력칸을 안전하게 분석할 수 없음')
    fields = profile['fields']
    section_ids = ctd_template_section_ids(profile)
    tokens = _placeholders(source, kind)
    rendered = [token[2:-2] for token in tokens]
    if (not tokens or len(tokens) != len(set(tokens)) or set(rendered) - set(selected)
            or set(rendered) != set(section_ids)
            or any(token != '{{' + token[2:-2] + '}}' for token in tokens)):
        raise ValueError('양식의 정확한 CTD 절 자리표시자와 선택 절이 일치해야 함')
    sections = {section['section_id']: section for section in current['sections']}
    values = {}
    evidence = {}
    for section_id in rendered:
        section = sections[section_id]
        if (section['status'] not in {'proposed', 'manual_check'}
                or not section['draft'] or not section['evidence']):
            raise ValueError('양식에 있는 절의 누락·상충·미확인 원문으로 출력을 보류함')
        records = section['evidence']
        expected = '\n'.join(item['quote'] + ' [' + item['source_id'] + ']' for item in records)
        if section['draft'] != expected:
            raise ValueError('절 초안과 현재 원문 인용이 달라 출력할 수 없음')
        # Rebuild from exact quotes. A literal [S1] in the source is data and
        # must never be removed by a citation-stripping regex.
        values[section_id] = '\n'.join(item['quote'] for item in records)
        evidence[section_id] = records
    for field in fields:
        field['narrative_style_required'] = False  # CTD fixed section excerpts keep original wording.

    descriptor, temp_name = tempfile.mkstemp(prefix='ctd-', suffix=source.suffix,
                                             dir=target.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    wrote_output = False
    wrote_sidecar = False
    try:
        fill_compatible_template(source, values, temporary, profile=profile)
        checked = verify_output(source, temporary, values, profile=profile)
        if checked['status'] != 'passed':
            raise ValueError('저장 후 원위치·서식 검수를 통과하지 못함')
        if sha256(source.read_bytes()).hexdigest() != profile['source_sha256']:
            raise ValueError('검수 중 양식 원본이 변경됨')
        payload = {'document_kind': current['document_kind'],
                   'submission_ready': False, 'package_fingerprint': current['fingerprint'],
                   'template_sha256': checked['sha']['template'],
                   'output_sha256': checked['sha']['output'],
                   'selected_sections': selected, 'rendered_sections': rendered,
                   'missing_sections': current['missing_sections'],
                   'ambiguous_sections': current['ambiguous_sections'],
                   'deferred_sections': current['deferred_sections'],
                   'coverage': current['coverage'], 'evidence': evidence,
                   'manual_checks': current['manual_checks'], 'output_check': checked}
        if kind_name == 'ctd_module_2_3_s_dmf_working_draft':
            payload['dmf_links'] = current['dmf_links']
            payload['identity_evidence'] = current['identity_evidence']
            payload['summary_method'] = current['summary_method']
        with target.open('xb') as stream:
            wrote_output = True
            stream.write(temporary.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar.open('x', encoding='utf-8') as stream:
            wrote_sidecar = True
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return {'output_path': target, 'evidence_path': sidecar,
                'output_check': checked, 'template_sha256': checked['sha']['template'],
                'output_sha256': checked['sha']['output'], 'submission_ready': False}
    except Exception:
        if wrote_output:
            target.unlink(missing_ok=True)
        if wrote_sidecar:
            sidecar.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
