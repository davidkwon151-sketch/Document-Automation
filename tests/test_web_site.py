"""Exercise the real Worker trust boundary and frontend upload invalidation."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_site_worker_security_contract():
    node = shutil.which('node')
    assert node, 'Node is required to verify the deployed gateway'
    result = subprocess.run([node, 'tests/worker.test.mjs'], cwd=ROOT / 'share_site', capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

def test_external_ui_has_no_secrets_and_invalidates_changed_attachments():
    site = ROOT / 'share_site'
    script = (site / 'dist/workspace.js').read_text(encoding='utf8')
    assert "['template','sources']" in script and 'uploadDirty=true;invalidate()' in script
    assert 'function requireSaved(){if(uploadDirty)' in script
    assert 'lockInputs(true)' in script and 'lockInputs(false)' in script
    assert "document.querySelectorAll('input,textarea,select')" in script
    for name in ('workspace.html','workspace.css','workspace.js'):
        text = (site / 'dist' / name).read_text(encoding='utf8')
        assert 'sk-proj-' not in text and 'RA_GATEWAY_SECRET' not in text
    assert 'innerHTML' not in script
    assert 'confirmed_keys:Object.keys(proposal.source_bindings' in script
