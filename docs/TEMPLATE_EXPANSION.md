# RA 양식·새 양식 분석 확장 — 2026-10-03

## 실제 원본 기입 범위

서브에이전트 3개가 서로 다른 원본·프로파일·테스트 파일을 맡아 RA PDF 양식 11종의 선택 입력칸 306개를 추가함. 등록 위치가 원본 글자와 겹치지 않는지, 작성 영역 밖의 원본 페이지가 유지되는지, 시험값이 정확한 위치에 들어가는지 검사함. 신규 원본 11개·15쪽과 시험 출력 15쪽을 PDFium PNG로 시각 대조함.

| 추가 업무 | 공식 원본 ID | 입력칸 | QA 기록 |
|---|---|---:|---|
| 제조·위탁제조판매·제조업 변경·수입업 | 별지 1·3·7·7의2 | 89 | `outputs/ra_manufacturing_profile_qa/qa.json` |
| 임상시험 실시상황·사전 검토·해외제조소 | 별지 32의2·41·57의2 | 114 | `outputs/ra_clinical_foreign_profile_qa/summary.json` |
| 품목신고·GMP·원료 GMP·위해성 관리 개요 | 별지 6·81·82·RMP 개요 | 103 | `outputs/ra_gmp_rmp_profile_qa/summary.json` |

직접 입력 136칸과 자료 근거 작성 170칸으로 분리함. 신원·허가 식별자·제조소 정보·연락처 등을 임의 생성하지 않음. 앞뒤 페이지의 동일 담당자 이름은 같은 값으로 입력하고 임상시험 단계·IRB 상태·교육이수/전체 인원·정규/비정규·변경 전/후/사유는 각 역할을 보존함.

이 확장 당시 기존 6종/93칸을 포함한 법정 RA 프로파일은 17종/399칸, Business·Office를 합친 전문 업무 프로파일은 23종/463칸임. 후속으로 기업 공개 Roche 프로파일 1종/33칸을 추가한 현재 총계는 24종/496칸이며 [최신 RA 실자료 검증](RA_REAL_SOURCE_VALIDATION.md)에 분모를 구분함. 원본 추가 다운로드 수로 합산하지 않음. 원본 SHA가 바뀌면 기존 위치를 적용하지 않음.

최신 RA 코퍼스 검사는 고유 원본 18개를 전체 파싱하고 원본 SHA 불변을 확인함. 선택 항목 기입·저장 후 독립 검수는 PDF 17종에서 통과하고 GMP 영문 증명 HWP 1종의 변환·기입은 대기임. 실행 결과는 `outputs/ra-compatibility-expanded.json`임. 앞선 전체 공개 코퍼스 137개 및 회사별 제품정보 6개 출력과 분모가 다름.

## 긴 미지 양식과 많은 입력칸

새 PDF의 이미지 분석은 앞 10쪽으로 제한하지 않음. UI는 실제 전체 쪽 수를 기본값과 선택 상한으로 표시함. API의 `max_image_pages=None`은 전체 페이지 분석이며 양의 정수는 앞쪽 선택 범위임. 분석한 페이지와 남은 페이지를 `image_analysis`에 기록함. 전체 이미지 분석과 모든 입력칸 발견·제출 적합성은 구분함.

PDFium 렌더는 한 스레드에서 순서대로 실행하고 모델 요청만 최대 4개로 병렬 실행함. 실패한 페이지가 있으면 확인된 부분 프로파일을 저장하지 않음. 입력칸 매핑은 64칸씩 요청하고 배치별 및 전체 ID 누락·중복·다른 배치 혼입·반복 항목 제약 충돌을 차단함. 12쪽 PDF 전체 분석, 11쪽 부분 범위, 130칸의 3개 배치, 페이지 실패·후속 배치 오류·원본 불변·저장 안 됨·실제 Streamlit 위젯을 mock으로 검증함.

같은 XML 부품·XLSX 공유 문자열은 문맥 추출 중 한 번만 읽어 재사용함. 합성 200항목 DOCX의 문맥 추출 중앙값은 0.1348초에서 0.0088초로 줄었음. 측정 기록은 `outputs/template-learning-context-benchmark.json`이며 실제 LLM 응답 시간이나 사용자 초안→최종본 KPI로 해석하지 않음.

## 예시값과 실제 근거 분리

프로파일의 `demo_values`, QA `verification`, 저장 예시값·필드 기본값을 지시 해석·초안·상사 리뷰·완결성 검수의 모델 입력에서 제외함. 실제 사용자 답변·원자료·원본 SHA·권한·분량·업무 문맥은 유지함. 원본 프로파일과 독립 출력 검수는 원래 정보를 보존하고 완결성 검수의 fingerprint에 입력 투영 버전을 포함함. 시험값을 근거로 실제 신청인·숫자·설명을 생성하지 않도록 경계를 강화함.

## 남은 범위와 재실행

당시 전체 pytest 1,142개가 통과하고 skip 0개·의존 라이브러리 경고 1개·165.92초를 기록함. RA 코퍼스 17개 선택 기입, 회사별 공개 원자료 출력 규칙 6/6, 기존 mock 15/15·전문 업무 규칙 24/24가 통과하며 기준 대비 점수 하락 없음. `outputs/template-expansion-verification.json`에 당시 원본·프로파일·프롬프트·출력 SHA와 각 분모를 저장함. 한국어·기업 양식·최신 전체 테스트는 후속 검증 기록을 기준으로 확인함. 실제 모델 평가는 API 키 미설정으로 응답 0건·미평가이며 성공으로 대체하지 않음.

서명·동의·신청 구분·분절 날짜·기관 작성 접수/발급란·일부 좁은 신원 칸·추가 행·전체 필수 첨부는 이번 등록 범위에 포함하지 않음. RMP 상세 위해성 표는 기존 안내 문구를 덮어쓰지 않으며 현행 고시 버전 미확인 상태를 유지함. 모든 프로파일의 전항목 작성·법적 제출 인증은 false임. 숫자 합계·교육인원 관계·달력 날짜의 검증은 별도 보강 대상이며 메타데이터 등록만으로 통과를 주장하지 않음.

공식 원본에 시험값을 채운 검증과 실제 생성 모델 평가를 구분함. 실제 API 설정과 RA 담당자의 최종 수정본이 필요하며 사용자 수정 비율·작성 시간·반려 횟수 개선은 아직 미측정임.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --junitxml=outputs/pytest-ra-expanded.xml
.\.venv\Scripts\python.exe -X utf8 -m evals.compatibility --provenance official_ra --pdf-pages 0 --output outputs/ra-compatibility-expanded.json
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --baseline outputs/ra-public-regression.json --output outputs/ra-public-expanded-regression.json
```

`--provenance`는 선택 출처의 수집·다운로드·검증만 실행하며 다른 출처나 정부 탐색 분모를 섞지 않음.
