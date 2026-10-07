# 공개 프로토콜 선택 페이지 회귀 자료

ClinicalTrials.gov에 공개된 Regeneron의 R3918-PNH-1868 임상시험계획서(NCT04162470)를 사용함.
공식 공개 위치: https://cdn.clinicaltrials.gov/large-docs/70/NCT04162470/Prot_000.pdf

- 전체 원본은 68쪽이며 SHA-256은 `52c3066161ceee95cf4ecfea4656a81c36260bfcdb8e645ee669c8c9694d8007`임.
- 전체 원본 `full-original-protocol.pdf`와 원본 8쪽(목차), 17쪽(시험 목적), 31쪽(시험약 투여)의 한 쪽 발췌를 함께 보존함. 발췌 3개는 총 약 0.83 MiB임.
- 각 PDF는 원래 페이지의 콘텐츠 스트림·글꼴·자원을 유지한 부분 발췌이며 원래 페이지 번호를 `provenance.json`에 저장함. 발췌 PDF의 물리적 첫 페이지를 원본 1쪽으로 오인하지 않음.
- 준비할 때 전체 원본의 SHA와 발췌 페이지의 동일 콘텐츠 스트림을 대조했음. `test_ra_natural_scope.py`의 84개 테스트는 저장된 발췌 SHA·콘텐츠 스트림 SHA와 선정 절의 문맥을 검사함. `test_ra_natural_default_flow.py`는 전체 원본 SHA를 대조하고 68쪽을 모두 파싱하며 원본 17·31쪽의 해당 절과 8쪽 목차의 거부를 확인함. 68쪽 전수 파싱은 전체 문장의 의미 검증을 뜻하지 않음. 네트워크 재다운로드는 하지 않음.
- 기본 생성·출력 흐름 테스트의 첨부는 원본 17쪽을 온전히 보존한 한 쪽 발췌 PDF임. 발췌 파일 SHA와 전체 원본 SHA, 원본 17쪽과 첨부의 물리적 1쪽을 구분함. 전체 68쪽을 생성 흐름의 첨부로 처리한 검증으로 표시하지 않음.
- 평가는 정확한 원문 인용과 같은 페이지의 문서 식별·실제 절 경계를 검사함. LLM 요약·재서술, 임상적 타당성, 법정 제출 적합성, 사람 수정 KPI를 검증한 자료가 아님.
- `synthetic-counterexamples.json`은 실제 제품/시험 자료가 아닌 독립 검토용 합성 반례 6개임. DSUR 성공 사례도 테스트 코드의 합성 자료이며 공개된 완성 DSUR의 실자료 검증으로 표시하지 않음.

네트워크·API 키·외부 모델·네이티브 앱 없이 `pytest -q tests/test_ra_natural_scope.py tests/test_ra_natural_default_flow.py`로 실행함. 생성 흐름의 LLM은 mock이며 실제 모델의 작성 품질을 평가한 결과가 아님.
