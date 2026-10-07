# 공개 제약·시험 의뢰 양식 추가 검증

2026-10-03 확인함. Pace Life Sciences와 Cambrex가 공식 홈페이지에 공개한 **외부 고객의 시험·시료 제출용 PDF 3개**를 확보함. 회사 2곳, SHA가 서로 다른 원본 3개이며 비공개 사내 RA 양식 확보 건수는 0임.

| 공식 공개 원본 | 전체 쪽 | canonical 필드 | 등록·부분 기입 | 미등록 canonical 필드 |
| --- | ---: | ---: | ---: | ---: |
| Pace Single Lot General Submission | 1 | 55 | 5 | 50 |
| Pace RTP ARF + Guidance | 3 | 92 | 4 | 88 |
| Cambrex Durham Sample Submission | 2 | 78 | 5 | 73 |
| 합계 | 6 | 225 | 14 | 211 |

등록한 항목은 프로젝트 제목·시료 식별자·용기 수·의뢰 시험 설명·추가 지시, Cambrex 첫 행의 시료 설명·시험 정보·보관 조건 등 명확한 업무 입력칸에 한정함. 연락처·청구 정보·계좌·허가 식별자·서명·승인·기관 내부 수령 기록·시험 결과는 작성하지 않음. Cambrex의 빈 전자서명 칸과 인쇄 날짜/버전 값도 유지함.

공식 원본의 `/Ff` 필수 비트·글꼴·표시 크기·선택 옵션을 변경하지 않음. Cambrex에는 실제 제출 시 모든 칸을 작성하라는 별도의 인쇄 지시가 있고, Pace RTP에도 연락·청구·견적·서명을 포함한 작성 안내가 있음. 이번 14칸 검증은 그 제출 조건을 만족한 완성 서류가 아니며, 원본의 전 항목 필수 조건을 완화한 인증으로 사용하지 않음.

## 출처와 버전

