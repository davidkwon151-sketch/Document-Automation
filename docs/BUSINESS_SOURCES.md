# 해외영업·해외사업·정부지원 공개 양식

확인일은 2026-10-03 한국시간임. 실제 다운로드한 공식 원본 17개를 등록했으며 작성용 서식 16개와 원가계산 참고 가이드 1개를 구분함. 모든 대표 원본의 SHA-256이 고유하며 기존 캐시의 해시·크기를 재확인함. 원본은 `data/public_templates/business/`, 검증 목록은 `outputs/business_source_inventory.json`에 보존함.

## 공식 원본

| 출처 페이지 | 수집한 원본 | 분류·확인 범위 |
|---|---|---|
| [중기부 제품화 올인원팩 공고](https://www.mss.go.kr/site/smba/ex/bbs/View.do?bcIdx=1067431&cbIdx=310&parentSeq=1067431) | 사업계획서 HWPX 1개 | 정부지원, 2026년 공고 첨부 |
| [충남중기청 기업성장 어드바이저](https://www.mss.go.kr/site/chungnam/ex/bbs/View.do?bcIdx=1068456&cbIdx=315&parentSeq=1068456) | 참가 신청서 HWPX 1개 | 정부지원, 충남 소재 등 해당 공고의 자격 확인 필요 |
| [경기북부 인도 유통망 진출 공고](https://www.mss.go.kr/site/gyeonggi/ex/bbs/View.do?bcIdx=1070654&cbIdx=247) | 신청 XLSX, 공고·신청·소개서 HWPX 2개 | 해외영업·해외사업·정부지원, HWPX에는 안내와 작성 서식이 함께 있음 |
| [KOTRA WHS 수소 상담회](https://www.kotra.or.kr/subList/20000020753/subhome/bizAply/selectBizMntInfoDetail.do?cpbizYn=N&dtlBizMntNo=26CN0EF) | 신청서 HWP 1개 | 해외영업·해외사업 |
| [KOTRA 중고차·부품 수출상담회](https://www.kotra.or.kr/subList/20000020753/subhome/bizAply/selectBizMntInfoDetail.do?cpbizYn=N&dtlBizMntNo=26CN0G8) | 참가 신청서 XLSX 1개 | 해외영업·해외사업 |
| [IRIS 호남권 연구단 공고](https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId=024160) | 협약시 제출 서식모음 HWP 1개 | 국가 R&D·정부지원, 협약 단계 서식이며 신규 신청계획서로 바꿔 표시하지 않음 |
| [IRIS 양자정보과학 리더급 공고](https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId=022977&ancmPrg=ancmPre) | 별첨자료 서식 HWP 1개 | 국가 R&D, 연구개발계획서 ZIP과 구분함 |
| [중진공 탄소중립 설비투자 공고](https://esg.kosmes.or.kr/esgplatform/board/board13View.do?idx=1326) | 원가계산기관 신청서·사업계획서 HWP 1개 | 정부지원, 지원기업용과 수행기관용을 혼동하지 않음 |
| [기업마당 농산업 스마트공장 공고](https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId=PBLN_000000000118739) | 사업계획 HWPX, 자가진단 XLSX, 정보활용동의 HWP, SW개발비 XLSX, 원가 가이드 PDF 5개 | 정부지원, 가이드 PDF는 `layout_reference`이고 작성 양식 수에 포함하지 않음 |
| [서울과기대 초기창업패키지 공고](https://sssf.seoultech.ac.kr/community/notice/?bidx=844338&bnum=57917&cate=7&do=view&profboardidx=0) | 사업계획 DOCX 1개 | 정부지원, 공식 주관기관 첨부 |
| [KOTRA 일본 Salesforce 피칭](https://www.kotra.or.kr/subList/20000020753/subhome/bizAply/selectBizMntInfoDetail.do?cpbizYn=N&dtlBizMntNo=26CN0FW) | 참가 신청 XLSX 1개 | 해외사업, 2026-06-19 접수 종료 |
| [KOTRA 방산 디지털로드쇼](https://www.kotra.or.kr/subList/20000020753/subhome/bizAply/selectBizMntInfoDetail.do?cpbizYn=N&dtlBizMntNo=26PC016) | 발표 스크립트 DOCX 1개 | 해외영업, 2026-09-07 접수 종료 |

관찰된 직접 다운로드 URL, 파일명·형식·크기·해시, 출처 페이지·기관, 확인일, 공고 버전, 확인된 마감일은 [business_catalog.json](../templates/business_catalog.json)에 기록함. 자료 수집만으로 현재 모집 여부를 승인하지 않으며 `application_status`는 기한 경과가 확인된 `closed` 또는 `not_assessed`임. 게시일이 확인되지 않은 원본에는 게시일을 추정하지 않음.

이 자료는 공식 외부 사업 제출용 서식임. 민간 종합상사의 비공개 사내 기안·사업계획 원본을 확보한 범위로 표시하지 않음. 개인·기업 정보 동의, 서명, 직인, 계좌 및 담당자 정보는 사용자가 확인한 값만 입력하도록 별도 보호함.

## API와 검증

`templates.business.BUSINESS_WORKFLOWS`는 `trade_sales`, `overseas_business`, `government_grant`, `rd_project`의 한국어 표시명을 제공함. `load_business_catalog()`, `list_business_templates(workflow=None)`, `download_business_template(entry, directory=...)`를 제공함.

기존 공유 다운로드 함수를 사용하고, 등록된 공식 HTTPS URL·호스트·해시를 호출자가 바꿀 수 없도록 확인함. 변조된 캐시를 덮어쓰지 않으며 HTML 로그인 응답, 경로 이탈, 형식 서명 불일치를 차단함. 실제 수집 시 DOCX/XLSX/HWPX ZIP 내부 부품도 확인함. KOTRA 경제외교 포털 일부는 TLS 인증서 호스트 불일치로 수집하지 않았으며 인증서를 무시하지 않음.

초기창업 DOCX·Salesforce XLSX·디지털로드쇼 DOCX의 3개 입력 프로필은 각각 별도의 실제 샘플 기입 검증 결과를 연결함. 전체 항목 충족·제출 적합성을 의미하지 않으며 카탈로그의 `fill_verified`, `full_submission_ready`, `mandatory_requirements_verified`, `legal_compliance_certified`는 모두 `false`임. HWP 원본의 현대형식 변환이나 모든 원본의 자동 기입까지 완료한 것으로 표시하지 않음.

`.venv\Scripts\python.exe -X utf8 -m pytest tests/test_business_catalog.py -q`의 오프라인 테스트 23개가 통과함. 모든 공식 원본 재배포 조건은 미평가로 기록하고 Git에서 제외함. 공개 다운로드 가능성만으로 재배포 허가를 추정하지 않음.
