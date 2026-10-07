# DOCX 드롭다운·콤보 입력

Word SDT의 `w:dropDownList`를 폐쇄 선택으로, `w:comboBox`를 선택 제안이 있는 편집 가능한 입력으로 구분함. Microsoft 공식 문서의 [드롭다운](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.sdtcontentdropdownlist?view=openxml-3.0.1)과 [콤보박스](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.sdtcontentcombobox?view=openxml-3.0.1)는 한 런·한 문단의 입력 구조를 설명함. 이 구조의 텍스트 컨트롤만 대상으로 하며 행·표·그림·중첩 컨트롤로 확장하지 않음.

## 분석 계약

| 원본 | kind / control_type | 입력값 | 메타데이터 |
| --- | --- | --- | --- |
| `w:dropDownList` | `docx_choice` / `choice` | 원본 목록의 export value만 | `allow_custom: false`, `editable_native: false` |
| `w:comboBox` | `docx_combobox` / `combobox` | 원본 export value 또는 사용자가 직접 입력한 한 줄 | `allow_custom: true`, `editable_native: true` |

`options`는 export value 문자열 목록이며 `choice_items`는 `{value, label}` 목록임. UI는 label을 보여주고 value를 저장해야 함. 두 유형 모두 `input_required: true`, `input_mode: user_provided`, `narrative_style_required: false`임. AI가 승인·선택·개인 결정을 임의로 생성하지 않음. 콤보는 제안 목록 밖 직접 입력도 허용하므로 폐쇄 선택으로 표시하지 않음.

원본 [listItem](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.listitem?view=openxml-3.0.1)의 `w:value`와 `w:displayText`를 분리함. 선택된 value에 대응하는 원본 displayText를 본문에 쓰며, displayText 속성이 없으면 value를 표시함. 콤보의 목록 밖 입력은 같은 문자열을 표시함. [lastValue](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.sdtcontentdropdownlist.lastvalue?view=openxml-3.0.1)는 선택한 export value 또는 직접 입력 문자열로 갱신함. 원본 옵션 목록·순서·글꼴·문단·alias·스타일은 유지하며 표시용 `showingPlcHdr`만 제거함.

## 제외와 차단

- 잠금/상위 잠금, 기존 실값, 편집 보호가 설정된 선택 컨트롤은 입력 대상에서 제외함. 양식 보호 문서의 기존 활성 FORMTEXT 처리 범위는 유지함.
- XML `dataBinding` 연결은 연결 데이터까지 안전하게 갱신하는 기능이 없으므로 제외함. 연결을 제거하거나 보호를 우회하지 않음.
- 빈 export value, 앞뒤 공백이 있는 value, 중복 value/display label, 줄바꿈 옵션, 폐쇄 목록의 빈 옵션과 잘못된 구조는 경고 후 제외함. 아무 항목이나 기본 선택하지 않음. 빈 제안 목록의 콤보는 직접 입력 대상으로 유지함.
- date/picture/group/repeating section은 일반 텍스트 SDT로 오인해 덮어쓰지 않고 경고함. 해당 유형의 고유 의미 처리는 이번 범위가 아님.
- 폐쇄 선택의 display label을 export value처럼 넘기거나 목록에 없는 값을 쓰면 차단함. 선택·콤보 값의 줄바꿈/탭은 차단함. 원본과 다른 kind/options/label 메타데이터로 우회할 수 없음.
- 일반 자리표시자와 함께 작성할 때 선택 표시·직접 입력·잠금 SDT의 `{{...}}`는 값 자체로 보존하며 일반 본문의 치환 대상으로 재해석하지 않음. 보존 위치 복원이 실패하면 기존 출력 파일을 바꾸지 않고 오류로 처리함.

출처 인용은 JSON/별도 출처 기록에 유지함. UI·공통 기입 경로는 입력 끝의 유효 출처 ID를 선택값과 구분하며 DOCX 선택칸에 인용 ID를 인쇄하지 않음.

## 독립 검수와 테스트

출력을 다시 열어 원본의 value/display 정의로 표시 문구와 lastValue를 대조함. 채우기 helper를 재사용하지 않으며 원본 옵션·속성·alias·서식·주변 정적 안내문과 동일 입력 위치를 검사함. 원본 SHA와 수정 대상 밖 ZIP 부품을 보존함. 잘못된 표시·lastValue·옵션·alias·안내문 변조는 출력 오류임.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_docx_choices.py tests/test_template_compatibility.py tests/test_output_check.py
```

테스트는 Python으로 만든 합성 DOCX를 사용하며 API 키·모델 호출·네트워크 없이 실행함. 실제 회사 제출 양식 인증이나 Word 네이티브 화면 전체 호환 검증을 뜻하지 않음. 한글 폰트/서식의 XML 보존과 실제 Word 렌더 확인 범위는 구분함.

2026-10-03 최종 집중 실행 결과는 **118 passed, 16.77초**임. block/inline 선택·콤보, export/display 분리, 직접 입력·XML 특수문자, 잠금/보호/기존 값/XML 연결, 잘못된 원본 옵션, 일반 placeholder 혼합, 표시/lastValue/옵션/alias/주변 안내 변조를 포함함. 독립 검수는 원본의 선택 컨트롤을 직접 읽고 일반 SDT·셀로 위장한 프로파일과 다른 위치의 수동 조작 출력을 거부함.
