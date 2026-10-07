# 반복 표의 고정 행 검사와 동적 행 확장

DOCX·HWPX·XLSX의 확인된 입력 행을 원본 포함 1~200행으로 준비하고, 각 물리적 입력칸에 서로 다른 값을 작성함. 원본 파일을 유지하며 **원본→준비본의 행 확장 검수와 준비본→작성본의 값·서식 검수**를 각각 수행함. 구조 검사 통과는 실제 Word·한글·Excel의 페이지 배치, 업무 내용의 정답, 전체 제출 완료 또는 법적 적합성 인증을 뜻하지 않음.

## 고정 행 검사와의 관계

기존 `constraints.groups`의 `all_or_none`는 이미 존재하는 항목의 함께 입력 조건임. 그룹이 모두 비어 있으면 선택 행으로 유지하고, 하나라도 입력하면 `required_fields`를 모두 요구함. 생략 시 그룹의 모든 항목이 사용 시 필수임. 이 규칙 자체는 행을 추가하지 않음.

이번 동적 확장은 사용자가 선택한 원본 행 바로 뒤에 `count - 1`개의 복제본을 삽입함. 앞 행·표머리·합계·기존 뒤 행은 유지하며, 기존 전체 행 수가 `R`이라면 준비본은 `R + count - 1`행임. `count=1`도 안전 검수와 위치별 입력 매핑을 적용하지만 추가 행은 없음. 표 전체를 1~200행으로 재구성하거나 기존 행을 삭제하는 기능은 아님.

현재 지원은 DOCX·HWPX의 안전하게 확인되는 표 입력 행과 명시 참조의 의존성을 대조할 수 있는 XLSX 입력 행임. [Excel 계약과 보류 구조](REPEAT_XLSX.md)를 참조함. PPTX·PDF의 동적 표 생성, 임의 문서의 무제한 행 추가, 이미 작성된 행의 자동 반복은 이번 범위에 포함하지 않음.

## 화면 사용법

1. 원본 양식을 업로드하거나 공개 양식을 선택함. DOCX·HWPX·XLSX에서 **반복 표의 행 수 조정**을 열고 **작성할 표의 행 수 늘리기**를 선택함.
2. **반복할 표**와 **복제할 원본 행**을 선택함. 목록에 표시된 원문 셀 문구를 보고 실제 업무 입력 행인지 확인함. 지원 불가 구조는 이유를 표시하고 복제하지 않음.
3. **작성할 행 수 (원본 행 포함)**를 1~200으로 지정함. 사용하지 않을 행은 이 수를 줄임. 원본을 덮어쓰지 않고 `data/prepared_templates/<원본 SHA>/<계획 키>/`에 별도 준비본을 만듦.
4. **양식 항목 매핑**에서 행·열별 항목을 확인함. 예를 들어 같은 `{{품목}}`도 `품목 (반복 1행 1열)`, `품목 (반복 2행 1열)`처럼 다른 값 이름을 사용함. 원문 label은 유지함. 같은 문단에 같은 이름이 두 번 있어도 각각 독립된 위치와 값 이름을 가짐.
5. 빈 셀의 업무상 필수 여부, 직접 입력 여부, 숫자·날짜·선택 규칙을 확인함. **입력값·날짜·합계 검사 규칙 편집**에서 함께 입력할 항목과 사용 시 필수 항목을 JSON 목록으로 지정할 수 있음.
6. 직접 입력 정보를 제공하고 원자료를 첨부하여 초안을 작성·검수함. 최종 내용 확인 후 다운로드하면 준비본 바인딩을 다시 검사하고 작성본을 독립 검수함. 차단된 값·출처·매핑·구조를 해결하기 전에는 성공 결과로 제공하지 않음.

**명시적 `{{자리표시자}}`는 요청한 반복 행마다 필수임.** 한 행의 자리표시자를 모두 비워도 작성 대상으로 요청한 행이므로 통과시키지 않음. 반면 단순 빈 셀에는 업무상 필수 조건을 자동 추정하지 않음. 등록·확인 프로파일의 조건이 없으면 `required_definition_pending`을 기록하고 사용자가 필수 항목을 정의해야 함. 빈 셀을 발견한 사실만으로 그 행의 업무 의미를 학습했다고 표시하지 않음.

이어쓰기에서는 저장한 원본 SHA와 계획으로 원래 선택을 복원함. 이미 늘어난 준비본을 다시 원본으로 삼아 중복 확장하지 않음. 행 수·원본·매핑이 바뀌면 해당 준비본과 검수 결과를 다시 확인해야 함.

