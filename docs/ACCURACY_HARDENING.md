# 문서 입력 검증과 RA 실자료 검수 보강

문서 표준화 AI AGENT의 작성·수정·양식 기입·저장 파일 재열기에 같은 입력 제약을 적용함. 사람이 발견하던 잘못된 달력 날짜, 합계, 교육 인원, 단위 중복과 RA 조건 누락을 출력 전에 차단하는 기능임. 실제 모델의 초안 품질과 사용자 수정 비율은 별도로 측정해야 함.

2026-10-03 전체 pytest는 **1,570개 통과·보류 1개·경고 1개·218.94초**임. 보류 항목은 공개 PDF의 추출 권한 제한이며 보호 차단 테스트는 통과함. [JUnit 결과](../outputs/pytest-accuracy-hardening.xml)와 [통합 검증 기록](../outputs/accuracy-hardening-verification.json)에 분모·원본/프로필/출력 해시와 현재 코드 해시를 연결함. mock 15/15 및 전문 업무 규칙 24/24도 통과했으며 실제 모델 평가는 아님.

## 입력과 출력의 두 검증 경로

이 문서의 수치·캐시 버전은 앞선 입력 검증 보강 실행의 기록임. 이후 선택 목록·그룹 규칙과 `template-mapping-5`의 검증은 [후속 보강](NATIVE_CHOICES_AND_ROWS.md)에 별도로 기록함.

1. `agent/pipeline.py`가 생성 및 사용자 수정본의 필수 항목·분량·입력 타입·항목 관계를 검사함. 경고에 항목명을 표시하고 미해결 오류가 있으면 다운로드를 차단함.
2. `templates/compatibility.py`가 직접 기입 호출에서도 같은 규칙을 확인함. `agent/output_check.py`는 저장 파일을 다시 열어 동일 입력 위치의 실제 값과 원본 서식·수식·배치를 대조함. 예상 값 자체가 잘못되어도 검수 통과로 처리하지 않음.

검증은 DOCX·HWPX·XLSX·PPTX·작성 가능한 PDF의 공통 엔진에 연결됨. 원본을 덮어쓰거나 잘못된 수치·단위를 자동 수정하지 않음. 숫자·날짜 칸이 좁더라도 별첨 참조로 바꾸지 않음.

## 실제 등록 규칙

10개 공개 양식 프로필의 92개 숫자·날짜 입력칸과 22개 관계를 등록함. RA 제81·82호의 면적·작업인원 합계 4개, 제32호의2의 교육 이수≤전체 인원 16개, 제81·82호의 분리된 년·월·일 달력 2개임. 날짜와 숫자 칸에는 본문 개조식 종결을 덧붙이지 않음.

원본에 `㎡`, `명`, `건`, `주`가 칸 밖에 인쇄되어 있으면 숫자만 입력함. 원칸에 단위가 없는 작업인원·연구비에 다른 표의 단위를 추정 이식하지 않음. `2025년 2월 29일`, `2026년 2월 30일`처럼 세 숫자 각각은 허용 범위이지만 잘못된 날짜도 결합하여 차단함. 관계 일부가 비어 있으면 0이나 현재 날짜를 만들어 넣지 않음.

원본 페이지·인쇄 라벨·SHA와 실제 기입/PNG 확인 범위는 [필드 검증 근거](FIELD_VALIDATION.md)에 기록함. 합성 QA 숫자·날짜·신청인 예시는 제출 자료가 아니며 모델 작성의 근거에서 제외함.

## 처음 보는 양식

새 양식 분석은 인쇄된 항목·문맥에 근거한 숫자·정수·날짜 규칙을 제안함. `prompts/template.md`와 `template_image.md`에서 번호·기간 범위·월 단위 날짜·임의 단위 환산을 금지함. 사용자가 입력 위치와 규칙을 확인하면 원본 SHA별 서명된 매핑 캐시에 저장함. 새 매핑 버전은 `template-mapping-4`이며 이전 버전 캐시는 재확인이 필요함.

화면의 **숫자·날짜·합계 검사 규칙 편집**에서 타입·인쇄 단위·범위·날짜 형식·합계·날짜 관계를 지정할 수 있음. 잘못된 직접 입력값은 해당 입력란 아래에 표시하고 작성 버튼을 비활성화함. 확인 후 저장한 선택 매핑은 재사용 시 필수 여부와 관계를 유지함.

## Excel 숫자 셀의 저장 검증

원본의 빈 입력칸이 정수·숫자로 선언되고 인용이나 입력 단위 문자열이 없으면 XLSX의 실제 숫자 셀(`t="n"`)로 저장함. 일반 번호의 앞자리 0, 날짜, 혼합 자리표시자와 인용 포함 문자는 문자열로 유지함. 칸 밖 인쇄 단위는 유지하며 병합·글꼴·수식·표시 형식을 바꾸지 않음.

