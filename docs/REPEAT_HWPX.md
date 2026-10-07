# HWPX 반복 입력행 확장

`templates.repeat_hwpx`는 원본 ZIP 부품을 메모리에서 받아 빈 입력행 또는 자리표시자 행을 복제함. 원본 파일 저장, 사용자 행 선택, 위치별 값 매핑, 최종 채우기 및 독립 검수는 상위 계층에서 수행함. 한글 실행·페이지 렌더링·법적 제출 적합성은 이 어댑터의 통과 결과로 인증하지 않음.

```python
from templates.repeat_hwpx import inventory, transform

tables = inventory(parts)  # dict[str, bytes]: 원본 HWPX ZIP 부품
changes = transform(parts, {
    "table_id": tables[0]["id"],
    "row": 2,              # 원본의 물리적 행 번호, 1부터 시작
    "count": 5,            # 원본 prototype을 포함한 최종 반복행 수
})
expanded_parts = {**parts, **changes}
```

`inventory()`의 표 ID는 `hwpx:<part>:<table xpath>`임. 각 표는 `part`, `xpath`, 원문에서 얻은 `label`, 물리적 `row_count`, `rows`를 반환함. 행에는 `index`, `editable`, 지원 불가 사유 `reason`, 실제 셀 순서의 원문 `columns`를 기록함. 가로 병합 셀은 하나의 물리적 셀로 표시함. 원문 항목명을 임의로 바꾸거나 원자료 수치를 채우지 않음.

`transform()`은 `table_id`, `row`, `count` 세 키만 허용함. 정수 `count`의 범위는 1~200이며 `True` 같은 불리언은 거부함. count=1은 검증 후 빈 dict를 반환함. 수정된 section XML 한 부품만 반환하고 입력 `parts`는 변경하지 않음. 지원하지 않는 행, 부정확한 주소, 보호, 넘치는 정수 범위는 구체적인 `ValueError`로 차단함.

## 지원 범위와 보존 계약

- 현재 실제 원본에서 확인한 `http://www.hancom.co.kr/hwpml/2011/paragraph` 네임스페이스의 `Contents/sectionN.xml` 본문 구역 직속 문단 표를 지원함.
- prototype 뒤에 count−1행을 삽입하고 기존 뒤행을 이동함. 기존 문구·표머리·합계·원본 ID와 글꼴/셀/문단 스타일 참조를 유지함. 새로운 행의 자리표시자도 그대로 복제하므로 이후 위치별 매핑이 필요함.
- 가로 병합은 지원함. 선택 행을 포함하거나 삽입 경계를 지나는 세로 병합은 차단함. 삽입 위치와 겹치지 않는 기존 위/아래 세로 병합은 rowSpan을 유지하며 아래 셀 주소만 이동함. 원본 격자의 겹침·공백·행/열 범위·물리적 순서를 검사함.
- 잠금/보호된 표·셀, 표머리로 표시된 prototype, 실제 문구·실적·합계가 들어 있는 행, 이름이 있는 셀, 연결 목록/번호/변경 추적 참조, 페이지/단 나눔, 그림/제어/중첩 표가 있는 prototype은 차단함.
- 표의 cellzoneList, label, caption 또는 알 수 없는 확장 구조는 주소·배치 갱신을 추정하지 않음. 원본에 이런 구조가 있으면 해당 표는 지원 불가 사유와 함께 표시함. 암호·배포용·서명 보안 부품/노드를 발견하면 수정하지 않음.
- 상대 높이 표, 0 높이이며 줄 배치 근거도 없는 prototype, 200,000칸을 넘는 표 격자는 지원하지 않음. 원본 및 확장 후 패키지는 압축 해제 크기 100 MiB 한도를 적용하며 복제 전에 예상 증가량도 제한함. 빈 행은 사용자가 반복할 행으로 직접 선택해야 하며 모든 빈 행을 자동으로 반복 데이터로 해석하지 않음.

## 독립 검수에서 허용할 변경