## 공통 API와 처리 구조

| 단계 | 모듈·API | 책임 |
| --- | --- | --- |
| 원본 후보 확인 | `templates.repeat_rows.inspect_repeat_tables(path)` | 실제 표 ID, 원본 행 번호, 셀 문구, 지원 가능 여부·이유 반환 |
| 준비본 생성 | `prepare_repeat_template(original, prepared, plan)` | 형식별 어댑터로 복제 후 독립 검수, 통과한 준비본만 원자적으로 저장 |
| 확장 독립 검수 | `agent.repeat_check.verify_repeat_expansion(original, prepared, plan)` | 변환기·filler를 호출하지 않고 raw ZIP·XML 대조 |
| 위치별 프로파일 | `templates.repeat_fields.repeat_profile(prepared, plan, binding)` | 행·열·문단·자리표시자 범위를 값 이름과 연결 |
| 원본 규칙 승계 | `templates.repeat_rules.inherit_repeat_rules(...)` | 등록·확인 매핑의 입력 위치·직접 입력·길이·타입·관계를 원본 SHA에 연결해 승계 |
| 사용자 설정 | `app.form_config` | 확인 매핑과 추가 필수·값·그룹 규칙 적용, 값 이름 변경 시 관계 재결합 |
| AI 매핑·재사용 | `agent.template_learning` | 위치를 유지한 매핑 제안, 사용자 확인 캐시 V6 저장·불러오기 |
| 최종 준비본 확인 | `verify_prepared_template(prepared, profile, expansion)` | 실제 원본·준비본·계획·프로파일 재대조, 권위 있는 물리 입력칸 재생성 검사 |
| 값 기입·출력 검수 | `fill_compatible_template(...)`, `agent.output_check.verify_output(...)` | 입력 규칙 검사, 지정 칸 기입, 재열기한 같은 위치·주변 문구·서식 대조 |

```python
plan = {
    "table_id": "docx:word/document.xml:<inventory에서 받은 표 XPath>",
    # HWPX는 "hwpx:Contents/sectionN.xml:<표 XPath>"
    # XLSX는 "xlsx:xl/worksheets/sheetN.xml", row는 실제 Excel 행 번호임
    "row": 2,       # 원본의 직접 표 행 번호: 1부터 시작
    "count": 3,     # 원본 prototype 포함 최종 반복 수: 1..200
}
```

계획은 이 세 키만 허용하며 `row`·`count`에 불리언을 허용하지 않음. table ID는 직접 만든 XPath 대신 inventory에서 받은 값을 사용함. 임의 XPath·다른 XML 부품·없는 행·모호한 표를 차단함. 독립 검수는 파일과 압축 해제 부품 합계에 64MiB 한도를 적용하고 ZIP 경로·중복 부품·암호·링크·위험 XML을 검사함. 어댑터의 별도 한도가 더 커도 최종 공통 검수 한도가 적용됨.

### 재현 예시: 샘플 DOCX 준비 후 확인된 값 작성

프로젝트 루트에서 실행함. `samples/sample_company_form.docx`의 첫 표 2행을 대상으로 하며, 실제 양식에서는 inventory 목록과 업무 역할을 먼저 확인해야 함. 아래의 `confirmed-values.json`에는 출력한 `value_key`별로 사용자가 확인한 문자열 값을 저장해야 함. 실제 자료의 출처 검수·의미 검수는 상위 pipeline에서 별도로 실행함.

```python
from hashlib import sha256
import json
from pathlib import Path

from agent.output_check import verify_output
from templates import fill_compatible_template, load_form_profile
from templates.repeat_fields import repeat_profile
from templates.repeat_rows import (
    inspect_repeat_tables, prepare_repeat_template,
    expansion_binding, verify_prepared_template,
)
from templates.repeat_rules import inherit_repeat_rules
from templates.value_rules import inspect_form_values

original = Path("samples/sample_company_form.docx")
prepared = Path("outputs/repeat-example/prepared.docx")
output = Path("outputs/repeat-example/filled.docx")
original_sha = sha256(original.read_bytes()).hexdigest()
table = inspect_repeat_tables(original)[0]
assert table["rows"][1]["editable"]
plan = {"table_id": table["id"], "row": 2, "count": 3}

expansion = prepare_repeat_template(original, prepared, plan)
profile = repeat_profile(prepared, plan, expansion_binding(expansion))
source_profile = load_form_profile(original)
if source_profile is not None:
    profile = inherit_repeat_rules(
        original, profile, source_profile, prepared_path=prepared,
    )

for field in profile["fields"]:
    print(field["value_key"], field.get("repeat_info"),
          "필수=" + str(field.get("required", False)),
          "직접입력=" + str(field.get("input_required", False)))

# 위에서 확인한 key로 사용자가 작성한 평면 문자열 JSON임.
# 선택 항목·동의·서명 등을 첫 옵션이나 추정값으로 자동 보충하지 않음.
values = json.loads(Path("outputs/repeat-example/confirmed-values.json")
                    .read_text(encoding="utf-8"))
issues = inspect_form_values(values, profile)
if issues:
    raise ValueError(issues)
prepared_check = verify_prepared_template(prepared, profile, expansion)
fill_compatible_template(prepared, values, output, profile=profile)
output_check = verify_output(prepared, output, values, profile=profile)
assert sha256(original.read_bytes()).hexdigest() == original_sha
print(prepared_check["status"], output_check["status"])
```

