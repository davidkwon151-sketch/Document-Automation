"""Check that the public snapshot shares only intentional assets and verified claims."""
import json
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "share_site" / "dist"


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references, self.ids, self.tabs, self.panels = [], [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        self.references.extend(values[key] for key in ("src", "href") if key in values)
        if values.get("role") == "tab":
            self.tabs.append(values)
        if values.get("role") == "tabpanel":
            self.panels.append(values)


def test_share_assets_and_anchors_are_complete_and_local():
    parser = References()
    parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
    assert len(parser.ids) == len(set(parser.ids))
    for reference in parser.references:
        if reference.startswith("#"):
            assert reference[1:] in parser.ids
        elif reference in {"workspace", "sales", "common"}:
            assert (SITE / (reference + '.html')).is_file()
        else:
            target = (SITE / reference).resolve()
            assert target.is_relative_to(SITE.resolve()) and target.is_file()
    assert len(parser.tabs) == len(parser.panels) == 4
    assert sum(tab["aria-selected"] == "true" for tab in parser.tabs) == 1
    for tab, panel in zip(parser.tabs, parser.panels):
        assert tab["aria-controls"] == panel["id"]
        assert panel["aria-labelledby"] == tab["id"]
        assert ("hidden" not in panel) == (tab["aria-selected"] == "true")


def test_share_claims_match_recorded_run_and_do_not_invent_kpis():
    snapshot = json.loads((SITE / "assets" / "validation.json").read_text(encoding="utf-8"))
    latest = snapshot['latest_overall_pytest']
    assert f"{latest['passed']:,}" in (SITE / 'index.html').read_text(encoding='utf-8')
    assert latest['failures'] == latest['errors'] == 0
    assert latest['passed'] >= snapshot['pytest']['passed']
    run = snapshot["pytest"]["evidence_run"]
    assert run.startswith("ra-auto-final-") and "/" not in run and "\\" not in run and ".." not in run
    audit = json.loads((ROOT / "outputs" / run / "audit.json").read_text(encoding="utf-8"))
    assert snapshot["pytest"]["passed"] == audit["passed"]
    for key in ("skipped", "failures", "errors"):
        assert snapshot["pytest"][key] == audit["counts"][key]
    assert snapshot["actual_ra_model_generation_successes_in_this_trial"] == 0
    assert snapshot["observed_human_kpi_runs"] == 0
    assert snapshot["user_edit_ratio"] is None
    assert snapshot["real_public_source_trial"]["submission_ready"] is False
    assert snapshot["ra_change_trial"]["filled_fields"] == 14
    assert snapshot["ra_change_trial"]["registered_fields"] == 18
    assert snapshot["ra_change_trial"]["data_kind"] == "synthetic"
    assert snapshot["global_trial"]["native_checked_fields"] + snapshot["global_trial"]["native_ambiguous_fields"] == 48
    assert snapshot["global_trial"]["company_official_forms"] is False


def test_share_downloads_are_original_blank_templates():
    for filename in ("ra_workflow_input_blank.csv", "ra_change_input_blank.txt"):
        assert (SITE / "assets" / filename).read_bytes() == (ROOT / "samples" / filename).read_bytes()
    assert (SITE / "assets/ctd_demo_template.docx").read_bytes() == (ROOT / "samples/ctd_demo_template.docx").read_bytes()
    assert (SITE / "assets/qos_dmf_demo_template.docx").read_bytes() == (ROOT / "samples/qos_dmf_demo_template.docx").read_bytes()


def test_public_assets_exclude_private_files_and_network_calls():
    expected = {"index.html", "styles.css", "app.js", "assets/mark.svg", "assets/validation.json", "assets/workspace.jpg", "assets/comparison.jpg", "assets/hwp.jpg", "assets/auto-workspace.png", "assets/auto-result.png", "assets/ra_workflow_input_blank.csv", "assets/ra_change_input_blank.txt", "assets/ctd_demo_template.docx", "assets/qos_dmf_demo_template.docx"}
    public = {path.relative_to(SITE).as_posix() for path in SITE.rglob("*") if path.is_file()}
    assert public == expected | {'workspace.html', 'workspace.css', 'workspace.js', 'sales.html', 'sales.css', 'sales.js', 'common.html', 'common.css', 'common.js', 'access.html', 'access.css', 'access.js', 'claude.html', 'server/index.js', '.openai/hosting.json'}
    for filename in ("index.html", "app.js", "styles.css", "assets/validation.json"):
        text = (SITE / filename).read_text(encoding="utf-8")
        for forbidden in ("OPENAI_API_KEY", "sk-proj-", "C:\\Users", "Administrator", "fetch(", "XMLHttpRequest", "WebSocket"):
            assert forbidden not in text
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "공유용 HTML 페이지" in html and "현재 PC" in html and "미측정" in html


def test_claude_guide_links_to_protected_key_mint_without_embedding_secrets():
    html = (SITE / 'claude.html').read_text(encoding='utf-8')
    assert 'href="/workspace#claude-connection"' in html
    assert 'https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site/claude-mcp' in html
    assert 'Authorization' in html and 'Bearer' in html
    assert '초대 링크' in html and 'PC' in html
    assert 'sk-proj-' not in html and 'OPENAI_API_KEY' not in html
    assert 'Bearer …' in html and 'Bearer [A-Za-z0-9_-]' not in html


def test_gemini_is_default_and_claude_is_optional_without_browser_key_storage():
    html = (SITE / 'workspace.html').read_text(encoding='utf-8')
    script = (SITE / 'workspace.js').read_text(encoding='utf-8')
    guide = (SITE / 'claude.html').read_text(encoding='utf-8')
    assert '<option value="gemini" selected>' in html
    assert '<option value="claude">' in html
    assert 'id="gemini-key" type="password"' in html
    assert "gemini_api_key:geminiKey()" in script
    assert "addEventListener('pagehide',()=>{clearMcpSecret();$('gemini-key').value='';})" in script
    assert 'localStorage' not in script and 'sessionStorage' not in script
    assert 'Gemini 기본 · Claude 선택' in guide
    assert 'GEMINI_API_KEY=' not in guide


def test_data_first_preview_matches_actual_file_qa_and_is_not_a_live_backend():
    from hashlib import sha256
    snapshot = json.loads((SITE / "assets/validation.json").read_text(encoding="utf-8"))
    trial = snapshot['ra_auto_trial']
    actual = json.loads((ROOT / 'outputs/ra-auto-696856/audit.json').read_text(encoding='utf-8'))
    assert trial['filled_fields'] == actual['target_coverage']['filled_count'] == 5
    assert trial['target_fields'] == actual['target_coverage']['target_count'] == 5
    assert trial['native_word_pages'] == actual['native']['page_count'] == 1
    assert trial['actual_model_generation'] is False and trial['submission_ready'] is False
    assert trial['actual_employee_observations'] == 0
    for filename, key in [('auto-workspace.png', 'workspace_preview_sha256'), ('auto-result.png', 'output_preview_sha256')]:
        assert sha256((SITE / 'assets' / filename).read_bytes()).hexdigest() == trial[key]
    html = (SITE / 'index.html').read_text(encoding='utf-8')
    assert '로그인 없이 누구나' in html and '실제 모델 생성은 수행하지 않았습니다' in html
    references = References()
    references.feed(html)
    assert not any('127.0.0.1' in link or 'localhost' in link for link in references.references)
    assert '<form' not in html


def test_public_and_workspace_ui_put_primary_task_before_optional_details():
    public = (SITE / 'index.html').read_text(encoding='utf-8')
    workspace = (SITE / 'workspace.html').read_text(encoding='utf-8')
    script = (SITE / 'workspace.js').read_text(encoding='utf-8')
    assert '<a class="button primary" href="workspace">RA 문서 만들기' in public
    assert '<a class="button secondary" href="sales">바이어 이메일 회신' in public
    assert public.index('id="hero-title"') < public.index('id="how"') < public.index('id="proof"')
    assert public.count('<details class="disclosure">') == 2
    assert public.index('id="proof"') < public.index('id="panel-auto"')
    assert workspace.index('id="upload-section"') < workspace.index('id="claude-connection"')
    assert '<details id="claude-connection" class="optional">' in workspace
    assert '<details id="ctd-workspace" class="optional" hidden>' in workspace
    assert '<details id="qos-workspace" class="optional" hidden>' in workspace
    assert "if(location.hash==='#claude-connection')$('claude-connection').open=true" in script


def test_sales_workspace_is_protected_and_keeps_user_key_in_request_only():
    html = (SITE / 'sales.html').read_text(encoding='utf-8')
    script = (SITE / 'sales.js').read_text(encoding='utf-8')
    worker = (ROOT / 'share_site' / 'worker.mjs').read_text(encoding='utf-8')
    assert 'id="buyer-email"' in html and 'id="sales-sources"' in html
    assert 'id="sales-gemini-key" type="password"' in html
    assert "gemini_api_key:key()" in script
    assert "sessionStorage.setItem('salesJobId'" in script
    assert "'/sales'||path==='/common')&&!user" in worker
    assert 'id="sales-trade"' in html and 'id="trade-transaction"' in html
    assert 'id="sales-writing"' in html and 'id="sales-writing" hidden' not in html
    assert 'id="sales-draft" disabled' in html and 'id="trade-propose" class="secondary" disabled' in html
    assert 'id="sales-documents-preview"' in html and 'id="sales-draft-error"' in html
    assert 'id="sales-supplement-text"' in html and 'id="sales-supplement-files"' in html
    assert "sales/jobs/'+sales.job_id+'/supplement" in script
    assert "$('sales-draft').disabled=busy||!prepared" in script
    assert '회신은 보류했습니다' not in script
    assert 'function updateGates()' in script and 'requested_documents' in script
    assert "/trade/propose'" in script and "/trade/prepare'" in script and "/trade/export'" in script
    assert 'trade-final-confirm' in html and 'tradeDirty' in script
    landing = (SITE / 'index.html').read_text(encoding='utf-8')
    assert 'id="workspaces"' in landing and '해외영업·해외사업개발' in landing
    assert '공통 보고서·기안' in landing and 'href="common"' in landing
    common = (SITE / 'common.html').read_text(encoding='utf-8')
    script = (SITE / 'common.js').read_text(encoding='utf-8')
    assert all(f'value="{form}"' in common for form in
               ('business_trip', 'meeting_minutes', 'weekly_report', 'monthly_report', 'approval'))
    assert 'id="common-sources"' in common and 'id="common-notes"' in common
    assert 'id="common-template"' in common and 'id="common-export" disabled' in common
    assert "common/jobs/'+common.job_id+'/generate" in script
    assert "common/jobs/'+common.job_id+'/review" in script
    assert "common/jobs/'+common.job_id+'/export" in script
    assert 'localStorage' not in script and 'GEMINI_API_KEY=' not in common + script
    for forbidden in ('OPENAI_API_KEY=', 'GEMINI_API_KEY=', 'sk-proj-'):
        assert forbidden not in html + script
