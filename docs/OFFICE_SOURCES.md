# 기획·재무·일상 품의·산업 품질 공개 양식

확인일은 2026-10-03 한국시간임. 실제 공식 원본 11개를 등록했으며 작성용 서식 10개와 완성 보고서 참고자료 1개를 구분함. 사무 카탈로그 안에서는 SHA-256 11개가 모두 고유함. Bosch 품질 PPTX 1개는 국제 카탈로그의 기존 원본을 재사용하므로 프로젝트 전체 신규 수집 원본은 10개임. 재사용 사본을 새 공식 원본으로 추가 집계하지 않음.

원본 캐시는 data/public_templates/office/, 원본 해시·크기 재확인 목록은 outputs/office_source_inventory.json에 보존함. 실제 다운로드 URL·원본 형식·파일명·확인일·기관·게시일·관찰 버전은 [office_catalog.json](../templates/office_catalog.json)에 기록함.

## 공식 원본과 적용 범위

| 공식 출처 | 수집 원본 | 적용 범위 |
|---|---|---|
| [국립한밭대 2026년 하반기 캡스톤디자인 지원 안내](https://www.hanbat.ac.kr/bbs/BBSMSTR_000000001565/view.do?mno=&nttId=B000000169673So6zM3r) | 세부 계획·재료비 구매·재료비 공정/결과·전문가 활용비·결과보고 HWPX 5개 | 기획·구매·비용 신청. 해당 교육과정에 참여한 확정 팀을 대상으로 하며 일반 기업 공통 품의서가 아님 |
| [한국성장금융 2026년 국민성장펀드 제출서식 수정본](https://www.kgrowth.or.kr/notice_view.asp?idx=1042&page=1&str_type=1&tab=1) | 위탁운용사 제출서류·서식 HWP/XLSX 2개 | 해당 지역전용 분야 운용사 선정용 외부 제출서식. 일반 회사 공통 재무공시 서식으로 표시하지 않음 |
| [법제처 국고금 관리법 시행규칙](https://www.law.go.kr/LSW/lsInfoP.do?lsId=009472) | 별지 13 지출결의서 PDF 1개 | 법정 지출결의 작성용. 관찰한 현행 본문 시행일은 2026-01-02이며 개별 양식의 인쇄 개정일과 분리함 |
| [법제처 의료기관 회계기준 규칙](https://www.law.go.kr/LSW/lsInfoP.do?lsId=009552) | 별지 1 재무상태표 PDF 1개 | 의료기관 회계 법정 작성용. 관찰한 현행 본문 시행일은 2021-03-05이며 모든 업종 공통 서식으로 해석하지 않음 |
| [Bosch 공식 공급망 자료](https://www.bosch.com/company/supply-chain/information-for-business-partners/) | Change Proposal Form PPTX 1개 | 회사 공개 공급사 품질 변경 제안용. 기존 국제 원본과 동일 SHA이며 새 비공개 사내 양식 확보로 집계하지 않음 |
| [포스코인터내셔널 지속가능경영보고서](https://www.poscointl.com/ccReport) | 2025 국문 보고서 PDF 1개 | 기획 보고의 구성·표현 참고자료 layout_reference. 작성용 빈 양식이 아님 |

한밭대 공고 게시일은 2026-09-02이며 지원금 신청 마감은 2026-11-13, 결과보고 제출 마감은 2026-12-14로 관찰함. 공개 모집 기한만으로 사용자 자격이나 현재 신청 가능성을 인증하지 않음. 한국성장금융 제출서식은 2026-06-01 수정본이며, 수집한 첨부 페이지에서 확인되지 않은 접수 마감은 추정하지 않음.

공개 열람·다운로드는 재배포 및 가공 허가와 별개임. 공식 사이트의 저작권·이용약관 및 개별 파일의 사용 조건을 따르며 재배포 허가는 미평가(redistribution_permitted=false)로 기록함. 원본 파일과 수집 HTML은 Git에서 제외함. 법정 서식의 최신 개정·적용 대상·서명·직인·첨부·제출기관 요구는 별도 확인해야 함.

## API와 검증

templates.office.OFFICE_WORKFLOWS는 office_planning, financial_report, daily_approval, industrial_quality의 한국어 표시명을 제공함. load_office_catalog(), list_office_templates(workflow=None), download_office_template(entry, directory=...)로 조회·필터·다운로드할 수 있음.

등록된 공식 HTTPS URL·호스트·파일명·형식·SHA를 고정하고 호출자의 변경을 거부함. 기존 캐시가 달라지면 보존하면서 재확인을 요구함. HWPX/XLSX 원본의 ZIP 내부 형식을 실제 수집 시 확인했으며 공유 다운로더는 PPTX 서명과 ppt/presentation.xml, [Content_Types].xml 존재를 검사함. HTML 접근 오류 또는 다른 Office ZIP을 PPTX로 저장하지 않음.

한국성장금융은 직접 다운로드만 요청하면 접근 오류 HTML을 반환함. 공식 공개 게시글을 정상 익명 세션으로 먼저 방문해 받은 쿠키를 이용하면 원본이 제공됨. 등록한 동일 공식 호스트의 session_page만 사전 방문하며 로그인·전자 인증·DRM을 우회하지 않음.

법정 PDF 2개와 Bosch PPTX의 등록 프로필 ID는 각각 office_treasury_voucher, office_medical_balance, office_bosch_change_proposal임. 3개 프로필에 등록한 실제 빈칸 22개(12/8/2)의 샘플 기입·독립 검증과 PDF 렌더, PowerPoint 시각 확인은 별도 검증 결과로 보존함. Bosch는 원본 12쪽 중 기입된 2쪽을 확인한 범위임. 이 범위를 원본 전체 항목 작성 완료 또는 제출 승인으로 확장하지 않음.

샘플 검증은 profile_verification.sample_fill_verified에 분리함. 카탈로그의 fill_verified, full_submission_ready, mandatory_requirements_verified, legal_compliance_certified, company_internal는 모두 false를 유지함. 개인정보·담당자·서명·동의·법적 책임 정보는 사용자 확인 값만 입력함.

사무 카탈로그·공유 다운로더·사업 및 국제 카탈로그의 오프라인 회귀 테스트 89개가 통과함. 원본 등록·캐시 검증은 모든 원본의 자동 기입이나 모든 회사의 비공개 내부 양식 호환 완료를 의미하지 않음.
