"""Verify practical RA guest UI behavior without a network or model key."""
from pathlib import Path
import shutil
import subprocess


def test_ra_guest_flow_and_confirmations():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    assert node, 'Node is required for frontend behavior checks'
    result = subprocess.run([node, 'tests/ra-flow.test.mjs'], cwd=root / 'share_site',
                            capture_output=True, text=True, timeout=30, encoding='utf8')
    assert result.returncode == 0, result.stderr