일반 보고서 작성에서는 `agent.pipeline`의 생성·근거·완결성 검수를 유지하고 `template_path`, `template_profile`, `template_expansion`을 함께 전달함. `build_downloads()`가 원본→준비본 검수와 준비본→작성본 검수를 연결함. 위 저수준 예시만으로 사실의 출처와 의미가 검증되었다고 표시하지 않음.

## 원본 규칙·직접 입력·캐시 V6

UI는 원본 SHA에 맞는 사용자 확인 캐시 또는 등록 프로파일이 있으면 먼저 읽고 준비본 프로파일에 규칙을 승계함. 원본 입력 위치와 준비본 위치를 대조하여 `required`, `input_required`, `input_mode`, `max_chars`, `validation`, 문체 제외 조건과 업무 메타데이터를 유지함. 숫자·날짜·합계·`calendar_date`·`all_or_none`도 새 값 이름에 연결함.

같은 반복 행 안의 관계는 각 행에 복제하고 반복 구간 밖의 관계는 그대로 유지함. 반복 행과 바깥 항목을 연결하는 관계, 여러 원본 행을 하나의 반복 구간으로 합쳐야 하는 관계, 같은 원키가 여러 물리적 값으로 나뉘어 관계의 의미를 결정할 수 없는 경우는 추정하지 않고 차단함. 기존 프로파일에 없는 새 입력칸은 원본 규칙을 확인한 칸처럼 표시하지 않고 사용자 확인 대상으로 남김.

새 `value_key`는 변경할 수 있지만 원래 물리적 ID·kind·문단·범위·원문 anchor·행/열·선택 옵션을 바꿀 수 없음. 같은 반복 입력칸의 중복 ID, 서로 다른 칸을 같은 반복 값 이름으로 합치는 매핑, 필수·직접 입력·길이·원본 validation 완화, 원래 관계·그룹 삭제를 차단함. 논리적 값 이름 변경에는 `mapped_rule_profile()`로 참조를 재결합함.

AI 매핑 캐시의 엔진 버전은 `template-mapping-6`임. 현재 원본 SHA, 사용자 확인, 엔진 버전, HMAC 무결성을 검사하며 이전 버전은 재사용하지 않음. 반복 양식은 준비본 SHA뿐 아니라 `repeat_expansion`의 원본 SHA·준비본 SHA·계획이 일치해야 재사용함. 새 행 수·계획·원본으로 만든 매핑에 이전 위치를 적용하지 않음. 저장 대상은 확인된 위치·매핑·규칙 메타데이터이며 실제 답변·개인정보·작성 값·QA 예시값을 매핑 캐시에 저장하지 않음.

DOCX native 드롭다운·콤보박스·체크박스는 원본 옵션과 미선택 상태를 유지함. 닫힌 드롭다운의 저장값과 표시 문구를 구분하고 허용값 밖의 입력을 거부함. 편집 가능한 원본 콤보박스만 사용자 자유 입력을 허용함. 선택·동의·승인·개인정보·서명은 AI가 추정하지 않고 직접 입력 조건을 유지함.

한 문단에 native 선택 컨트롤과 일반 `{{...}}` 입력칸이 섞인 경우, 원본 텍스트 위치를 기준으로 컨트롤 밖 토큰을 먼저 작성한 뒤 선택 문구를 작성함. 같은 이름의 중복 토큰과 여러 run에 나뉜 토큰도 위치별로 처리함. 컨트롤의 표시 문구나 옵션에 있는 literal `{{...}}`를 일반 입력칸으로 바꾸지 않음. 독립 검수는 별도 원문 범위와 native 경계를 대조하여 다른 칸의 값이 우연히 포함됐다는 이유로 통과시키지 않음.

