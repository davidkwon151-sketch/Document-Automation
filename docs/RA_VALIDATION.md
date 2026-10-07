# RA 공개 실자료 검증 — 2026-10-03

이 문서는 앞선 EMA 3개사·법정 2종 평가의 기록임. 한국어 원자료·기업 공개 양식·의도적 오류·최신 전체 테스트의 추가 결과는 [최신 통합 검증](RA_REAL_SOURCE_VALIDATION.md)을 기준으로 확인함. 아래 1,142개 pytest와 6개 출력 수치는 당시 실행 결과임.

삼성바이오에피스·셀트리온 계열사·UCB의 공식 EMA 제품정보를 다운로드하여 국내 공통 RA 양식에 기입하는 검사와 의도적 오류 차단 검사를 수행함. 서로 다른 회사의 원자료를 사용했으며 기업 내부 비공개 양식을 확보한 검증으로 표시하지 않음. 해외 판매허가권자를 국내 신청인·제조원으로 추정하지 않음.

## 실제 원자료와 파서

| 자료 | PDF의 회사 역할 | 전체 파싱 | 선택 원문 대조 |
|---|---|---|---|
| [Herzuma 제품정보](https://www.ema.europa.eu/en/documents/product-information/herzuma-epar-product-information_en.pdf) | Celltrion Healthcare Hungary Kft. / EU 판매허가권자 | 66쪽·28표, 5.12초 | 150 mg 제형의 제품명·성상·미개봉 사용기간·초기/유지 용법 4개 |
| [Benepali 제품정보](https://www.ema.europa.eu/en/documents/product-information/benepali-epar-product-information_en.pdf) | Samsung Bioepis NL B.V. / EU 판매허가권자 | 136쪽·38표, 9.37초 | 25 mg 제형의 제품명·함량·사용기간·효능·용법 5개 |
| [Keppra 제품정보](https://www.ema.europa.eu/en/documents/product-information/keppra-epar-product-information_en.pdf) | UCB Pharma SA / EU 판매허가권자 | 170쪽·46표, 17.12초 | 250 mg 제형의 제품명·함량·사용기간·효능·용법 5개 |

운영 M1 `parsers.parse_file → parse_pdf`로 전체 372쪽·112표를 읽고 모든 페이지 번호와 원본 SHA 불변을 확인함. 선택 원문 14개 모두 지정된 페이지에서 공백 정규화만으로 일치함. pypdf의 독립 판독도 원본 SHA·회사 역할·선택 제형·전체 인용·페이지를 대조함. 31.61초는 이 환경의 전체 M1 파싱 합계이며 AI 작성 시간이나 사용자 초안→최종본 KPI가 아님.

원본 URL·SHA·관할·회사 역할·버전·사실별 원문과 페이지는 `evals/ra_public_sources.json`, 다운로드 경위는 [원자료 기록](RA_PUBLIC_SOURCES.md), 파서 실행은 `outputs/ra-public-parser-validation.json`에 저장함.

## 실제 국내 양식 기입

별지 4호 품목허가 신청서와 별지 20호 갱신 신청서에 각 회사의 선택 사실을 적용함. 3개사 × 2개 양식, 총 6개 검토용 출력이 모두 원문 대조와 독립 출력 검수를 통과함. 회사별 내부 서식 6개를 수집한 수가 아니며 고유 국내 양식은 2종임.

| 검사 | 결과 | 분모 |
|---|---|---|
| 전체 선택 값 보존 | 28/28 | 14개 사실을 두 양식에 각각 기입함 |
| 유효 출처 연결 | 28/28 | 비어 있지 않은 선택 필드의 모든 원문 줄을 검사함 |
| 수치·단위 정확 일치 | 24/24 | 숫자를 포함한 선택 필드, 참조 절 번호도 원문 그대로 보존함 |
| 저장 후 원칸·별첨 검사 | 6/6 | 모든 원본 페이지, 입력 위치, 전체 원문·출처, 별첨 글자 위치를 검사함 |
| PDFium 렌더 | 6개 파일·18쪽 | 모든 출력 페이지를 렌더함. 입력/별첨 12쪽과 대표 원본 안내 2쪽을 시각 확인함 |

선택 값을 줄이거나 일부 문장을 삭제해 작은 칸에 넣지 않음. 명시적 별첨 옵션을 사용하여 원칸에 `별첨 n 참조`를 쓰고 전체 내용·출처를 추가 페이지에 보존함. 별지 4호 원본 3쪽은 4쪽으로, 별지 20호 원본 1쪽은 2쪽으로 작성함. 제출기관이 별첨을 허용하는지는 이 자동 검사로 확정하지 않음. 직접 입력·서명·선택 항목을 별첨으로 대신하지 않음.

장문 양식 지원과 별도로 DMF·갱신·안전성 보고 프로파일 3종/48칸을 추가함. 기존 품목허가·변경·임상시험 3종/45칸과 합쳐 RA 6종/93칸의 선택 입력 프로파일을 제공함. 추가 3종의 기입 QA는 합성 시험값 검사이며 이번 3개사 원문 평가 6건과 분모가 다름. 각 프로파일은 해당 PDF 원본 SHA와 연결됨.

앞선 RA 코퍼스 재검사는 고유 원본 18개를 모두 읽고 SHA 불변을 확인함. 기입 6종/93칸 통과·대기 12종은 `outputs/ra-compatibility-final.json`에 기록함. 후속 확장에서 PDF 11종/306칸을 추가하여 현재 17종/399칸의 선택 기입·독립 검수가 통과하고 GMP HWP 변환/기입 1종은 대기임. 최신 실행은 `outputs/ra-compatibility-expanded.json`, 신규 11개 원본/시험 출력 15쪽의 시각 QA와 범위는 [확장 검증](TEMPLATE_EXPANSION.md)에 기록함. 원본 18개 모두 기입 성공 또는 전체 공개 코퍼스 137개와 합산한 것으로 표시하지 않음.

## 오류를 차단하는 두 검수

내용 검수는 다른 제품·제형의 수치, 성인/소아, 초기/유지 용량, 미개봉/재구성 상태, 주간/일간 투여 간격, 단위 오타·누락, 사실 문장과 출처를 대조함. 원문이 정확해도 영어 `years`, `mg/kg`, 초기/유지 문단을 오판하던 일반 검수를 수정함. 한국어 및 무역·금융 수치 검사의 기존 회귀를 함께 실행함.

저장 후 독립 검수는 같은 입력칸의 참조와 별첨 전체 내용·출처, 별첨 글자의 페이지 내 위치, 모든 원본 페이지에서 작성 영역 밖의 픽셀 보존을 확인함. 별첨 누락·다른 수치·추가 빈 페이지·화면 밖으로 이동한 글자·원본 그림 변경을 차단함. 검수 후 프로파일의 직접 입력 보호·필드·분량·제품 범위·매핑을 바꾸어 내려받는 우회도 차단함. UI에서 양식 설정이 바뀌면 이전 초안과 다운로드를 폐기함.

공식 RA 프로파일은 출처를 별도 기록하는 설정을 기본으로 사용함. 짧은 신원 칸에 인용 ID를 인쇄하지 않으면서 JSON 초안의 사용자 입력 출처와 고정값 검수를 유지함. 선택 항목의 명시적 빈 문자열은 원칸을 유지하며 필수 빈 값·없는 값의 매핑은 실패함.

## 반복 실행과 현재 한계

최신 전체 pytest는 1,142개 통과·skip 0개·경고 1개·165.92초임. 경고는 의존 라이브러리의 기존 deprecation 안내이며 결과는 `outputs/pytest-ra-expanded.xml`에 저장함. mock 15건과 전문 업무 규칙 24건도 통과하고 기존 기준 대비 RA 원문 기입·mock 점수 하락이 없었음.

앞선 원본·프롬프트·양식 프로파일·출력 SHA 종합 기록은 `outputs/ra-final-verification.json`이며 최신 확장·해시·회귀 근거는 `outputs/template-expansion-verification.json`임. 회사별 원자료 출력의 시각 확인 14쪽과 자동 검사 범위는 `outputs/ra-public-validation/previews/rendered-pages.json`에 분리하여 기록함.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --junitxml=outputs/pytest-ra-final.xml
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --output outputs/ra-public-validation.json
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --baseline outputs/ra-public-validation.json --output outputs/ra-public-regression.json
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode live --output outputs/ra-public-live.json
```

`rules`는 독립 확인한 원문을 그대로 옮기는 검사임. 정확한 인용·수치·양식 보존의 성공을 실제 LLM 생성 품질로 환산하지 않음. `live`는 운영 생성·검색·검수 경로를 사용하며 API 설정 실패를 mock 응답으로 대체하지 않음. 현재 API 키가 없어 모델 응답 0건·실제 AI 작성 품질 미평가로 기록함. 프롬프트 변경 전후의 규칙 평가 하락은 없으나 실제 모델 품질이 개선됐다고 주장하지 않음.

EU 자료만으로 대한민국 품목허가·갱신·임상시험 승인·법정 제출 적합성을 인증하지 않음. 한국 신청인·서명·허가번호·제조 및 시험 자료·필수 첨부 등 미기입 항목을 결과에 함께 기록하며 모든 출력의 `submission_ready`는 false임. 기업 내부 RA 서식, 한국 허가 원자료, 실제 RA 담당자의 최종본·수정 비율·작성 시간·반려 횟수 검증은 남아 있음.

다운로드 원본과 테스트 PDF는 Git에서 제외함. 원본이 없는 환경의 실제 corpus pytest는 skip으로 표시하며 실자료 평가 CLI는 원본 누락·SHA 불일치를 실패로 기록함. 이번 환경에서는 실제 원본을 확보하여 해당 검사를 생략하지 않음.
