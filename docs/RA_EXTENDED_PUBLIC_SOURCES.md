# 신규 제약사 공식 RA 원자료 스냅샷

2026-10-03에 EMA 공식 제품 페이지에서 관찰한 제품정보 PDF 링크를 내려받아, 기존 RA 자료에 없는 판매허가권자 2개사의 원문을 추가함. 선택 원문 복사·출처·수치·조건을 검사하기 위한 자료이며 모델 훈련, 실제 LLM 생성 평가, 국내 허가 상태 확인 또는 제출 적합성 인증으로 사용하지 않음.

등록 파일은 [ra_extended_public_sources.json](../evals/ra_extended_public_sources.json)이며 기존 `evals.ra_public.read_source` 및 `validate_source_parser` 계약을 그대로 사용함. 실제 원본은 Git에서 제외된 `data/ra_public_validation/extended/`에 보존함. 원문·공식 페이지·최종 다운로드 URL·확인 시각·SHA-256·바이트 크기·실제 PDF 페이지·회사 역할 절·선택 제품 제형을 연결함. 인용 페이지의 해시는 pypdf 추출 UTF-8 텍스트 및 디코딩한 콘텐츠 스트림의 해시이며 독립 PDF 파일 또는 전체 레이아웃 인증을 의미하지 않음.

## 공식 출처와 확인 범위

