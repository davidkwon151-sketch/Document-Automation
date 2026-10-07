# DOCX 반복 입력 행

`templates.repeat_docx`는 원본 표의 입력 행을 그대로 복제함. 새 내용을 작성하거나 합계·순번·선택값을 자동 생성하지 않음. 표를 다시 만들지 않으며 ZIP 읽기·원자적 파일 저장·행별 값 매핑·출력 검수는 호출자가 맡음.

## 어댑터 계약

```python
inventory(parts: dict[str, bytes]) -> list[dict]
transform(parts: dict[str, bytes], plan: dict) -> dict[str, bytes]

plan = {"table_id": "docx:word/document.xml:<원본 표 XPath>",
        "row": 2, "count": 5}
```

`row`는 원본 직접 `w:tr`의 1부터 시작하는 번호이며 `count`는 원본 prototype를 포함한 최종 반복 수(1..200)임. 원본 행 바로 뒤에 `count - 1`개를 추가하고 기존 뒤 행은 순서를 유지해 이동함. `count=1`은 같은 안전 검사를 거친 후 `{}`를 반환함. 반환 dict에는 실제 변경한 XML 부품만 포함함. 입력 dict와 원본 파일은 변경하지 않음.

inventory 레코드는 `id`, `part`, `xpath`, `label`, `row_count`, `rows`를 제공함. 각 행은 `index`, `editable`, `reason`, `columns`를 제공하며 columns는 원본 셀의 문구임. 빈 칸·명시 `{{...}}`·미입력 SDT가 있는 행을 후보로 표시함. 일반 텍스트가 업무상 고정 라벨인지 이미 작성된 값인지는 자동 인증하지 않으므로, 사용자가 반복할 업무 행인지 확인해야 함. 예산 예시 행이나 고정 의안 행의 빈 칸만을 이유로 반복해서는 안 됨.

## 보존과 허용된 변경

원본 prototype와 기존 앞·뒤 행, 표 머리글·합계 문구·정적 본문은 그대로 유지함. 복제본의 문구·자리표시자·글꼴·런·단락·행/셀 속성·가로 병합·목록 제안·체크 상태는 원본과 동일함. 서로 다른 행 값을 위한 자리표시자 이름 변경은 이 어댑터에서 하지 않음.

복제본의 다음 식별자만 새 값으로 변경함. 원본 행의 식별자는 그대로 유지함.

| 위치 | 새 값 |
| --- | --- |
| `w:sdtPr/w:id/@w:val` | 기존 모든 word XML 부품·새 복제본과 충돌하지 않는 양수 decimal |
| `@w14:paraId`, `@w14:textId` | 기존 모든 word XML 부품·새 복제본과 충돌하지 않는 8자리 hex, `0 < 값 < 0x80000000` |

[Microsoft SDT ID 정의](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.sdtid?view=openxml-3.0.1)는 입력 컨트롤의 고유 수치 ID를 명시함. [Microsoft paraId 정의](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-docx/a0e7d2e2-2246-44c6-96e8-1cf009823615)는 위 양수 hex 범위를 명시함. 새 textId도 같은 좁은 범위에서 생성하며 원본의 유효한 8자리 textId 문구는 그대로 둠.

미입력 일반 SDT·드롭다운·콤보박스와 명시적으로 미선택인 native 체크박스를 복제함. 드롭다운 export/display 옵션 정의와 미선택 표시를 유지하며 첫 옵션을 선택하지 않음. 체크 상태 `0`/`false`/`off`를 그대로 보존함. 선택·동의·승인 및 개인정보는 후속 단계에서 사용자 입력으로 처리해야 함.

## 원본 기반 차단

- 활성 문서 보호·쓰기 보호·패키지 전자 서명, 표/입력 컨트롤의 잠금·XML 데이터 연결
- 표 머리글, 합계·소계 시작 문구, 첫 셀에 인쇄된 고정 숫자 순번, 입력칸이 없는 행
- prototype 내부의 세로 병합 및 바로 다음 행으로 이어지는 continuation 경계
- 필드·북마크·주석·각주·미주·권한 범위·하이퍼링크·그림·관계 속성·변경 추적과 알 수 없는 ID 속성
- 중첩 표를 포함한 prototype 및 직접 행 이외 wrapper가 있는 표; 개별 내부 표의 안전한 행은 따로 선택 가능
- 기존 입력/선택 완료 SDT, 미선택으로 확인되지 않은 native 체크박스, date/picture/group/repeating section, 잘못된 옵션·식별자 형식

막힌 구조의 보호·참조를 삭제하거나 임의로 빈 입력으로 바꾸지 않음. 일반 글자 `□` 등 인쇄 기호의 결정 의미와 반복 행의 법정 제출 적합성은 이번 어댑터가 인증하지 않음.

## 검증 범위

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_repeat_docx.py tests/test_docx_choices.py tests/test_output_check.py
```

테스트는 `tmp_path`에 만든 합성 DOCX로 최대 200행, 스타일·가로 병합·앞뒤 문구·ID 고유성·옵션/체크 보존·위험 구조 차단과 python-docx 재열기를 확인함. API 키·LLM·네트워크 없이 실행함. XML 보존·재열기 성공은 실제 Word 페이지 배치·표 페이지 나눔·네이티브 렌더 검증과 다르며, 네이티브 Word 시각 확인은 별도 미확인으로 남음.

2026-10-03 집중 회귀 결과는 **142 passed, 18.17초**임. 반복행 49개 및 기존 DOCX 선택·출력 검수 회귀를 함께 실행함.

기확보 공식 공개 blank-form의 XML 관찰도 수행함. [KOTRA 디지털로드쇼 공고](https://www.kotra.or.kr/subList/20000020753/subhome/bizAply/selectBizMntInfoDetail.do?cpbizYn=N&dtlBizMntNo=26PC016)의 `business_kotra_digital_2026.docx`는 SHA `105a6bd0abdf2b4b2231cbf7129f3bf3cce3247907b334bb3751b54b4be00827`임. 표 1, 13행은 2칸 모두 빈칸이며 마지막 행의 “밑으로 추가 해주시면 됩니다.”를 유지해 15→17행으로 확장 가능함. [서울과학기술대학교 초기창업패키지 공고](https://sssf.seoultech.ac.kr/community/notice/?bidx=844338&bnum=57917&cate=7&do=view&profboardidx=0)의 `business_startup_2026.docx`는 SHA `7defedeaaf2c47a0127db784d3ded6a04efc2e9b236e47b91c073209eefc0d9f`임. 본문 표 5, 15행은 팀 구성 현황의 5칸 모두 빈칸이며 원본 예시 1·2행을 유지해 16→18행으로 확장 가능함. 원본을 저장하지 않고 메모리에서 변환함. 이 관찰은 전체 작성·제출 가능·최신 공고 적합성 확인이 아니며, KOTRA 해당 공고는 2026-09-07 마감으로 기한이 경과함.