[Pace 공식 Submit a Sample 페이지](https://www.pacelabs.com/life-sciences/submit-a-sample/)의 직접 PDF 링크에서 두 원본을 내려받음. 회사 페이지에서 관찰한 CDN 호스트는 `6835044.fs1.hubspotusercontent-na1.net`임. [ARF Single Lot 원본](https://6835044.fs1.hubspotusercontent-na1.net/hubfs/6835044/Certifications%20and%20Forms/PLS/Forms/ARF%20-%20General%20Single%20Lot.pdf), [RTP ARF + Guidance 원본](https://6835044.fs1.hubspotusercontent-na1.net/hubfs/6835044/Certifications%20and%20Forms/PLS/Forms/RTP-ARF%20%2B%20Guidance.pdf)을 사용함. 두 원본의 확실한 인쇄 개정일은 확인하지 못했으며 현재 접수용 최신 서식이라는 주장은 하지 않음.

[Cambrex 공식 Forms and Certificates 페이지](https://www.cambrex.com/forms-and-certificates/)의 Durham 제출 서식 링크에서 [실제 PDF 원본](https://www.cambrex.com/wp-content/uploads/2023/05/cambrex-durham-sample-submission-form.pdf)을 내려받음. 원본에서 `FRM03446.02`, `Effective date: 30Dec2025`, 인쇄 기록 `12/30/2025 10:06 am`을 관찰함. URL의 2023 경로를 서식 버전으로 해석하지 않음. 해당 회사의 현재 제출 요건·최신 개정·재배포 허용 여부는 별도 확인이 필요함.

원본·출처 URL·호스트·확인 시각·SHA·선택 필드 위치는 [manifest](../evals/ra_extended_public_templates.json)에 저장함. [Pace 공식 페이지의 실제 CDN 링크 관찰 기록](../outputs/ra-extended-templates/official-page-observation.json)과 [Cambrex 공식 페이지의 실제 PDF 링크 관찰 기록](../outputs/ra-extended-templates/cambrex-official-page-observation.json)도 보존함. 프로파일은 원본 SHA가 일치하는 업로드에만 적용하며 개인 QA 예시 값을 포함하지 않음. 가짜 입력값은 평가 manifest와 무시 대상 QA 출력 폴더에 분리함.

## 실물에서 발견하고 검증한 문제

- Pace Single Lot에는 30개 Widget의 `/P`가 존재하지 않는 객체 210을 가리키는 원본 결함이 있었음. canonical 트리와 전체 `/Annots`에서 원위치의 고유 연결을 확인한 경우에만 사본의 `/P`를 실제 같은 페이지로 복구함. 실제 다른 페이지·중복·orphan·서명/암호 원본은 계속 차단함. 원본 바이트는 수정하지 않음.
- Pace RTP의 Project Title은 `/DA=/Calibri 0 Tf 0 g`이나 원본 폼·local·AP 자원에 해당 글꼴이 없음. Helvetica로 조용히 바꾸지 않고 기입을 차단하며 등록한 4칸에서 제외함. 안내 p1과 기관 수령 p3도 그대로 유지함.
- Cambrex의 정상 FreeText/Popup 역참조 때문에 기존 독립 검수가 순환 오류로 막혔음. 부모-팝업의 상호 연결과 페이지/주석 위치·속성을 독립 대조하는 수정 뒤 정상 부분 기입을 검증함. 임의 순환 연결과 주석 이동은 허용하지 않음.
- Cambrex Qty 첫 행의 원본 8pt·폭 21.24pt 칸에 긴 ASCII 숫자를 넣으면 전체 `/V`가 저장되어도 화면 표시가 잘렸음. 저장 전에 생성된 AP의 전체 문자와 실제 글리프 경계를 검사하여 고정 크기 넘침을 차단함. 원본 0pt 자동 크기 입력칸에서 전체 글리프 사각형이 이미 들어갈 때만 텍스트 기준선을 맞추며 원문·글꼴·색·DA·줄바꿈·필드 조건은 유지함. 잘린 값을 잘라 저장하지 않음.

첫 실패 출력·재현 PNG·원본 결함 목록은 기존 경로에 보존하고 수정 뒤 QA는 새 UUID 하위 폴더로 생성함. 상세 기록은 [원본 페이지 연결](../outputs/ra-extended-templates/source-page-links.json), [원본 주석 순환 경로](../outputs/ra-extended-templates/source-cycles.json), [고정 크기 넘침 첫 재현](../outputs/ra-extended-templates/actual-bug-probes.json)에 있음.

## 확인 범위와 한계

선택 14칸은 실제 작성기와 저장 후 독립 검수로 `/V`, 원위치, 전체 페이지, 비선택 값·옵션·서명, 작성 영역 밖 픽셀 보존을 대조함. PDFium으로 원본 6쪽과 수정 후 작성본 6쪽을 렌더하고 모두 직접 시각 확인함. 선택 입력에서 새 겹침·잘림을 발견하지 못함. 실제 Acrobat 재편집·전자 제출·시험 기관 접수 검증은 수행하지 않음.

전체 6쪽의 M1 추출을 실행함. fi/ffi 등 원본 ligature를 문자로 펼친 비교에서 5쪽은 pypdf 독립 추출과 문자 빈도가 일치함. RTP 안내 p1의 `insufficient`에는 원본 글리프 매핑 차이가 있어 pdfplumber의 `(cid:431)`와 pypdf의 `Ư`를 수동 확인 항목으로 남김. 최신 파서로 다시 읽어 해당 페이지의 `검증 필요=True`와 `추출 방식=PDF 문자 매핑 / 원문 확인 대기`를 확인함. 그 안내 글리프를 완전한 자동 텍스트 검증 성공으로 집계하지 않음. [두 추출 원문과 차이](../outputs/ra-extended-templates/parser-independent-diff.json)를 보존함.

Cambrex의 시료 설명 첫 행은 `제품명`으로 매핑하여 공식 EMA 제품명 전체를 참고용으로 복사하는 시험이 가능함. 실제 보낸 시료·라벨 일치·공급/제조 역할·시험 요청으로 추정하지 않음. Pace 프로젝트·시료 식별자는 사용자 직접 정보로 유지하며 약품 사실을 억지로 넣지 않음. 기본 출처는 sidecar 방식이고 생성 장문 별첨으로 직접 입력·숫자·선택 제약을 우회하지 않음.

실제 LLM/API 호출 0건, 사람 사용 KPI 관측 0건, 전 항목 제출 완료 0건임. pytest·합성 기입·인터넷 원문 복사·PDFium 시각 확인은 서로 다른 검증 단계로 기록함.

## 재현

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_ra_extended_public_templates.py tests/test_pdf_page_links.py tests/test_pdf_annotation_links.py
.venv\Scripts\python.exe -X utf8 -c "import runpy; runpy.run_path('.runtime/ra_template_verify_extended.py', run_name='__main__')"
```

첫 최종 QA 작성 직전에 bundled PDF authoring marker를 성공 1회 실행함(예상 PDF 3개, exit 0). 이전 이 단계에는 원본 다운로드·읽기·렌더만 수행했고 과거 작업의 marker를 이번 작성의 성공 이력으로 소급하지 않음.

최종 집중 회귀는 **196 passed, 1 warning, 102.50초**임. 실패와 skip은 없으며 경고는 RTP 안내 p1의 CID 원문 확인 대기임. 생산 코드·독립 검수·파서·시험·등록 프로파일·manifest의 SHA가 실행 전후 동일함을 대조함. 원본 3개·기입본 3개·선택 14칸을 다시 열어 독립 검수하였고 원본 해시를 유지함. [집중 시험 XML](../outputs/ra-extended-templates/pytest-final-focused.xml), [실행 전후 SHA 감사](../outputs/ra-extended-templates/focused-test-audit.json), [PDF/PNG 및 시각 확인 감사](../outputs/ra-extended-templates/final-audit.json)에 기록함. 전체 프로젝트 pytest와 교차 원자료 행렬은 루트 에이전트의 별도 결과이며 이 196개 분모와 합쳐 표시하지 않음.
