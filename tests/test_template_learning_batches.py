from copy import deepcopy
from pathlib import Path
from threading import Barrier, Lock, get_ident
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader, PdfWriter
import pytest

from agent import template_learning as learning
from templates import analyze_template

ROOT = Path(__file__).resolve().parents[1]


class MappingClient:
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate

    def generate_json(self, prompt, payload):
        self.calls.append(deepcopy(payload))
        fields = [dict(id=f['id'], kind=f['kind'], value_key=f['label'], required=False,
                       input_required=False, input_mode='source_grounded', max_chars=120,
                       confidence=.9) for f in payload['fields']]
        response = {'fields': fields}
        if self.mutate:
            self.mutate(response, payload)
        return response


@pytest.fixture
def long_form(tmp_path):
    path = tmp_path / 'long_form.docx'
    doc = Document()
    table = doc.add_table(rows=130, cols=2)
    for number, row in enumerate(table.rows, 1):
        row.cells[0].text = f'사업 설명 {number}'
    doc.save(path)
    return path


@pytest.fixture
def long_pdf(tmp_path):
    original = PdfReader(ROOT / 'samples/sample_static_form.pdf')
    writer = PdfWriter()
    for _ in range(12):
        writer.add_page(original.pages[0])
    path = tmp_path / 'twelve_pages.pdf'
    writer.write(path)
    return path


def test_long_form_batches_cover_every_real_field_and_preserve_source(long_form, tmp_path):
    before = long_form.read_bytes()
    client = MappingClient()
    profile = learning.learn_template(long_form, client, cache_dir=tmp_path / 'cache')
    assert [len(p['fields']) for p in client.calls] == [64, 64, 2]
    assert [p['batch_number'] for p in client.calls] == [1, 2, 3]
    assert all(p['batch_count'] == 3 and p['total_field_count'] == 130 for p in client.calls)
    supplied = [f['id'] for p in client.calls for f in p['fields']]
    assert len(set(supplied)) == 130
    assert [f['id'] for f in profile['fields']] == supplied
    assert long_form.read_bytes() == before
    assert not (tmp_path / 'cache').exists()


def test_each_xml_part_is_read_once_for_many_contexts(long_form, monkeypatch):
    fields = analyze_template(long_form)['fields']
    reads = []

    class CountedZip(ZipFile):
        def read(self, name, *args, **kwargs):
            reads.append(name)
            return super().read(name, *args, **kwargs)

    monkeypatch.setattr(learning, 'ZipFile', CountedZip)
    contexts = learning._contexts(long_form, fields)
    assert reads == ['word/document.xml']
    assert len(contexts) == 130
    assert all(f"사업 설명 {i}" in field['context'] for i, field in enumerate(contexts, 1))


@pytest.mark.parametrize('failure', ['foreign_batch', 'missing', 'duplicate', 'mode_conflict'])
def test_later_batch_errors_do_not_produce_confirmed_partial_mapping(long_form, tmp_path, failure):
    def mutate(response, payload):
        if payload['batch_number'] != 2:
            return
        if failure == 'foreign_batch':
            response['fields'][0]['id'] = client.calls[0]['fields'][0]['id']
        elif failure == 'missing':
            response['fields'].pop()
        elif failure == 'duplicate':
            response['fields'].append(deepcopy(response['fields'][0]))
        else:
            response['fields'][0].update(value_key='사업 설명 1', max_chars=5)
    client = MappingClient(mutate)
    before = long_form.read_bytes()
    with pytest.raises(ValueError):
        learning.learn_template(long_form, client, cache_dir=tmp_path / 'cache')
    assert not (tmp_path / 'cache').exists()
    assert long_form.read_bytes() == before


class ImageClient(MappingClient):
    def __init__(self, *, barrier=None, failed_page=None):
        super().__init__()
        self.image_calls = []
        self.barrier = barrier
        self.failed_page = failed_page
        self.lock = Lock()
        self.active = self.peak = 0

    def read_image_json(self, image, *, prompt_name, payload):
        with self.lock:
            self.image_calls.append(payload['page'])
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.barrier:
                self.barrier.wait(timeout=3)
            if payload['page'] == self.failed_page:
                raise RuntimeError('page read failed')
            return {'fields': [dict(label=f"설명 {payload['page']}", page=payload['page'],
                                    x=80, y=100, width=100, height=20)]}
        finally:
            with self.lock:
                self.active -= 1


def test_twelve_page_pdf_all_pages_and_bounded_parallel_requests(long_pdf, tmp_path, monkeypatch):
    render_threads = []
    root_thread = get_ident()
    monkeypatch.setattr('parsers.extended.render_pdf_page',
                        lambda *_: render_threads.append(get_ident()) or b'image')
    client = ImageClient(barrier=Barrier(2))
    before = long_pdf.read_bytes()
    profile = learning.learn_template(long_pdf, client, cache_dir=tmp_path/'cache',
                                     max_image_pages=None, image_workers=2)
    assert client.peak == 2
    assert set(render_threads) == {root_thread}
    assert sorted(client.image_calls) == list(range(1, 13))
    assert [f['page'] for f in profile['fields']] == list(range(1, 13))
    assert profile['image_analysis'] == {'analyzed_pages': list(range(1, 13)),
                                         'remaining_pages': [], 'complete': True}
    assert all(f['location']['page'] == i for i, f in enumerate(client.calls[0]['fields'], 1))
    assert long_pdf.read_bytes() == before


def test_explicit_page_limit_over_ten_and_partial_scope(long_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr('parsers.extended.render_pdf_page', lambda *_: b'image')
    client = ImageClient()
    profile = learning.learn_template(long_pdf, client, cache_dir=tmp_path/'cache',
                                     max_image_pages=11, image_workers=1)
    assert profile['image_analysis'] == {'analyzed_pages': list(range(1, 12)),
                                         'remaining_pages': [12], 'complete': False}
    assert any('앞 11쪽만' in w for w in profile['warnings'])


def test_failed_page_never_maps_or_saves_partial_pdf(long_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr('parsers.extended.render_pdf_page', lambda *_: b'image')
    client = ImageClient(failed_page=5)
    before = long_pdf.read_bytes()
    with pytest.raises(RuntimeError, match='page read failed'):
        learning.learn_template(long_pdf, client, cache_dir=tmp_path/'cache', max_image_pages=None)
    assert not client.calls and not (tmp_path/'cache').exists()
    assert long_pdf.read_bytes() == before


@pytest.mark.parametrize('kwargs', [dict(max_image_pages=0), dict(max_image_pages=True),
                                   dict(max_image_pages=1.5), dict(image_workers=0),
                                   dict(image_workers=5), dict(image_workers=True)])
def test_invalid_analysis_limits_are_rejected(long_pdf, tmp_path, kwargs):
    with pytest.raises(ValueError):
        learning.learn_template(long_pdf, ImageClient(), cache_dir=tmp_path/'cache', **kwargs)


def test_ui_allows_default_complete_pdf_analysis_over_ten_pages(long_pdf, tmp_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        f"from pathlib import Path\nfrom app.ui import form_options\n"
        f"form_options(Path({str(long_pdf)!r}), Path({str(tmp_path / 'ui-data')!r}))",
        default_timeout=15).run()
    assert not app.exception
    control = next(widget for widget in app.number_input if widget.label == '이미지 양식 분석할 쪽 수')
    assert control.value == 12 and control.max == 12
    control.set_value(11).run()
    assert any('전체 12쪽 중 앞 11쪽' in caption.value for caption in app.caption)
