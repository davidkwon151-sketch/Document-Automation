"""Keep the Vercel adapter and published assets protected in the pytest suite."""
import json
import subprocess
from pathlib import Path


SITE = Path(__file__).resolve().parents[1] / 'share_site'


def test_vercel_gateway_authentication_and_routes():
    result = subprocess.run(['node', '--test', 'tests/vercel_gateway.test.mjs'],
                            cwd=SITE, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    config = json.loads((SITE / 'vercel.json').read_text(encoding='utf-8'))
    assert config['outputDirectory'] == 'vercel_output'
    destinations = {rule['source']: rule['destination'] for rule in config['rewrites']}
    assert '/api/:path*' in destinations
    assert '/oauth/gmail/callback' in destinations
    assert '/access/redeem' in destinations


def test_vercel_build_excludes_gateway_source_and_private_runtime():
    result = subprocess.run(['node', 'vercel-build.mjs'], cwd=SITE,
                            capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    output = SITE / 'vercel_output'
    assert (output / 'index.html').is_file()
    assert (output / 'sales.js').is_file()
    assert not (output / 'server').exists()
    assert not (output / '.openai').exists()
    assert not (output / '.env').exists()
