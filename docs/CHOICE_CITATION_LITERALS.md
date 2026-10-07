# PDF 선택 코드와 출처 인용의 구분

원본 PDF의 저장 코드가 `[S1]` 또는 `[S1, S2]`인 경우 코드 자체를 출처 인용으로 지우지 않음. 화면 표시 문구와 저장 코드는 원본 `/Opt`의 대응대로 유지함. 이 보강은 공개 양식 전수 호환이나 실제 허가·동의·전자 제출 완료를 의미하지 않음.

## 데이터와 출처

| 입력 예 | 실제 양식 값 | 출처 ID |
|---|---|---|
| 원본 코드 `[S1]` + ` [SUactual]` | `[S1]` | `SUactual` |
| 원본 코드 `[S1, S2]` + ` [SUactual]` | `[S1, S2]` | `SUactual` |
| 다중 선택 `["[S1]","[S1, S2]"] [SUactual]` | JSON 배열 문자열 그대로 | `SUactual` |
| 직접 입력한 editable 값 `[Scustom]` + ` [SUactual]` | `[Scustom]` | `SUactual` |
| 일반 문장 `○ 매출 120원을 달성함 [S1]` | 기존 문장·출처 규칙 적용 | `S1` |

`agent.field_citations.split_field_citations(value, field=None, literal=None)`는 실제 값과 인용 ID를 분리하되 입력을 변경하지 않음. 닫힌 목록은 확인된 원본 코드와 JSON 배열을 먼저 대조함. 실제 선택 코드와 별개인 뒤의 인용은 기존 출처 검사 대상으로 남김. `승[S1]인`을 지워서 허용 옵션 `승인`으로 만들지 않음.

Editable 입력의 임의 문자열은 그 자체만 보고 인용인지 확정할 수 없음. 실제 사용자 입력으로 기록한 `locked_fields` 원값을 `literal_values`로 전달하여 분리함. 모델 응답을 직접 입력 원값으로 승격하지 않음. 의미 검수 fingerprint에는 선택 규칙과 직접 입력 원값의 해석도 포함함.

일반 문장의 존재하지 않는 출처, 출처 없는 사실, 원자료와 다른 수치는 계속 차단함. 코드 `[S1]` 자체는 출처를 제공하지 않으며, 자료 ID `S1`이 우연히 존재하더라도 선택값의 인용으로 인정하지 않음. 다른 메타데이터가 없는 일반 문자열을 자동으로 선택 코드로 판단하지 않음.

## 작성·수정·저장

- 직접 입력으로 선언한 선택 항목은 사용자 값과 연결된 실제 입력 출처를 사용함. 사용자가 지정하지 않은 항목을 모델의 첫 옵션·서명·동의 결정으로 채우지 않음. 선택값에는 용어 자동 교정도 적용하지 않음.
- 초안에서 사용자 선택을 바꾸면 기존 출처 연결·잠금 검사가 다운로드를 차단함. 입력란에서 값을 정정하고 다시 작성해야 새 직접 입력 출처로 저장됨.
- Sidecar 출력은 초안 JSON·사용자 입력·출처 연결을 유지하면서 인쇄할 값의 실제 인용만 분리함. PDF 작성기로 들어가는 값과 독립 검수의 예상 값은 같은 실제 코드임.
- 저장 후 검수는 원본 `/Opt`·플래그·위치·표시와 작성본 `/V`·`/I`를 대조함. 이미 분리한 editable 코드에 인용 제거를 다시 적용하지 않음. 빈 값·다른 코드·미등록 코드로 변조한 작성본은 차단함.
- 원본 PDF와 사용자 초안은 변경하지 않음. 선택칸의 원래 빈 입력 보존, 다중 선택의 원본 옵션 순서, 기존 선택 권한 검사는 유지함.

## 검증 범위

새 테스트는 ReportLab으로 만든 합성 PDF와 mock 응답을 사용함. 단일 선택, 코드와 다른 표시 문구, editable 입력, JSON 다중 선택, Streamlit의 최초 미선택 상태, 초안 수정 차단·재작성, 저장본 재열기와 독립 검수를 포함함. 원본 SHA·초안 불변과 고의 `/V` 변조도 검사함.

실제 LLM API 호출·모델 품질 평가·사람 KPI 관측은 0임. 이 시험용 선택은 실제 승인·동의·서명 또는 외부 제출이 아님. PDFium 기반 기존 표시 검수와 실제 Acrobat 재편집·전자 제출 적합성은 별도 범위임.

최종 집중 회귀는 아래 명령으로 **562개 통과, 23.77초**를 기록함. 실제 합성 PDF 저장·재열기·독립 검수 두 정상 흐름, 초기 미선택 UI, 코드 삭제/교체/미등록 값 세 변조 흐름을 포함함. 전체 pytest는 별도 통합 실행 결과로 관리함.

재현 명령:

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_choice_citation_literals.py tests/test_pdf_choice_integration.py tests/test_pdf_choices.py tests/test_pdf_choice_output_check.py tests/test_draft.py tests/test_review.py tests/test_review_ra_english.py tests/test_grounding.py tests/test_grounding_batches.py tests/test_value_rules.py tests/test_choice_group_rules.py tests/test_choice_groups_integration.py tests/test_value_rules_integration.py
```
