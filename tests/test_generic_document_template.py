"""일반 사내외 문서의 중립 문구와 재생성·기입·독립 검수를 확인함."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from agent.output_check import verify_output
from parsers import parse_file
from samples.generate import SEED, generate
from templates import analyze_template, fill_compatible_template

ROOT=Path(__file__).resolve().parents[1]
VALUES={
    '제목':'사내외 문서 검토안 (시험용)',
    '요약':'□ 일반 검토를 위한 시험 문서임',
    '본문':'□ 작성 내용을 확인함\n○ 제출처와 첨부자료를 직접 확인함\n- 시험값으로 작성함',
}
FORBIDDEN=('결과보고','주간업무보고','품의서','보고 제목','보고 요약','사내 보고서','교육 실시 결과')


def _visible_text(path):
    with ZipFile(path) as archive:
        texts=[]
        for name in archive.namelist():
            if name.startswith('word/') and name.endswith('.xml') or name.startswith('Contents/section') and name.endswith('.xml'):
                xml=etree.fromstring(archive.read(name))
                texts.extend(xml.xpath('//*[local-name()="t"]/text()'))
            elif name=='Preview/PrvText.txt':
                texts.append(archive.read(name).decode('utf-8'))
    return '\n'.join(texts)


@pytest.fixture(scope='module')
def regenerated(tmp_path_factory):
    target=tmp_path_factory.mktemp('generic-document-generator')
    seed=SEED.read_bytes()
    generate(target)
    assert SEED.read_bytes()==seed
    return target/'templates'


@pytest.mark.parametrize('suffix',['docx','hwpx'])
def test_generator_produces_neutral_generic_document(regenerated,suffix):
    source=regenerated/f'generic_document.{suffix}'
    assert source.is_file()
    profile=analyze_template(source)
    assert profile['supported']
    assert {field['value_key'] for field in profile['fields']}=={'제목','요약','본문'}
    text=_visible_text(source)
    assert '사내외 문서 (일반 검토용)' in text
    assert '문서 제목' in text and '문서 요약' in text
    assert not any(term in text for term in FORBIDDEN)


@pytest.mark.parametrize('suffix',['docx','hwpx'])
def test_generic_document_actual_fill_and_independent_verification(tmp_path,suffix):
    source=ROOT/f'templates/generic_document.{suffix}'
    before=source.read_bytes()
    profile=analyze_template(source)
    output=fill_compatible_template(source,VALUES,tmp_path/f'generic-filled.{suffix}',profile=profile)
    checked=verify_output(source,output,VALUES,profile=profile)
    assert checked['status']=='passed'
    assert checked['sha']['template']==sha256(before).hexdigest()
    assert source.read_bytes()==before
    text=parse_file(output)['본문']
    assert all(value in text for value in VALUES.values())
    visible=_visible_text(output)
    assert '{{' not in visible
    assert not any(term in visible for term in FORBIDDEN)
    assert '사내외 문서 (일반 검토용)' in visible
    with ZipFile(source) as original,ZipFile(output) as filled:
        changed={part for part in original.namelist() if original.read(part)!=filled.read(part)}
    assert changed==({'word/document.xml','word/header1.xml'} if suffix=='docx' else {'Contents/section0.xml','Preview/PrvText.txt'})
