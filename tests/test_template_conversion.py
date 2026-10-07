from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from app import template_conversion as conversion
from parsers import hancom

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hwp_form(tmp_path, monkeypatch):
    source = tmp_path / '신청서.hwp'
    shutil.copyfile(ROOT / 'data/public_templates/ra/ra_law_form_23_20260305.hwp', source)
    calls = []

    def convert(path, output_dir):
        calls.append(path)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / (path.stem + '.hwpx')
        shutil.copyfile(ROOT / 'samples/sample_company_form.hwpx', target)
        return target

    monkeypatch.setattr(conversion, 'convert_legacy', convert)
    return source, calls


def test_hwp_form_uses_hwpx_and_reuses_only_bound_cache(hwp_form):
    source, calls = hwp_form
    before = source.read_bytes()
    output = conversion.prepare_form_template(source)
    assert output.suffix == '.hwpx'
    assert conversion.prepare_form_template(source) == output
    assert len(calls) == 1 and source.read_bytes() == before
    receipt = json.loads(output.with_suffix('.hwpx.conversion.json').read_text(encoding='utf-8'))
    assert receipt['source_sha256'] == sha256(before).hexdigest()
    assert receipt['output_sha256'] == sha256(output.read_bytes()).hexdigest()
    assert set(receipt) == {'source_sha256', 'output_sha256', 'source_format', 'output_format'}


@pytest.mark.parametrize('mutation', ['source', 'output', 'receipt', 'missing'])
def test_stale_hwp_conversion_is_blocked_without_reconverting(hwp_form, mutation):
    source, calls = hwp_form
    output = conversion.prepare_form_template(source)
    receipt = output.with_suffix('.hwpx.conversion.json')
    if mutation == 'source':
        with source.open('ab') as stream:
            stream.write(b'source changed')
    elif mutation == 'output':
        with output.open('ab') as stream:
            stream.write(b'output changed')
    elif mutation == 'receipt':
        receipt.write_text('{}', encoding='utf-8')
    else:
        receipt.unlink()
    with pytest.raises(ValueError, match='캐시'):
        conversion.prepare_form_template(source)
    assert len(calls) == 1


def test_cached_hwpx_is_revalidated_even_with_matching_hash(hwp_form):
    source, _ = hwp_form
    output = conversion.prepare_form_template(source)
    output.write_bytes(b'not HWPX')
    receipt = output.with_suffix('.hwpx.conversion.json')
    binding = json.loads(receipt.read_text(encoding='utf-8'))
    binding['output_sha256'] = sha256(output.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(binding), encoding='utf-8')
    with pytest.raises(ValueError, match='캐시'):
        conversion.prepare_form_template(source)


def test_hwp_protection_is_checked_before_cache_reuse(hwp_form, monkeypatch):
    source, calls = hwp_form
    conversion.prepare_form_template(source)

    def block(path):
        raise ValueError('protected document')

    monkeypatch.setattr(hancom, '_validate_hwp', block)
    with pytest.raises(ValueError, match='protected'):
        conversion.prepare_form_template(source)
    assert len(calls) == 1


def test_modern_form_never_invokes_legacy_converter(tmp_path, monkeypatch):
    source = tmp_path / 'form.hwpx'
    monkeypatch.setattr(conversion, 'convert_legacy', lambda *a, **k: pytest.fail('unexpected conversion'))
    assert conversion.prepare_form_template(source) == source