1. 선택한 표의 `rowCnt`를 실제 새 행수로 변경함. clone의 `cellAddr.rowAddr`는 실제 새 행 위치로 설정하고 기존 뒤행의 rowAddr는 count−1만큼 이동함. colAddr와 cellSpan은 유지함.
2. 원본 표의 `sz.height`에 `(count−1) × (prototype 행 높이 + 원본 cellSpacing)`를 더함. prototype 행 높이는 각 셀에서 `max(cellSz.height, max(원본 lineseg.vertpos + vertsize) + 위/아래 여백)`을 구한 뒤 셀 최댓값을 사용함. 줄 배치 캐시가 없으면 cellSz.height만 사용함. hasMargin=1인 셀은 cellMargin, 그 외는 원본 표 inMargin을 사용함. 글꼴 폭·문장 길이를 추정하는 레이아웃 엔진은 아님. 원본 셀 높이 및 clone cellSz는 그대로 보존함.
3. 원본 p/subList ID는 변경하지 않음. clone의 p ID와 비어 있지 않은 subList ID만 새로운 양의 uint32 숫자로 배정함. 모든 원본 XML 부품의 숫자 ID 및 다른 새 ID와 충돌하지 않음. 빈 subList ID는 빈 값을 유지함. 원본에 이미 있는 반복된 ID를 임의 정리하지 않음.
4. 선택 표를 포함하는 구역 직속 p와 그 뒤의 구역 직속 p에서 직속 linesegarray만 제거함. 이전 문단, 표 안 원본/복제 셀의 지역 줄 배치 캐시는 유지함. 표 확장에 따른 이후 본문 배치를 한글이 다시 계산하도록 하는 제한된 캐시 무효화임.
5. 다른 section, header, version, 스타일·글꼴·그림·관계·패키지 부품과 원본 파일 SHA는 유지함. Preview/PrvText.txt, 미리보기 이미지 및 version.xml의 작성 application 정보는 여기서 다시 만들지 않음. 최종 저장 계층에서 작성자 정보/텍스트 미리보기 정책과 native 열기·페이지 넘침을 별도로 확인해야 함.

## 확인 근거와 테스트

한컴 담당자의 [행 추가 후 무한 로딩 안내](https://forum.developer.hancom.com/t/hwpx-xml/2416/2)는 복제 셀의 rowAddr를 실제 위치로 갱신하도록 설명함. 같은 안내는 수정 application 정보를 version.xml에 기록하도록 권장함. 한컴의 [문단 레이아웃 캐시 안내](https://forum.developer.hancom.com/t/hwpx-section0-xml/2414)는 linesegarray가 선택적 줄 배치 정보이며 변경 문단에서 제거할 수 있다고 설명함. [공식 OWPML 모델](https://github.com/hancom-io/hwpx-owpml-model/blob/main/OWPML/Class/Para/TableType.h) 및 [cellAddr 모델](https://github.com/hancom-io/hwpx-owpml-model/blob/main/OWPML/Class/Para/cellAddr.h)의 rowCnt/rowAddr 정수 필드와, [본문/문단·서식 참조 구조 안내](https://tech.hancom.com/python-hwpx-parsing-2/)를 실제 2011 네임스페이스 원본과 대조함. 여기의 높이 산식과 제한된 캐시 제거 범위는 원본 크기/캐시를 보존하기 위한 구현 계약이며 한컴 렌더링 결과로 검증된 일반 해법이라고 주장하지 않음.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_repeat_hwpx.py
```

2026-10-03 기준 focused 테스트 38개 통과함. 합성 회귀는 가로/세로 병합, split-run 자리표시자, 스타일·원본 ID 유지, 새 ID 충돌과 uint32 경계, 보호/참조/구역 구조 차단, count1/200, 패키지 크기 한도, 잘못된 계획/XML을 포함함. 로컬 공식 원본 중 중기부 올인원 사업계획서와 한밭대 사업계획서의 빈 행을 확장하여 production M1로 다시 읽는 pytest 회귀 2개를 포함함. snapshot 파일이 없으면 그 실제 원본 회귀만 skip으로 표시함.

같은 날 별도 메모리/임시 파일 점검에서 `data/public_templates/`의 SHA 기준 고유 HWPX 19개·표 232개를 탐색함. 지원 후보 행 141개가 발견된 고유 원본은 13개였음. 그 13개에서 대표 빈 행 1개씩 count=3으로 확장한 결과, production M1 재파싱·원래 본문과 표 수 유지·다른 ZIP 부품 유지·원본 SHA 불변이 13/13 통과함. 후보 141행 전체를 출력/시각 검증한 수치는 아님. 나머지 6개는 조건을 충족하는 입력행이 없었음.

실제 한글 열기/렌더링, 반복행에 사용자 값 기입 후 페이지 분할·넘침, 독립 반복행 검수기의 전체 통합 회귀는 별도 검증 대상임. 이번 작업에서는 한글을 실행하지 않았으며 모델 API를 호출하지 않았음.
