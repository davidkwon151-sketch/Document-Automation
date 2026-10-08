import json
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from docs.demo_sales.record_multilang import CASES, check_reply
from docs.demo_sales import render_multilang


DEMO = Path(__file__).resolve().parents[1] / 'docs' / 'demo_sales'


def test_three_original_buyer_cases_and_reply_language_checks():
    assert set(CASES) == {'en', 'es', 'he'}
    assert len(set(CASES.values())) == 3
    check_reply('es', 'Gracias por su consulta. El precio y la entrega figuran abajo.')
    check_reply('he', 'תודה על פנייתכם. מחיר היחידה וזמן האספקה מפורטים להלן.')
    with pytest.raises(ValueError):
        check_reply('es', 'Thank you for your inquiry.')
    with pytest.raises(ValueError):
        check_reply('he', 'Thank you for your inquiry.')


def test_video_requires_live_model_provenance_for_each_language(tmp_path, monkeypatch):
    monkeypatch.setattr(render_multilang, 'RAW', tmp_path)
    for language in CASES:
        folder = tmp_path / language
        folder.mkdir()
        (folder / 'provenance.json').write_text(json.dumps({
            'live_gemini': language != 'es', 'model': 'gemini-3.6-flash',
            'language': language,
        }), encoding='utf-8')
        (folder / 'manifest.json').write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='actual model provenance'):
        render_multilang.source_frames('es')
    assert 60 <= sum(render_multilang.DURATIONS) <= 120


def test_published_multilingual_video_and_real_reply_provenance():
    assert (DEMO / 'buyer_email_multilingual_live_demo.mp4').stat().st_size > 100_000
    with Image.open(DEMO / 'buyer_email_multilingual_live_preview.gif') as preview:
        assert preview.size == (800, 450) and preview.n_frames >= 5
    proof = json.loads((DEMO / 'multilingual_live_provenance.json').read_text(encoding='utf-8'))
    assert proof['source_sha256'] == sha256((DEMO / 'sample_company_facts.txt').read_bytes()).hexdigest()
    assert {item['language'] for item in proof['responses']} == set(CASES)
    for item in proof['responses']:
        assert item['model'] == 'gemini-3.6-flash'
        reply = (DEMO / f"sample_live_reply_{item['language']}.txt").read_text(encoding='utf-8')
        assert sha256(reply.encode()).hexdigest() == item['reply_sha256']
        check_reply(item['language'], reply)