저장 파일은 입력값과 정확한 XML 십진수를 독립적으로 비교함. Python 실수로 읽었을 때 동일하게 반올림되는 변조도 차단함. Excel의 15 유효자리와 숫자 입력 범위를 벗어나는 값, 원본의 표시 정밀도 계산 설정은 오류로 표시하고 자동 반올림하지 않음. 근거는 [Microsoft Excel 제한](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits?app=wp)과 [부동소수점·표시 정밀도 설명](https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/floating-point-arithmetic-inaccurate-result)임.

실제 공개 `history-competition-2026.xlsx`의 학년 G9가 숫자 셀로 저장되고 원본 SHA가 유지되었음을 [QA 기록](../outputs/xlsx-numeric-qa/history-competition-2026.qa.json)에 저장함. 이 원본에는 수식이 없음. 합성 양식의 SUM 참조·수식 보존은 pytest로 확인했으며 실제 Excel 계산 결과·새 출력의 시각 검증은 대기임.

## RA 근거와 연결 조건

운영 검수에 성상 근거와 인용 문장의 적용 조건·예외·의사 판단·증량 시점을 추가함. 제품명만 인용한 성상, 기존 치료 불충분 조건을 잘라낸 효능, 의사 판단에 따른 대체 초기 용량과 증량 시점을 삭제한 용법을 차단함. 약품명·정답 평가 메타데이터를 하드코딩하지 않음.

M1이 읽은 Benepali 136쪽·Keppra 170쪽의 실제 검색 조각에서도 검사함. 조각은 같은 원문 블록의 `context_text`와 정확한 문자 범위를 보존하며 문맥이 바뀌면 출처 ID와 의미 검수 fingerprint도 바뀜. 숫자·사실 근거를 무관한 주변 문장으로 확대하지 않음. 임의 항목명으로 매핑된 성상/용법과 본문의 임상 문장도 확인함.

선정된 실제 원문 변형 92개에서는 RA 운영 검출이 88→92건으로 개선되었고 정상 17개는 유지됨. 일반 검수 65/92, RA 92/92, 별도 정답 원문 대조 92/92를 구분함. [운영 평가](../outputs/ra-public-adversarial-operating.json)는 실제 모델 생성 평가가 아님.

현재 규칙은 같은 문장과 최대 3개의 명시적으로 연결된 후속 문장을 검사함. 다른 페이지의 조건, 멀리 떨어진 참조, 의미가 같은 번역·요약과 미등록 표현은 추가 의미 검수 대상임. 임상·법적 적합성 또는 모든 오류의 전수 검출을 인증하지 않음.

## 실자료 재실행과 남은 범위

법정 RA 원본 18개를 전체 읽고 PDF 17종/399칸의 선택 기입을 재검증함. GMP 영문 증명 HWP 1종의 기입은 대기임. EMA 원자료 3개사×법정/기업 양식 출력 9건과 한미약품 원자료×법정 양식 2건이 통과함. 에리우스 2건은 원본 추출 권한 제한 때문에 보류함.

선택 원문·출처 41/41, 수치 포함 필드 35/35를 대조함. 새 출력은 독립 구조·값·원본 픽셀 검사를 통과했으며, 이번 실행에서 이전 42쪽을 모두 다시 직접 시각 확인했다고 표시하지 않음. 등록 필드 QA의 직접 시각 확인은 별도 근거 파일에 한정됨.

실제 API 모델 응답과 사용자 확정본 KPI는 아직 확보하지 않았음. 비공개 기업 내부 양식, 모든 공공기관 서식, 네이티브 HWP 기입, 서명·첨부·전자 제출과 현행 접수 요건은 프로젝트 전체의 남은 범위임.

재현 명령:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_adversarial --output outputs/ra-public-adversarial-operating.json
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --check-parser --profile ra_law_form_4_pdf --profile ra_law_form_20_pdf --profile corporate_roche_supplier_change_request --output outputs/ra-real-source-typed-ema.json --artifact-dir outputs/ra-typed-round/ema
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --manifest evals/ra_korean_sources.json --check-parser --profile ra_law_form_4_pdf --profile ra_law_form_20_pdf --output outputs/ra-real-source-typed-korean.json --artifact-dir outputs/ra-typed-round/korean
.\.venv\Scripts\python.exe -X utf8 -m evals.compatibility --provenance official_ra --no-samples --pdf-pages 0 --workers 4 --timeout 90 --output outputs/ra-compatibility-value-rules.json
```

한국어 원자료의 권한 보류 2건은 CLI 실패 상태로 기록하고 통과 분모에 넣지 않음. 원본 출처와 회사 역할은 [RA 실자료 보고서](RA_REAL_SOURCE_VALIDATION.md)에 기록함.
