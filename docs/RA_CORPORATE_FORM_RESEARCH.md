# 기업 공개 RA·품질 양식 표본 조사

2026-10-03 확인. 8개 기업을 검색하여 공식 공개 빈 파일 **3종·21쪽**을 확보함. Roche 1종의 공급자 작성란 **33칸**을 부분 기입하고 독립 검수·PDFium 시각 확인을 수행함. 전항목 제출 완료·회사 비공개 내부 양식·법적 적합성 인증 건수는 **0**임.

원본·검색어·검색 URL·전체 canonical 필드·SHA·판독 기록은 [manifest](../evals/ra_corporate_sources.json)에 기록함. 원본은 `data/public_templates/ra_corporate/`, QA는 `outputs/ra_corporate_form_qa/`에 보관함. 공개 접근과 재배포 허가는 별도임.

| 기업 | 실제 원본 관찰 | 기입 상태와 범위 |
|---|---|---|
| Pfizer Canada | 공식 공급업체 등록·품질보증·윤리·조달 설문 PDF 12쪽. 인쇄 버전 2017-01-20, 95개 canonical 필드와 widget 관찰함. | 전체 읽기 검증만 수행함. `/Perms/UR3` Adobe 사용권 인증 서명 때문에 현재 엔진은 기입을 차단함. 보호를 제거하지 않음. [공식 원본](https://channels.pfizer.ca/sites/default/files/Pfizer_Canada_Supplier_Survey_distributed.pdf) |
| Roche | 공식 사이트의 지역별 공급자 지침이 직접 연결한 품질 변경관리 PDF 6쪽. 인쇄 버전 2023-06-30 / 04임. | A~F 공급자 텍스트 33칸에 가짜 시험값을 기입함. G의 Roche 결정·서명과 모든 선택칸을 유지함. 진단·품질 업무 문맥이며 신약 판매허가 신청서가 아님. [공식 출처 페이지](https://suppliers.roche.com/suppliers/site-specific-instructions), [원본 PDF](https://assets.roche.com/f/94122/x/9c208e373e/supplier-initiated-change-request-form.pdf) |
| Terumo Medical | 공급자 변경통지 및 회사 내부 품질·의료기기 RA 평가를 포함한 PDF 3쪽, Rev 12임. | 전체 읽기와 3쪽 시각 관찰을 수행함. PDF에는 정적 Word 입력 안내 문자열이 인쇄되어 있으며 원문은 Word 제출을 지시함. 이번 기입 검증은 미평가임. [공식 원본](https://www.terumomedical.com/en-us/support/08-1TFORM-04-Supplier-Change-Notification-Form.pdf) |

## 실제 제품정보 교차 기입

Roche의 1쪽 재료 설명 1~3행에 Herzuma 150 mg, Benepali 25 mg, Keppra 250 mg의 **전체 EMA 제품명**을 원문 그대로 기입함. 세 EMA PDF의 SHA·실제 인용 페이지·전체 인용과 필드 연결을 [별도 출처 기록](../outputs/ra_corporate_form_qa/roche_ema_cross_fill_sidecar.json)에 유지함. 원자료는 기존 [공식 제품정보 수집 기록](RA_PUBLIC_SOURCES.md)을 사용함.

이는 서로 다른 기업 원문의 글자·영문 문체·행·서식 보존 시험임. 공급자 이름·재료 식별자·계약조건·변경 설명·영향평가·승인란을 채우지 않았음. 해당 약품이 Roche에 공급되거나 변경 영향을 받는다는 사실을 증명하지 않음. 외국 승인 정보를 국내 허가 상태나 회사별 신청인 정보로 대입하지 않음.

가짜 33칸 결과와 원문 제품명 결과 모두 원본 6쪽·표·안내문·로고·입력 영역 밖 픽셀 및 전체 입력 문구의 독립 검사를 통과함. 원본 6쪽과 가짜 결과 6쪽, 제품명 교차 결과 첫 쪽을 PDFium PNG로 직접 확인함. 긴 내용은 기본 차단하며 임의 축약·필수 항목 생략으로 제출 가능하다고 표시하지 않음.

## 이번 검색에서 미확보한 기업

- **Novartis:** 공식 페이지는 GxP 공급자 평가·품질협약 절차 및 Ariba 등록을 안내함. 이번 검색에서 독립 공개 RA/품질 빈 파일 양식은 확보하지 못함. 정책·구매조건·완료보고서를 빈 양식으로 집계하지 않음. [품질 안내](https://www.novartis.com/about/quality/third-party-suppliers), [공급자 문서 안내](https://www.novartis.com/supplier-portal/documentation)
- **Sanofi:** 공급자 HTML 등록과 정책·구매조건은 확인함. 별도의 공개 RA/품질 빈 파일 양식은 이번 검색에서 미확보함. [공급자 포털](https://suppliers.sanofi.com/), [정책 안내](https://suppliers.sanofi.com/en/standards-and-procedures)
- **유한양행·한미약품·셀트리온:** 공식 도메인 검색에서 회사 빈 RA/품질 양식 원본을 확보하지 못함. 제품정보·변경 공지·QA 보도자료·완료 ESG 보고서를 빈 입력 양식으로 집계하지 않음. 검색어와 검색 URL은 manifest에 남김. 미확보 결과는 공개 양식 자체의 부재나 회사 내부 양식 미보유를 뜻하지 않음.

현재 접근한 공개 파일의 인쇄 버전과 회사 내부 최신 버전은 다를 수 있음. 한국 직원의 실제 사용 여부, 특정 계약에 대한 적용 여부, 회사 비공개 RA 문서 전체와의 동일성은 검증하지 않음. 체크박스·서명·승인 판단·제출 첨부·실제 변경 시행 허가는 담당자가 확인해야 함.
