# 파서 및 양식 테스트 자료

`sample_repeat_costs.xlsx`는 RA 시험 비용 내역을 가정한 합성 회귀 양식임. 시험명·수량·단가·검토 상태의 입력 행과 행 금액·합계·다른 시트 집계를 포함함. 실제 의약품·임상 자료나 회사 양식으로 집계하지 않음. 생성과 입력 규칙의 재현은 `tests/test_repeat_xlsx_integration.py`의 `cost_form`·`cost_policy`를 사용함.

`source.pdf`, `source.xlsx`, `source.docx`, `source.hwpx`는 개인정보 없는 교육 실적 자료임.
교육 대상 100명, 참석 95명, 참석률 95%, 만족도 4.5점을 테스트 데이터로 사용함.
PDF는 2쪽, XLSX는 교육 실적·만족도 시트로 구성함.

`templates/`에는 결과보고서, 주간업무보고, 품의서 DOCX/HWPX 양식을 제공함.
자리표시자는 제목·요약·본문이며 표·분할 run·머리말의 치환을 검증함.
`filled_result_report.docx/.hwpx`는 줄 단위 개조식 치환의 예시 결과임.

`python -m samples.generate`로 fixture를 다시 생성할 수 있음. PDF 재생성에는
맑은 고딕(`C:/Windows/Fonts/malgun.ttf`)이 필요하며 다른 OS는 해당 경로를 변경해야 함.

## HWPX seed 출처와 수정 기록

`hancom_table_seed.hwpx`는 [python-hwpx의 한컴 저장 fixture](https://github.com/airmang/python-hwpx/blob/006eb94b05fc8d3996c61bed74ec4897d290ad0e/tests/fixtures/hancom_saved/cell_border_2x3_base.hwpx)를 사용함.
Copyright 2025-2026 airmang (Dr. Wily), Apache License 2.0. 원문 라이선스는
`HWPX_SEED_LICENSE.txt`에 보존함. 라이브러리는 설치하지 않음.
HWPX samples와 templates는 seed의 정상 패키지·서식 정의·표 구조를 유지하고
본문 및 표 셀에 자체 테스트 내용을 넣은 수정본임. toy XML ZIP을 HWPX로 표시하지 않음.

HWPX 구조 처리는 [한컴 포맷 안내](https://tech.hancom.com/hwpxformat/),
[한컴 본문 파싱 안내](https://tech.hancom.com/python-hwpx-parsing-2/),
[한컴 줄 나눔 안내](https://forum.developer.hancom.com/t/hwpx-linesegarray-lineseg-textpos/1677),
[본문 수정 시 줄 위치 캐시 제거 안내](https://forum.developer.hancom.com/t/hwpx-section0-xml/2414)를 참고함.

## 지원 범위

- PDF 텍스트 및 격자 기반 표를 추출함. 스캔 페이지는 OCR 필요 경고로 표시함.
- XLSX 문자열·숫자·날짜·표시된 백분율 및 저장된 수식 계산값을 추출함.
  수식 계산값이 없으면 재계산 후 저장하도록 안내함. 병합 셀의 시각적 복원은 수행하지 않음.
- DOCX 본문·표 셀·중첩 표를 추출함. HWPX 구역·문단·표·셀을 추출함.
  두 형식 모두 실제 페이지 번호를 추정하지 않으며 문단/셀 위치를 제공함.
- 양식 치환은 원본 파일을 변경하지 않고 원본 텍스트 구간만 치환함.
  첫 글자의 서식을 따르며 글꼴·문단·표·페이지 설정과 미변경 ZIP 부품을 보존함.
- 내용 길이에 따른 재배치·표 넘침과 HWPX 고정 높이 셀은 자동 보장하지 못함.
  이미지 미리보기는 다시 생성하지 않으며 텍스트 미리보기만 갱신함.
- XML/ZIP 구조 검증과 실제 Word/한글에서 열어 본 확인은 별개임.

## 이번 검증 결과

- Python 3.11의 M1/M2 pytest 38건을 통과함.
- Microsoft Word에서 DOCX 양식 3종과 채운 결과보고서를 읽기 전용으로
  열고 PDF로 내보냄. 모두 1쪽이며 PNG 전체 시각검사에서 줄 나눔·글꼴·표·머리말·꼬리말이 유지됨.
- PDF 원자료 2쪽도 PNG로 렌더링해 한글 및 표가 정상 표시되는지 확인함.
- 스킬의 `render_docx.py`는 LibreOffice가 없어 실행하지 못해 Word native export로 대체함.
- 한글 2022 설치는 확인했으나 파일 접근 자동화 승인모듈이 설정되어 있지 않음.
  HWPX는 ZIP/XML 및 서식 참조 검증을 통과했으며 실제 한글 열기·쪽 배치 확인은 남아 있음.
