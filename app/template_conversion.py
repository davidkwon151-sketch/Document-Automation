"""Prepare legacy form uploads, binding cached HWPX to the original HWP."""

from hashlib import sha256
import json
from pathlib import Path
import os
import tempfile

from parsers.legacy import TARGETS, convert_legacy


def prepare_form_template(path):
    source = Path(path)
    suffix = {**TARGETS, '.hwp': '.hwpx'}.get(source.suffix.lower())
    if suffix is None:
        return source
    output = source.parent / 'converted' / (source.stem + suffix)
    if source.suffix.lower() != '.hwp':
        return output if output.is_file() else convert_legacy(source, output_dir=output.parent)
    from parsers.hancom import _validate_hwp, _validate_hwpx
    _validate_hwp(source)
    original_sha = sha256(source.read_bytes()).hexdigest()
    receipt = output.with_suffix('.hwpx.conversion.json')
    if output.is_file():
        try:
            binding = json.loads(receipt.read_text(encoding='utf-8'))
            if binding != {'source_sha256': original_sha, 'output_sha256': sha256(output.read_bytes()).hexdigest(),
                           'source_format': 'hwp', 'output_format': 'hwpx'}:
                raise ValueError('conversion binding mismatch')
            _validate_hwpx(output)
        except (OSError, ValueError, TypeError):
            raise ValueError('HWP 변환 캐시의 원본·결과를 확인하지 못함. 새 양식으로 다시 첨부해 주세요.') from None
        return output
    converted = convert_legacy(source, output_dir=output.parent)
    if converted != output or sha256(source.read_bytes()).hexdigest() != original_sha:
        raise ValueError('HWP 변환 결과 또는 원본 SHA가 일치하지 않음')
    _validate_hwpx(converted)
    binding = {'source_sha256': original_sha, 'output_sha256': sha256(converted.read_bytes()).hexdigest(),
               'source_format': 'hwp', 'output_format': 'hwpx'}
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix='conversion-', suffix='.json')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(binding, stream, ensure_ascii=False)
        os.replace(temporary, receipt)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return converted