## 두 단계 독립 검수와 보존 범위

첫 검수는 실제 원본과 준비본을 raw ZIP·XML로 다시 읽음. 변환기의 결과를 정답으로 재사용하거나 transformer/filler를 호출하지 않음. ZIP 부품 집합과 수정 대상 XML 부품을 고정하고, 원본 prototype·기존 앞뒤 행·다른 표·본문·속성·글꼴·병합·자리표시자 문구를 대조함. 허용된 변경만 정규화한 뒤 전체 XML을 비교함.

- **DOCX:** clone의 SDT ID와 `w14:paraId/textId`만 충돌 없는 새 값으로 허용함. 원본 ID·텍스트·행/셀/문단/run 속성·가로 병합·선택 정의·체크 상태를 유지함.
- **HWPX:** 선택 section의 표 행수와 정확한 셀 rowAddr, 새 문단/목록 ID, 원본 높이·캐시·여백에 근거한 표 높이 증가를 검사함. 선택 표를 포함하는 구역 직속 문단과 뒤 직속 문단의 직접 줄 배치 캐시만 무효화함. 원본 셀 높이와 셀 내부 캐시·스타일 참조·다른 부품을 유지함.

준비본 저장 전 검수하고 최종 출력 직전에 원본 SHA·준비본 SHA·계획·프로파일을 다시 대조함. `verify_prepared_template()`는 실제 입력 위치를 재생성하여 전역 placeholder로 반복 칸을 대체하는 우회도 거부함. 저수준 fill과 출력 검수 역시 위치·원본 규칙 guard를 적용하지만, 실제 원본까지 연결한 전체 확장 증명은 `verify_prepared_template()` 또는 이를 호출하는 pipeline을 함께 사용해야 함.

두 번째 검수는 작성본을 다시 열어 준비본의 같은 물리적 위치에 예상 값이 정확히 들어갔는지 확인함. 주변 단위·지시문·다른 행·선택 옵션·글꼴·병합·부품 보존도 검사함. 값·profile·원본·준비본을 검수 과정에서 바꾸지 않음. 검수 결과에 원본/준비본/작성본 SHA와 native 시각 검증의 별도 상태를 남김.

HWPX 준비 단계의 표 높이 계산과 제한된 캐시 삭제는 실제 한글의 페이지 재배치를 인증하는 방법이 아님. 미리보기·작성 프로그램 정보와 실제 페이지 분할·줄 넘침은 원본 프로그램에서 별도 확인해야 함.

## 안전하게 복제할 수 없어 차단하는 사례

- 활성 문서 보호·쓰기 보호·암호·DRM·전자서명, 잠긴 표/컨트롤·XML 데이터 연결. 보호나 참조를 지워서 복제하지 않음.
- 표머리·합계/소계·고정 숫자 순번, 이미 입력·선택된 native 값, 안전한 빈 입력 위치가 없는 행.
- 선택 행 또는 삽입 경계를 지나는 세로 병합. 확인된 가로 병합과 경계에 걸리지 않는 다른 기존 행의 병합은 보존함.
- prototype 안의 그림·관계·중첩 표·필드·북마크·주석·각주·미주·변경 추적·권한 범위·알 수 없는 ID/참조 구조.
- DOCX의 미지원 date/picture/group/repeating-section 컨트롤, 상태가 명확하지 않은 체크박스, 중복·잘못된 native 선택 정의.
- HWPX의 중첩 표, 본문 구역 직속 문단에 있지 않은 표, 이름 있는 셀, cellzoneList/label/caption·연결 본문/번호·추적 참조, 상대 높이·유효 높이 근거가 없는 행, 200,000칸을 넘는 격자.
- 행 삭제·잘못된 count·표/부품 바꿔치기·다른 ZIP 부품 변경, clone의 글꼴/문구/병합 변조, ID 충돌, HWPX 행수·주소·높이·캐시 위반.

일반 텍스트를 고정 라벨과 기존 입력 값으로 완벽히 구별하지 않음. DOCX 후보에 빈 칸이 있더라도 사용자가 실제 반복 업무 행인지 확인해야 함. 안전 검사를 통과하지 못한 양식은 지원 불가 또는 확인 필요로 남기며 제출 가능한 결과로 꾸미지 않음.

