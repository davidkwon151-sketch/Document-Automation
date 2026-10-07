# 공식 PDF 선택 목록 실물 조사

2026-10-03 확인한 공식 공개 원본을 로컬 검증용으로 보존함. 기준 파일은 [pdf_choice_sources.json](../evals/pdf_choice_sources.json)이며 원본은 `data/public_templates/pdf_choices/`에 저장함. 원본 PDF의 `/AcroForm/Fields`와 전체 페이지의 `/Widget`을 함께 관찰하고 선택 목록의 `/Opt`, `/Ff`, `/V`, `/DV`, `/I`, 페이지·주석 순서·좌표·부모 연결을 기록함.

인터넷 원본의 컨트롤 관찰과 합성 사용자가 고른 값의 기입 시험을 구분함. 개인정보·서명·동의·실제 지원 자격·주문·임상 판단을 생성하거나 제출하지 않음. 실제 모델 요청, 모델 학습, 사람 수정 KPI 측정은 각각 0건임.

## 원본과 관찰 분모

| 공식 공개 기관 | 원본 | 확인한 버전 범위 | 전체 쪽 | canonical 항목 / 형식 있는 필드 | 실제 `/Ch` |
|---|---|---|---:|---:|---:|
| Thermo Fisher Scientific / Gibco | [Custom Serum Request Form](https://documents.thermofisher.com/TFS-Assets/LSG/brochures/gibco-custom-serum-request.pdf) | PDF 제목 메타데이터 v6.2, 인쇄된 저작권 2016. 최신 주문 서식 여부는 미확인 | 3 | 66 / 66 | 6 |
| U.S. OPM, GSA 공식 저장소 | [SF182 공식 소개](https://www.gsa.gov/reference/forms/authorization-agreement-and-certification-of-training), [원본 PDF](https://www.gsa.gov/system/files/SF182-20.pdf) | 원문 Revised March 2020. GSA 페이지의 Current Revision 03/2020, 페이지 갱신 2021-04-12 | 9 | 88 / 86 | 13 |
| Nursing and Midwifery Board of Ireland | [Grade IV Application Form 공식 원본](https://www.nmbi.ie/NMBI/media/NMBI/Application-Form-Grade-IV_1.pdf) | 명시적 서식 개정일·현재 채용 공고는 미확인. ModDate 2024-06-25는 PDF 메타데이터 날짜로만 기록 | 9 | 127 / 127 | 1 |
| 합계 | 서로 다른 기관 3곳, 고유 원본 3개 | 현재 신청 가능·법적 적합성 미인증 | 21 | 281 / 279 | 20 |

GSA canonical 항목 중 2개는 `/FT`가 없는 계층 노드이므로 실제 형식 있는 입력 필드 수에서 제외함. 선택 목록 20개는 모두 닫힌 단일 선택 콤보이며 `/Ff=131072`임. 실제 원본에서 editable combo 0개, multiselect 0개를 확인함. 해당 유형의 구현 회귀와 실제 공개 원본 검증을 혼합하지 않음.

Thermo 원본 SHA256은 `c7e2e2eee4343f60e847b72f42a69e2d579d6a8de3e047989d506ee5b307ae1d`, GSA 원본은 `6f3bc58aaa8adfabc3f55d39df3a59ad36908be1ef5865a3cb90f4370d5b1eee`, NMBI 원본은 `9c96ef50c86b5b2d0d50eefccbee5601b347da72d94fb2c948779a5a456dfdf6`임. 크기·원본 URL·관찰 후 다운로드 최종 URL·확인 시각은 manifest에 저장함.

## 선택값과 표시 문구

Thermo의 원본 선택값은 공백 문자열임. 합성 사용자 선택 시험은 `Type of Serum=US Certified FBS`, `Bottle Size 1=100mL bottle` 두 칸으로 제한함. 제품 사용 분류, 승인·서명·주문 조건은 선택하지 않음.

NMBI의 `Dropdown1`은 원본 저장값이 `Yes`임. 실제 `/Opt`는 `Yes`와 `[Select option, No]` 쌍을 포함함. 두 번째 항목의 저장값은 `Select option`, 화면 표시 문구는 `No`이며 빈 자리표시자로 추정하면 안 됨. 시험은 합성 사용자가 그 두 번째 항목을 명시적으로 선택한 경우만 다룸. 실제 지원자의 국적·근무 자격·지원 의사에 관한 선언이 아님. 원본 `Yes`와 원본 파일 SHA를 유지하고 별도 출력 사본에서만 선택값을 교체함.

GSA 원본에는 `/Perms/UR3`와 서명 입력 필드가 있음. 이 선언이 개인의 실제 서명을 의미한다고 단정하지 않음. 현재 공통 엔진의 인증·권한 보호 guard가 입력을 차단하므로 원본 구조 관찰·페이지 렌더만 수행하고 기입은 보류함. 보호 설정을 제거하거나 다른 PDF로 변환해 작성 권한을 우회하지 않음.

## 검증과 재현

원본 관찰·render QA helper는 `.runtime/collect_pdf_choice_evidence.py`와 `.runtime/run_pdf_choice_public_qa.py`임. PDFium 렌더는 한 스레드에서 전체 원본 21쪽을 순차 처리함. 몽타주는 전체 페이지의 외관 확인용이며 모든 안내 문장을 검증했다는 뜻이 아님.

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_pdf_choice_sources.py
.venv\Scripts\python.exe -X utf8 .runtime/run_pdf_choice_public_qa.py --fill
```

실물 선택 기입·독립 검수·출력 PNG의 최종 수치는 어댑터 동결 후 [audit.json](../outputs/pdf-choice-public-qa/audit.json)에 기록함. 검증은 canonical 값과 원래 위젯 위치·옵션·비선택 값·배치·표시의 보존을 대조하며 Acrobat에서의 실제 재편집·동작, 제출 준비 완료, 실제 사용자 작성 성공률은 별도 미평가임.

최종 동결 코드의 실물 QA는 **2개 부분 기입본 통과, GSA 1개 보호 보류, 실패 0개**임. Thermo 2칸과 NMBI 1칸 총 3개 선택값의 canonical 저장값·raw `/I=[1]`·실제 표시 문구를 대조했고 독립 출력 검수를 통과함. 원본 21쪽·작성본 12쪽의 PNG 33개, 전체 페이지 몽타주 2개, 선택 영역 원본/출력 대조 이미지 3개를 확인함. NMBI는 저장값 `Select option`과 표시 문구 `No`의 차이를 보존함. 원본 파일과 최종 QA 실행 전후 코드 SHA는 불변임. 공개 원본 관련 pytest는 **9개 통과(2.95초)**이며 [summary.json](../outputs/pdf-choice-public-qa/summary.json)과 [tests-audit.json](../outputs/pdf-choice-public-qa/tests-audit.json)에 분모를 보존함. 원본 manifest는 관찰 시점 스냅샷이며 기입의 최종 결과는 별도 QA 보고서를 기준으로 확인함.

초기 QA helper는 PDFium `init_forms()` 호출을 빠뜨려 위젯이 렌더에서 제외됐음. 이를 원본의 표시 불일치로 해석한 관찰을 철회하고 폼 초기화·주석 렌더를 켠 상태로 모든 페이지를 다시 확인함. 원본 NMBI의 `Yes`와 작성본의 `No`가 표시됨. 초기 실물 검수의 좌표 실수 비교 오탐은 [first-attempt.json](../outputs/pdf-choice-public-qa/first-attempt.json)에 별도 보존함. pypdf `get_fields()`의 축약 결과에는 `/I`가 빠지므로 manifest와 선택 기입 증거의 인덱스는 원본 canonical 트리·위젯/부모의 raw `/I`에서 읽음.

최종 UI·매핑 계약 수정 후 생산 코드로 `outputs/pdf-choice-public-final/`에서 별도로 재실행함. 최종 결과는 부분 기입 2개 통과·보호 보류 1개·선택칸 3개임. 원본 21쪽·작성본 12쪽 렌더, 몽타주 2개·선택 영역 3개 대조와 코드/프롬프트 192파일 불변을 `summary.json`·`freeze-audit.json`에 기록함. 이전 `outputs/pdf-choice-public-qa/` 46파일을 유지했으며 최신 전체 회귀와 통합 범위는 [PDF 선택 목록 보강](PDF_NATIVE_CHOICES.md)에 연결함.

## 제외·접근 실패

- [Grants.gov Forms Repository](https://www.grants.gov/forms/forms-repository/)에서 관찰한 [SF424 Individual V1.0](https://apply07.grants.gov/apply/forms/readonly/SF424_Individual-V1.0.pdf)은 실제 원본 2쪽·28,278 bytes를 내려받았으나 canonical 입력 트리와 `/Ch`가 모두 0개여서 선택 목록 확보 수에서 제외함. 05-2005, 인쇄된 유효기간 2011-07-30의 과거 참조본이며 현행 제출본으로 안내하지 않음.
- [FDA MedWatch 공식 안내](https://www.fda.gov/safety/medical-product-safety-information/medwatch-forms-fda-safety-reporting)에서 FDA3500 원본 링크를 관찰함. 웹 PDF는 09/2025판 8쪽으로 표시됐으나 로컬 다운로드 요청은 302 `/apology_objects/abuse-detection-apology.html`로 거절됨. 내려받은 `/Ch` 원본으로 집계하지 않음.
- [USCIS I-9 공식 페이지](https://www.uscis.gov/i-9)는 웹 조회에서 403이 반환됨. 관찰·확보·기입 성공 수에 포함하지 않음.
- FDA/Grants Python 다운로드의 인증서 체인 실패도 조사 기록에 남김. 정상 인증서 검증을 사용하는 Windows 요청으로 Grants만 회복했고 인증서 검증 비활성화나 접근 차단 우회는 하지 않음.

공개 다운로드와 원본 재배포 허가는 별개임. 원본은 Git에서 제외하고 로컬 시험 스냅샷으로 보존함. 미국 연방·아일랜드 규제기관·기업 공개 서식의 관할을 한국 허가 신청 또는 각 기업의 비공개 사내 공통 양식으로 바꾸지 않음.
