from contextlib import nullcontext
from pathlib import Path

import pytest

from parsers import extract


@pytest.mark.parametrize('body,table,uncertain', [
    ('Original (cid:431) text', [], True),
    ('Original text', [[['Amount (cid:7)', '2']]], True),
    ('Original sufficient text', [], False),
])
def test_unmapped_glyphs_are_retained_and_flagged(monkeypatch, tmp_path, body, table, uncertain):
    path = tmp_path/'fixture.pdf'; path.write_bytes(b'%PDF mock')
    class Page:
        def extract_text(self, **kwargs): return body
        def extract_tables(self): return table
    class Reader:
        is_encrypted = False
    monkeypatch.setattr(extract, 'PdfReader', lambda path: Reader())
    monkeypatch.setattr(extract.pdfplumber, 'open', lambda path: nullcontext(type('PDF', (), {'pages': [Page()]})()))
    if uncertain:
        with pytest.warns(UserWarning, match='CID'):
            document = extract.parse_pdf(path)
    else:
        document = extract.parse_pdf(path)
    block = document['페이지/시트 정보'][0]
    assert block['본문'] == body
    assert bool(block.get('검증 필요')) == uncertain
    if uncertain:
        assert any('CID' in item for item in block['불확실한 항목'])
    if table:
        assert document['표 목록'][0]['검증 필요'] is True


def test_actual_rtp_guidance_unmapped_character_is_not_certified():
    path = Path(__file__).resolve().parents[1]/'data/public_templates/ra_extended/pace_rtp_guidance.pdf'
    if not path.is_file(): pytest.skip('Official fixture not downloaded')
    with pytest.warns(UserWarning, match='CID'):
        document = extract.parse_pdf(path)
    first = document['페이지/시트 정보'][0]
    assert '(cid:431)' in first['본문'] and first['검증 필요'] is True
    assert len(document['페이지/시트 정보']) == 3