## 재현 검사와 현재 확인 범위

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_repeat_docx.py tests/test_repeat_hwpx.py tests/test_repeat_check.py tests/test_repeat_fields.py tests/test_repeat_rows.py tests/test_repeat_integration_security.py tests/test_repeat_native_inline.py
```

확장·위치별 매핑·준비본 바인딩·저장 후 독립 검수·UI 선택과 native 혼합 문단을 검사함. 합성 fixture 및 로컬 공식 snapshot을 사용하며 pytest에서 모델 API를 호출하지 않음. 원본 snapshot이 없는 실제 자료 사례는 skip으로 기록함. 입력 데이터 validation, 규칙 승계와 전체 프로젝트 회귀의 최종 집계는 아래 별도 상태를 갱신해야 함.

| 확인 항목 | 현재 기록 | 범위·보류 |
| --- | --- | --- |
| 공식 HWPX 대표 입력 행 | 고유 19원본 중 후보가 있는 13원본; [독립 검수 보고서](../outputs/repeat-rows/hwpx-independent.json)의 13/13 통과, 114개 반복 칸에 합성 QA 값 작성 | 준비·독립 확장 검수·위치별 기입·저장 후 독립 검수·M1 재열기·SHA 불변. 나머지 6원본은 지원 후보 없음. 모든 표/행을 검사한 분모가 아님 |
| 공식 HWPX native 한글 확인 | `pending / 미평가` | 실제 한글 열기·페이지 분할·넘침을 확인하지 않음. 13원본의 선택 칸에는 native 선택 컨트롤 0개임 |
| DOCX native 선택+inline token 혼합 | 합성 회귀 18개 통과 | 앞/뒤 선택 컨트롤·표시 길이 변경·literal 토큰·중복/split-run 위치 검사. 일반 DOCX 전수 호환 인증이 아님 |
| 공식 공개 DOCX 2원본의 Word QA | 반복 21칸 작성·독립 검수 2/2, Word PDF 6개/30쪽 시각 확인 | KOTRA 1쪽×3상태와 서울과기대 창업계획서 9쪽×3상태의 모든 페이지를 비교함. 짧은 QA 값에서 겹침·잘림을 발견하지 않음. [페이지·PNG·원본 SHA 기록](../outputs/repeat-rows/docx-native/word-visual.json). 장문/모든 항목 완료 검증은 아님 |
| 직전 DOCX/HWPX 전체 pytest·규칙 승계 | 2,046 passed, 1 skipped, 2 warnings, 319.29초 | `outputs/pytest-repeat-rows.xml`의 최종 전체 실행임. skip은 원자료 PDF 추출 권한 제한이며 보호 차단 회귀는 통과함. 원본 규칙·AI 제안·캐시·UI·최종 출력까지 포함함 |
| 최신 XLSX 포함 전체 회귀 | 2,272 passed, 1 skipped, 2 warnings, 363.97초 | 코드/프롬프트172파일 SHA 불변. [XLSX 실물/계산/인쇄 범위](REPEAT_XLSX.md)와 `outputs/repeat-xlsx/verification.json` 참조 |
| 실제 LLM 생성·사람 수정 KPI | 이 구조 QA의 모델 응답 0, API/네트워크 호출 0 | QA 예시값 작성이며 실제 사용자 성능·수정률·실모델 정확도를 측정한 결과가 아님 |

공식 snapshot의 현재 SHA와 보고서에 기록된 코드 SHA를 함께 확인해야 함. 카탈로그의 공고 기한·서식 버전·권한은 별도 출처 기록을 따르며, 일부 반복 칸 작성 성공을 현재 신청 가능·전체 기입 완료·기업/기관 전체 호환이라고 표시하지 않음.

형식별 상세 계약은 [DOCX 어댑터](REPEAT_DOCX.md), [HWPX 어댑터](REPEAT_HWPX.md), [XLSX 어댑터·Excel 확인](REPEAT_XLSX.md), 선택 규칙은 [DOCX 선택 컨트롤](DOCX_CHOICES.md), 실물 타입·관계 근거는 [입력값 검증](FIELD_VALIDATION.md)을 참조함.

반복 매핑은 `repeat/<원본 SHA·준비본 SHA·계획의 해시>/` 안에 저장함. 1행 준비본이 원본과 같은 바이트여도 원본의 확인 매핑을 덮어쓰지 않으며, 원본 규칙 snapshot이 달라지면 이전 매핑을 재사용하지 않음. AI 제안의 필수·직접 입력·분량·숫자/날짜 제약도 기입 전에 원본 권한으로 다시 검사함.