| 자료 | 회사 역할의 원문 위치 | 선택 제형 | 전체 쪽 / 선택 사실 | 관찰한 제품정보 업데이트 |
|---|---|---|---:|---|
| [Ozempic 공식 제품 페이지](https://www.ema.europa.eu/en/medicines/human/EPAR/ozempic), [관찰한 PDF](https://www.ema.europa.eu/en/documents/product-information/ozempic-epar-product-information_en.pdf) | PDF 29쪽 `7. MARKETING AUTHORISATION HOLDER` 아래 Novo Nordisk A/S | Ozempic 0.25 mg solution for injection in pre-filled pen | 143쪽 / 9사실 | 2026-08-25 |
| [Mounjaro 공식 제품 페이지](https://www.ema.europa.eu/en/medicines/human/EPAR/mounjaro), [관찰한 PDF](https://www.ema.europa.eu/en/documents/product-information/mounjaro-epar-product-information_en.pdf) | PDF 43쪽 동일 역할 절의 Eli Lilly Nederland B.V. | Mounjaro 2.5 mg solution for injection in pre-filled pen, 단회용 | 202쪽 / 8사실 | 2026-09-21 |

회사명은 각 PDF의 판매허가권자 절에서 확인함. 이 회사들을 대한민국 신청인·제조원·수입원으로 바꾸지 않음. 문서 관할은 EU임. 게시 페이지의 업데이트 날짜와 다운로드 스냅샷만 확인했으며 이후 개정, 모든 허가 변경 이력 또는 현재 국내 허가 상태는 미검증임.

| 원본 | 크기 | SHA-256 |
|---|---:|---|
| `ozempic_ema_product_information.pdf` | 1,400,861bytes | `57219aa6be0c50a4f2cc98001152ec6dcce36b4e4c6ae43650c37f58899b3d2c` |
| `mounjaro_ema_product_information.pdf` | 3,673,021bytes | `8d75eff2f10bd886e04a9cae39b5a53d6eea8dc5f9d2d23738f4b6def2646875` |

두 원본 모두 HTTP 200·PDF 서명·비암호 상태를 확인함. 공식 HTTPS 이외의 리디렉션은 거부하며 문서 보호·TLS·로그인을 우회하지 않음. 이번 시도는 다운로드 2개, 실패 0개, 제외 0개, 기존 SHA 재사용 0개, 신규 고유 원본 2개임. 원본 재배포 허용 여부는 별도 미확인이므로 로컬 검증 스냅샷으로 보존함.

## 선택 원문의 조건

제품명 전체, 선택 함량의 원료 분량, 성상, 선택한 당뇨 적응증 전체 문단, 초기·증량 및 유지 조건, 냉장 보관, 사용기간을 원문 그대로 기록함. 같은 PDF에 다른 함량·용기·적응증이 포함되어 있다는 이유로 하나의 선택 제형에 합치지 않음.

- Ozempic은 펜의 mL당 농도·펜 전체 분량·회당 분량을 구분함. 주사기의 같은 함량에서 사용한 부피를 가져오지 않음. 원료 각주도 원문 페이지와 함께 보존함.
- Ozempic 용법은 시작 용량, 4주 이후 첫 증량, 이후 각 용량에서 최소 4주 경과 조건, 0.25 mg이 유지 용량이 아니라는 문장, 주간 상한을 같은 완전 인용에 보존함. 증량 과정이 선택한 0.25 mg 펜 자체의 전달 용량을 바꾸는 것으로 해석하지 않음.
- Ozempic 미개봉 펜과 개봉 후 4회용 펜의 기간, 개봉 후 보관 온도·동결 금지·차광 문단을 별도로 기록함. 8회용 펜 및 주사기의 기간을 선택하지 않음.
- Mounjaro는 단회용 펜의 함량·부피·농도만 선택함. 같은 함량의 다회용 KwikPen에서 사용하는 회당 부피와 전체 펜 분량을 가져오지 않음.
- Mounjaro 당뇨 적응증의 연령·식이 및 운동·메트포르민 부적합 조건을 보존함. 별도 체중 관리 적응증은 이번 선택 인용에 포함하지 않음.
- Mounjaro 용법의 초기 용량, 최소 증량 간격, 성인과 10세 이상 소아의 서로 다른 유지·최대 용량을 표제와 함께 보존함. 단회용 펜의 사용 전 기간과 냉장 외 누적 보관기간·온도·이후 폐기 조건을 기록하며 KwikPen의 사용 후 기간을 가져오지 않음.

각 회사의 제품정보는 여러 용량을 다루므로 이러한 인용은 선정한 사실·문단의 검증 범위임. 전체 설명서의 금기·상호작용·신기능 등 모든 임상 조건을 대신하지 않으며 환자에 대한 치료 지시를 생성하지 않음.

## 원자료 검증과 작성 평가의 구분

[source-audit.json](../outputs/ra-extended-public/source-audit.json)에 원본 SHA 전후, 독립 판독 및 M1 전체 파싱 결과를 보존함. 2개 PDF의 전체 345쪽·198표를 운영 파서로 읽어 17개 선택 사실·쪽을 독립 판독과 대조함. 선택 사실·회사 역할이 있는 Ozempic 2·3·27·29쪽과 Mounjaro 2·3·4·42·43쪽, 총 9쪽을 PDFium으로 순차 렌더링하고 실제 PNG로 대조함. 전체 345쪽의 시각 검수 또는 네이티브 Acrobat 편집 검증을 완료했다고 표시하지 않음.

[오프라인 테스트](../tests/test_ra_extended_public_sources.py)는 전체 원문·회사 역할·페이지 해시·전체 M1 페이지·잘못된 함량·다른 페이지·다른 제형·다른 역할 및 출처 결합을 검사함. 실제 원본이 없는 환경에서는 명시적으로 skip하며 대체 mock PDF나 다운로드를 실행하지 않음. 이번 로컬 실행은 13 passed이며 [pytest.xml](../outputs/ra-extended-public/pytest.xml)에 보존함.

원자료 대조 통과와 양식 작성 통과를 구분함. [첫 작성 평가](../outputs/ra-extended-public/first-attempt/evaluation.json)는 원문 복사 rules 모드의 6시도 중 0통과를 기록함. 별지4·20의 4시도는 전체 선택값·유효 출처 24/24, 수치 22/22가 일치했으나 성상·반복 증량 간격·성인/소아 수치 문맥의 운영 검수 차단을 발견함. 별지23의 2시도는 임상시험 입력 항목명과 제품정보의 키가 달라 선택값이 없는 미대응 사례임. 실패를 숨기거나 원문을 줄여 통과시키지 않고 원래 보고서를 보존함. 현행 최종 작성 결과는 별도 [evaluation.json](../outputs/ra-extended-public/evaluation.json)과 [summary.json](../outputs/ra-extended-public/summary.json)의 실행 시각·코드 SHA·분모로 판단함.

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_ra_extended_public_sources.py --junitxml=outputs/ra-extended-public/pytest.xml
.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --manifest evals/ra_extended_public_sources.json --profile ra_law_form_4_pdf --profile ra_law_form_20_pdf --check-parser --output outputs/ra-extended-public/evaluation.json --artifact-dir outputs/ra-extended-public/artifacts
```

모델 요청·실제 API 호출·모델 응답·사람 수정 KPI 관측은 각각 0임. 원문을 복사하는 rules 평가를 모델 성능이나 실사용 수정률로 해석하지 않음. 가짜 신청인·서명·허가번호·제조소·개인정보를 추가하지 않으며, 새 자료에 대한 국내 허가·법정 필수 첨부·전자 제출 적합성·전체 제출 완료는 미평가임. 이번 자료는 공개 제품정보이며 신규 공공 양식 또는 비공개 기업 RA 신청 서류를 확보했다는 뜻이 아님.
