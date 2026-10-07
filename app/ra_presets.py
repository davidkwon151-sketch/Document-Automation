"""Internal RA examples; no official submission or fabricated facts.

The caller supplies the project root and selects one output format before
generation. Source-bound reviewed profiles cannot be swapped across formats.
Existing brief/draft/ra/review prompts and report writers are reused.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from templates import analyze_template


_PRESETS = (
    {
        'id': 'ra_internal_review', 'title': 'RA 내부 검토 보고서',
        'document_kind': 'report', 'domain': 'pharmaceutical_ra',
        'ra_workflow': 'product_approval', 'report_type': '결과보고서',
        'templates': {
            'docx': ('generic_document.docx', 'ec2db86686f4db2af475159e4516a225a6c590d92b67473d8de2c19296da969e'),
            'hwpx': ('generic_document.hwpx', '1f4721cbf8bfdb49dbe0bb3c2b811f7f5e4954733a88d88b5baa10c2d372e0db'),
        },
        'instruction': ('RA 내부 검토 결과보고서를 작성해 주세요. 결론을 먼저 제시하고, '
            '검토 대상과 자료 버전, 확인 사실과 출처, 부족한 자료, 후속 조치를 본문에 구분해 주세요. '
            '제품·제형·용량·적용 조건을 원자료대로 유지하고, 근거가 없는 사항은 추가 확인 필요로 표시해 주세요.'),
        'source_requirements': ('검토 대상 제품·제형의 원문 자료', '자료 버전·관할·확인 범위'),
    },
    {
        'id': 'ra_change_impact', 'title': 'RA 변경 영향 검토 기안',
        'document_kind': 'report', 'domain': 'pharmaceutical_ra',
        'ra_workflow': 'variation', 'report_type': '품의서',
        'templates': {
            'docx': ('approval_request.docx', '240d0a2613221afdbc8164cce2e90993655c9220d88552948419b13d40b753c8'),
            'hwpx': ('approval_request.hwpx', '7082159a1fbd228495158fc99d2e4ebff15e0910e5d6e575fb82f65011e353f3'),
        },
        'instruction': ('RA 변경 영향 검토 품의서를 작성해 주세요. 요청 사항을 먼저 제시하고, '
            '원자료의 변경 전후 상태와 버전, 변경 사유, 영향과 근거, 추가 확인 사항을 구분해 주세요. '
            '변경 전후 자료가 없으면 실제 변경·허가 필요성·영향 없음·승인 완료를 만들어 쓰지 마세요. '
            '담당자·일정·결재 요청은 사용자가 명시한 정보만 사용해 주세요.'),
        'source_requirements': ('변경 전·후 원자료와 각 버전', '명시된 변경 사유·검토 대상'),
    },
)
NOTICE = '프로젝트 제작 내부 검토 예제임. 공식 제출 양식·실제 회사 내부 양식·법적 적합 인증이 아님.'


def list_ra_presets():
    return deepcopy(list(_PRESETS))


def prepare_ra_preset(preset_id, *, format='docx', root):
    """Read and bind a known template, preserving its original three slots."""
    spec = next((deepcopy(item) for item in _PRESETS if item['id'] == preset_id), None)
    if spec is None or format not in ('docx', 'hwpx'):
        raise ValueError('등록된 RA 내부 예제와 DOCX/HWPX 형식을 선택해야 함')
    filename, expected = spec['templates'][format]
    path = Path(root).resolve() / 'templates' / filename
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('RA 내부 예제 원본 SHA가 등록된 양식과 다름')
    profile = analyze_template(path)
    if (not profile.get('supported') or profile.get('format') != format
            or {field['value_key'] for field in profile['fields']} != {'제목', '요약', '본문'}
            or any(field['kind'] != 'placeholder' for field in profile['fields'])):
        raise ValueError('RA 내부 예제의 원본 입력 위치를 확인할 수 없음')
    profile.update(domain=spec['domain'], document_kind=spec['document_kind'],
                   ra_workflow=spec['ra_workflow'], preset_id=spec['id'],
                   template_origin='project_example', is_official_submission_form=False,
                   citation_mode='inline', constraints={'summary_max_lines': 3})
    spec.update(notice=NOTICE, format=format)
    return path, profile, spec


def apply_ra_preset(state, preset_id, *, format='docx', root):
    """Apply only before input widgets render; this always starts a new document."""
    path, profile, spec = prepare_ra_preset(preset_id, format=format, root=root)
    attachment_epoch = state.get('attachment_epoch', 0) + 1
    template_epoch = state.get('custom_template_epoch', 0) + 1
    transient = {'result', 'exports', 'answers', 'active_run', 'instruction', 'attachments',
                 'custom_template', 'request_fingerprint', 'resume_template_profile',
                 'resume_input_paths', 'rejection_reason', 'public_template_path',
                 'public_template_provenance', 'document_cache', 'displayed_questions',
                 'export_template', 'native_download_context', 'reset_draft_widgets',
                 'business_workflow', 'office_workflow'}
    prefixes = ('draft_', 'answer_', 'form_input_', 'verify_', 'repeat_', 'mapping_',
                'citation_mode_', 'pdf_regions_', 'value_rules_', 'value_relations_',
                'value_groups_', 'attachments_', 'custom_template_', 'learned_profile_')
    for key in list(state):
        if key in transient or key.startswith(prefixes):
            state.pop(key, None)
    # Explicit False before checkbox creation prevents a browser-resubmitted True.
    state.update(confirmed=False, instruction=spec['instruction'], document_kind='report',
                 work_domain=spec['domain'], ra_workflow=spec['ra_workflow'],
                 template_choice=path.name, resume_template_profile=profile,
                 attachment_epoch=attachment_epoch, custom_template_epoch=template_epoch,
                 ra_preset_id=spec['id'], ra_preset_format=format)
    return path, profile, spec
