# RA 공개 원자료·회사 양식 확장 검증 — 2026-10-03

이번 단계에서는 Novo Nordisk·Eli Lilly의 공식 제품 설명서와 Pace·Cambrex의 공개 시험 의뢰 양식을 추가하고, 원자료의 조건과 PDF 배치 오류를 고쳤음. 서브에이전트 3개가 원자료·검색 문맥, RA 조건 검수, 양식 작성·표시 보존을 나누어 작업했으며, 루트는 화면 연결과 저장 후 독립 검수를 통합했음.

**선정 원문을 옮기는 규칙·양식 검증을 통과했음. 실제 LLM이 한국어 초안을 작성하는 품질과 사람 수정 비율은 아직 미평가임.** 회사의 비공개 내부 양식이나 전 항목이 채워진 법정 제출서류의 완료를 뜻하지 않음.

## 최종 결과와 분모

| 검사 | 결과 | 범위와 남은 사항 |
|---|---|---|
| 전체 pytest | **2,940 passed · 1 skipped · 3 warnings** | 실패/오류 0, 종료 0. pytest 580.03초·실행기 582.11초. 코드·프롬프트·프로파일 등 268파일의 실행 전후 SHA 동일 |
| RA 원자료 파싱 | 허용된 8회사·8자료, **723쪽·325표·45선정 사실** | 계획 원자료 9개 중 에리우스 1개는 추출 제한으로 보류. 723쪽 전체 파싱과 45개 사실의 검증을 구분함 |
| 회사 자료×양식 | **47계획: 42선택 기입 통과·5보류** | 보호 원자료 1개×5양식이 보류됨. 8허용회사×기존5양식과 신규2자료×Cambrex1양식임 |
| 원문·출처 | 전체 값 보존 **106/106**, 유효 출처 **106/106** | 45개 고유 사실을 여러 양식 위치로 옮긴 횟수임. 출처 ID·원본 SHA·페이지·회사 역할·제품 범위·전체 인용을 대조함 |
| 수치 | **91/91** 수치 포함 항목 일치 | 숫자·단위·조건을 선택 원문과 대조한 결과이며, 모든 임상 판단의 정확도나 실제 모델 생성 정확도가 아님 |
| 출력 배치 | PDF 42개·**132쪽 새 렌더**, **60고유 이미지 직접 확인** | 몽타주10개를 루트4개·서브에이전트6개로 분담. 나머지72쪽은 확인 이미지와 PNG SHA 동일. 확대 확인도 이 분모 안에 포함함 |
| 추가 출력 검수 | **통과8·경고32·사용불가2** | 경고는 별첨·인쇄 위치 등 추가 확인 범위임. Cambrex 2개는 원본 서명 필드에 대한 guard로 추가 경로 사용불가. 별도 PDFium·독립 값/표시 검사와 추가 경로 통과를 혼합하지 않음 |
| 새 회사 빈 양식 | Pace2개·Cambrex1개, 원본6쪽·선정14칸 등록/기입 확인 | canonical225칸 중 미등록211칸. 3개 시험 출력6쪽·원본6쪽 직접 시각 확인. 시험값은 실제 신청인·제품 사실로 저장하지 않음 |
| 전문 RA 조건 | 선정 실제 오류 **11/11 차단**, 정상 쌍 **11/11 통과** | 의사 판단·부정·대상군·개봉/재구성·mL당/회당·초기/유지/최대 조건. 미등록 조건·번역·페이지 간 참조는 별도 평가 대상 |
| 검색 상충 분류 | 같은 9선정 사례의 수정 전2/9 → 수정 후**9/9** | 정상 상태 변화와 실제 상충을 함께 포함한 분류셋임. 9건 모두 오류라는 의미가 아님 |
| 평가 회귀 | mock상태15/15·업무규칙24/24·정상17/17·선정 변조92/92 검출 | mock 점수 대상14초안의 4개 검사 각100점·기준 대비 하락0. 모델 호출 없이 규칙을 검사했음 |
| 실제 LLM·사용자 KPI | 실제 요청0·응답0·확정/수정 기록0 | API 설정을 확인할 수 없음. 실제 정확도·수정 비율·시간 절감·반려 감소를 아직 측정하지 않음 |

자동 테스트의 보류1개는 원본 PDF의 일반 EXTRACT 권한이 없는 사례임. 경고3개는 Starlette deprecation, Pace RTP 안내문 1쪽의 미복원 CID 문자, openpyxl의 x14 확장 읽기 경고임. x14 출력은 원본 ZIP XML을 유지하며 확장 보존 회귀로 대조함. CID는 원문과 페이지를 유지한 확인 대기 상태임.

## 새로 확보한 공식 자료

