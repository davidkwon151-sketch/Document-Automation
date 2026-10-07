"""Explicit offline fixture for UI tests that exercise optional Office output."""
from hashlib import sha256
from pathlib import Path

import pytest


@pytest.fixture
def native_unavailable(monkeypatch):
    import parsers.native

    def render(path, output_path, **kwargs):
        return {'status': 'unavailable', 'engine': None, 'source_unchanged': True,
                'source_sha256': sha256(Path(path).read_bytes()).hexdigest(),
                'formula_recalculated': False}

    monkeypatch.setattr(parsers.native, 'render_native_pdf', render)
