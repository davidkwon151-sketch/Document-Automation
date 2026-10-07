# RA 실제 공개 제품정보 원자료

2026-10-03 한국시간에 서로 다른 EU 판매허가권자 3개사의 공식 제품정보 PDF를 실제 다운로드함. 제품별 사실 14개를 PDF 실제 페이지와 대조하여 [ra_public_sources.json](../evals/ra_public_sources.json)에 저장함. 원본은 data/ra_public_validation/에 보관하며 원본 파일명·다운로드 URL·SHA-256·크기·페이지 수·확인일·선택 제형·회사 역할을 함께 기록함.

## 확보한 공식 자료

| 제품 선택 범위 | PDF에서 확인한 회사·역할 | 공식 출처·버전 관찰 | 원본·선택 위치 |
|---|---|---|---|
| Herzuma 150 mg 주입용 분말 바이알 | Celltrion Healthcare Hungary Kft. / EU marketing authorisation holder | [EMA Herzuma 페이지](https://www.ema.europa.eu/en/medicines/human/EPAR/herzuma), [제품정보 PDF](https://www.ema.europa.eu/en/documents/product-information/herzuma-epar-product-information_en.pdf); 제품정보 항목 업데이트 2026-08-28 | 66쪽. 제품명·성상 2쪽, 초기·유지·3주 간격 용법 4쪽, 미개봉 바이알 사용기간 29쪽, 허가권자 32쪽(역할 제목은 31쪽) |
| Benepali 25 mg 프리필드시린지 | Samsung Bioepis NL B.V. / EU marketing authorisation holder | [EMA Benepali 페이지](https://www.ema.europa.eu/en/medicines/human/EPAR/benepali), [제품정보 PDF](https://www.ema.europa.eu/en/documents/product-information/benepali-epar-product-information_en.pdf); 제품정보 항목 업데이트 2025-12-01 | 136쪽. 제품명·주사기당 함량·성인 류마티스관절염 효능 2쪽, 주2회 권장·주1회 대안 용법 3쪽, 사용기간 30쪽, 허가권자 31쪽 |
| Keppra 250 mg 필름코팅정 | UCB Pharma SA / EU marketing authorisation holder | [EMA Keppra 페이지](https://www.ema.europa.eu/en/medicines/human/EPAR/keppra), [제품정보 PDF](https://www.ema.europa.eu/en/documents/product-information/keppra-epar-product-information_en.pdf); 제품정보 항목 업데이트 2026-04-01 | 170쪽. 제품명·정제당 함량·16세 이상 단독요법 효능·성인 및 체중 조건이 있는 청소년 용법 2쪽, 사용기간·허가권자 17쪽 |

회사 역할은 각 PDF의 판매허가권자 항목에서 확인한 범위임. 셀트리온의 헝가리 법인과 삼성바이오에피스의 네덜란드 법인을 한국 신청인·수입자·제조원·개발사로 바꾸어 기재하지 않음. 한국 신청인의 이름·주소·서명·직인 등 비공개 정보를 사용하지 않음.

## 국내 자료 우선 탐색과 대체 경위

[셀트리온제약 허쥬마 제품 페이지](https://www.celltrionph.com/ko-kr/product/introducedetail?modify_key=34)에서 공식 제품 HTML을 관찰했으나 제품정보 PDF 링크는 확보하지 못함. 삼성바이오에피스 공식 기업 소개·제품 포트폴리오를 확인했으나 이번 수집에서 제품별 국내 허가 PDF를 확보하지 못함. 검색에 노출된 한국 UCB 케프라 주사제 공식 PDF 직접 링크는 실제 HTTP 404를 반환했으며 다운로드 성공으로 기록하지 않음.

이후 공식 EMA 제품 페이지에서 관찰한 PDF 링크로 대체함. 한국 UCB의 삭제된 주사제 PDF와 확보한 EMA 250 mg 정제 자료는 제형·관할이 다르며 같은 국내 허가 원자료로 간주하지 않음. 국내 품목허가 검증 상태는 모두 false이고 확보 자료의 jurisdiction은 모두 EU임.

## 원문 사실과 긴 문단 보존

제품명·함량·성상·사용기간의 짧은 사실 9개와 효능·용법의 완전한 선택 문단 5개를 보존함. 값과 exact_quote에는 선택 원문을 그대로 저장하고 실제 1부터 시작하는 PDF 페이지 번호를 기록함. 공백과 줄바꿈만 정규화하여 해당 페이지에서 원문 일치를 확인했으며 문장을 요약·절단하거나 단위·투여 횟수를 환산하지 않음.

field_key는 별지4 프로필의 제품명, 원료약품 및 분량, 성상, 효능 효과, 용법 용량, 저장방법 및 유효기간과 맞춤. 사용기간 원문은 저장방법 및 유효기간 필드에 연결하되 source_field_label과 shelf_life 역할을 기록하여 보관 조건까지 확보했다고 오해하지 않게 함.

Herzuma 용법 문단은 전이성 유방암의 3주 간격 초기·유지 용량을 선택함. 같은 문서의 주간 요법과 다른 질환·제형·재구성 단계는 합치지 않음. Benepali는 성인 류마티스관절염의 메토트렉세이트 병용 및 기존 치료 반응 조건, 25 mg 주2회 권장과 50 mg 주1회 대안을 함께 보존함. Keppra는 신규 진단·16세 이상 단독 효능과 별도의 성인·체중 조건이 있는 청소년의 초기 및 의사 판단에 따른 낮은 초기용량 문단을 보존함. Keppra의 twice daily는 1일 2회이며 Benepali의 twice weekly와 구분함.

각 긴 사실에는 clinical_scope, role, complete_selected_paragraph, overflow_policy를 기록함. 원문이 기존 양식 칸의 분량을 넘으면 원필드 참조와 전체 문단 별첨 또는 기입 차단으로 처리해야 함. 전체 문단에서 연령·체중·치료 대상·투여 간격·초기와 유지 단계·의사 판단 조건을 제거한 짧은 문장을 만들지 않음.

각 PDF 2쪽을 PDFium으로 렌더하여 제품명·함량·제형·선택 임상 문단이 실제 페이지에 있는지 시각 확인함. PNG는 data/ra_public_validation/previews/에 보관함. 이 확인은 공개 원문 위치의 확인이며 작성된 국내 허가 양식의 전체 기입·형식 보존 검증과는 별개임.

## 검증 범위

EMA 웹페이지의 제품정보 업데이트 날짜는 한국 지침 개정일·법정 별지 시행일·국내 허가일이 아님. 원본 스냅샷의 명확한 사실과 긴 문단을 보존했으며 실제 한국 허가 제출 적합성을 인증하지 않음. 모델 학습, 외부 LLM 호출 및 실제 API 정확도 평가는 수행하지 않음. 이 자료만으로 RA 업무 전체 정확도를 검증했다고 표시하지 않음.

원본 SHA-256과 크기, PDF 페이지 수, 회사 역할 및 선택 사실 14개를 로컬 원본과 대조함. 원문 PDF는 Git에서 제외하고 공개 열람 가능성만으로 재배포 허가를 추정하지 않음. 개별 원본의 이용 조건은 별도 확인해야 함.

이후 실제 M1 전체 파싱과 국내 양식 2종 × 회사 3곳의 기입·별첨·출력 검수를 수행함. 분모별 결과와 미확인 범위는 [RA 실자료 검증](RA_VALIDATION.md)에 기록함. 이 후속 검사도 실제 모델 평가·국내 제출 적합성과 구분함.