- [EMA Ozempic 제품 정보](https://www.ema.europa.eu/en/medicines/human/EPAR/ozempic): 143쪽, 선정9사실. 0.25mg 사전충전 펜과 Novo Nordisk A/S의 판매허가권자 역할을 해당 원문 절에 연결함.
- [EMA Mounjaro 제품 정보](https://www.ema.europa.eu/en/medicines/human/EPAR/mounjaro): 202쪽, 선정8사실. 2.5mg 단회 펜과 Eli Lilly Nederland B.V.의 판매허가권자 역할을 연결함.
- [Pace 공식 시료 제출 안내](https://www.pacelabs.com/life-sciences/submit-a-sample/): Single Lot 1쪽·RTP 안내 포함3쪽. 명확한 선정9칸을 등록함. 미복원 CID 안내1쪽과 자원이 없는 Calibri 입력칸은 별도 보류함.
- [Cambrex 공식 양식 안내](https://www.cambrex.com/forms-and-certificates/): Durham 시료 의뢰2쪽·선정5칸. 인쇄된 문서 버전과 출처 URL 연도를 구분함.

공식 해외 설명서를 국내 허가·신청인·제조원·효능 인증으로 바꾸지 않음. 확인 날짜의 원본 스냅샷이며 최신 국내 법정 제출 적합성을 인증하지 않음. 확보 경로·버전·회사 역할·SHA·선정 인용은 [원자료 기록](RA_EXTENDED_PUBLIC_SOURCES.md)과 [회사 양식 기록](RA_EXTENDED_PUBLIC_TEMPLATES.md)에 있음.

## 발견한 오류와 수정

- 일반 용량 문장을 제품 규격 머리글로 오인하던 검색을 수정했음. 명시된 제품명·규격·제형 머리글만 범위로 사용하며 같은 조건의 실제 상충은 계속 검출함. 숫자만 있는 조각에서도 전체 원문에 연결한 문자 범위로 조건을 복원하고 불완전한 숫자 조각·잘못된 문맥 범위는 거부함.
- 의사 판단 조건 누락, 안전성·유효성 미확립의 반대 서술, 초기/유지/최대 용량, 성인/소아, 개봉 전/재구성 이후, mL당/회당 혼동을 RA 검수에 추가했음. 문맥 변경 시 검수·다운로드 근거를 무효화하고 오류를 사용자 경고와 출력 차단으로 전달함.
- 160문장을 넘는 의미 검수를 전체 출처와 원래 항목/줄 번호를 유지하는 배치로 나눴음. 누락·중복·다른 배치 응답·실패는 전체 통과로 처리하지 않음.
- Pace 원본의 존재하지 않는 Widget 페이지 참조는 canonical/페이지 주석에서 고유한 실제 위치를 확인한 경우에만 사본에서 연결했음. 실제 다른 페이지를 가리키는 참조나 중복·고아 주석은 계속 거부함. Cambrex의 정상 Popup/Parent 상호 연결은 원위치와 전체 속성을 독립 대조함.
- PDF 고정 글꼴의 실제 글리프 넘침을 저장 전에 차단했음. 원문 삭제·조용한 글꼴 대체·고정 글꼴 축소로 감추지 않음. 원본 자동 크기 입력칸만 같은 글꼴·색·영역 안에서 조정함.
- 국제 회사 카탈로그의 신규 RA 양식을 `시험의뢰·품질자료 지원` 업무와 연결했음. 잘못된 업무 흐름은 다운로드·화면 상태 변경 전에 거부함. 신청인·서명·동의·날짜는 직접 입력과 원본 권한을 유지함.

## 실행 및 증거

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m evals.run --mode mock --output outputs/evaluation.json
.\.venv\Scripts\python.exe -X utf8 -m evals.domains --output outputs/domain-evaluation.json
```

최종 감사가 참조하는 실제 실행 증거는 아래에 있음. 교차 실행기의 manifest와 선정 프로파일은 출력 안의 스냅샷으로 고정했으며, `evals.ra_public --mode rules --native-review auto --check-parser`의 각 실행과 연결함.

- `outputs/ra-extended-validation/verification.json`: 전체 검사·분모·현재 코드/입력/출력/증거 SHA 연결, `full_project_complete=false`.
- `outputs/ra-extended-validation/verified/tests-audit.json`, `pytest-full.xml`, `pytest-full.log`: 전체 테스트와 268파일 SHA.
- `outputs/ra-extended-validation/verified/matrix/audit.json`, `denominators.json`, `visual-qa.json`: 47계획·출처/수치·원본·출력·이미지 검수.
- `outputs/ra-extended-templates/final-audit.json`: 신규3양식의 독립 값/구조/배치와 원본 불변.
- `outputs/ra-extended-conditions/audit.json`, `outputs/ra-extended-conflicts/direct-before-after.json`: 같은 선정 사례의 수정 전후 및 현재 코드 SHA.
- `outputs/ra-extended-validation/verified/mock-evaluation.json`, `domain-evaluation.json`, `adversarial.json`, `live-preflight.json`: 평가 종류와 실제 모델 미실행 기록.

수정 전 발견한 파이프라인 오류와 검색 문맥 오류로 중단한 전체 실행2회는 `outputs/ra-extended-validation/tests-audit.json`과 `final/tests-audit.json`에 보존했음. 완료된 전체 테스트로 집계하지 않음. 이전 단계 2,740통과 기록은 [이전 검증](PDF_NATIVE_CHOICES.md)에 보존함.

## 남은 검증

실제 LLM 한국어 초안·요약·번역·스캔/OCR을 원문과 대조하고 RA 담당자가 최종본을 만들 때 수정 비율·소요 시간·반려 횟수를 측정해야 함. 최신 국내 허가/서식·전체 필수 항목·첨부·서명·전자 제출, 회사 비공개 양식, 실제 Acrobat/Hancom 재편집, 미등록211칸과 보류 글꼴/CID·특수 권한은 이번 선정 기입 완료에 포함하지 않음.

커밋 메시지 제안: `feat: validate RA public sources and harden scoped evidence checks`
