# 외국계·국제 업무 양식의 공식 공개 출처

2026-10-03 한국 시간에 아래 공식 페이지의 링크를 확인하고, 원본을 직접 읽어 실제 파일 형식과 SHA-256을 기록했음. `templates/international_catalog.json`·검증된 원본 다운로드 API·화면의 공개 양식 선택에 연결함. 직원용 비공개 보고·기안 양식을 확보하거나 회사의 모든 양식을 검증한 것으로 표시하지 않음. 신청·공급업체 등록·서명·승인 권한과 제출 자격은 사용자가 확인해야 함.

| 공식 게시 기업 | 작성용 공개 문서 | 확인된 형식·언어 | 공식 출처와 원본 | 확인 범위 |
|---|---|---|---|---|
| Siemens Healthineers | 제품 데이터 제출 서식 C | DOCX·영어 | [공식 다운로드 페이지](https://www.siemens-healthineers.com/business-opportunities/suppliers/download-center), [영어 원본](https://marketing.webassets.siemens-healthineers.com/6044a4a1e1b3f2fc/f1cfe5d01f41/siemens-healthineers-business-opportunities-Form_C_V05_EN.docx) | 실제 Word 본문에 기입·서명 안내와 빈 작성 영역을 확인함. 공급업체 제출용 서식임. |
| Siemens Healthineers | 제품 데이터 제출 서식 C 독일어판 | DOCX·독일어 | [공식 다운로드 페이지](https://www.siemens-healthineers.com/business-opportunities/suppliers/download-center), [독일어 원본](https://marketing.webassets.siemens-healthineers.com/cf13e0bcffcd90ab/15a37aa9d1f3/siemens-healthineers-business-opportunities-Form_C_V05_DE.docx) | 실제 Word 원본을 확인함. 독일어 전체 항목 해석과 기입 결과는 별도 검증해야 함. |
| Robert Bosch GmbH | 미국 공급업체 다양성 소개 서식 | XLSX·영어 | [공식 사업 파트너 페이지](https://www.bosch.com/company/supply-chain/information-for-business-partners/), [Excel 원본](https://assets.bosch.com/media/global/bosch_group/purchasing_and_logistics/information_for_business_partners/supplier-diversity-form-usa.xlsx) | 지침·작성 서식·연락 정보 시트를 확인함. 해당 미국 사업의 자격을 충족한 업체용이며, 제출이 공급업체 등록을 보장하지 않음. |
| Robert Bosch GmbH | 공급업체 변경 제안 서식 | PPTX·영어 | [공식 사업 파트너 페이지](https://www.bosch.com/company/supply-chain/information-for-business-partners/), [PowerPoint 원본](https://assets.bosch.com/media/global/bosch_group/purchasing_and_logistics/information_for_business_partners/downloads/quality_docs/specific_regulations/change-proposal-form.pptx) | 변경 대상·사유·요약을 기입하는 실제 발표 서식의 텍스트를 확인함. 예시 문구를 사실로 제출하면 안 됨. |
| Howmet Aerospace | 공급업체 추가 승인 요청서 | PDF·영어 | [공식 게시 원본](https://www.howmet.com/wp-content/uploads/sites/3/2023/05/SupplierRequestForm.pdf) | 요청자·업체 정보·세금 식별값·승인/서명란을 포함한 한 쪽 서식을 확인함. 본문 버전은 2020-04-17이며 현재 업무 적용 여부는 담당자에게 확인해야 함. |

원본 확인 해시는 다음과 같음. 인터넷 원본이 변경되면 다시 확인해야 하며, 이 해시만으로 기입·서식 유지·법적 적합성이 검증되는 것은 아님.

| 문서 | 원본 크기(bytes) | SHA-256 |
|---|---:|---|
| Siemens 영어 DOCX | 35,754 | `fa78b5f7e900043b3c7d0ea4e63f0e4d65f56af0b9a0d2baa6b556f9d0bfe9fb` |
| Siemens 독일어 DOCX | 31,317 | `5a4675d87a170896ae1aa3810d2164a520765ccea0bdfdebd77ddc63d9d7ba84` |
| Bosch XLSX | 50,227 | `f110149b3199cbeb92b9200779a7cedcf206302d3298f40230892e4c3979056e` |
| Bosch PPTX | 1,190,308 | `8807db0414e9ea983a13353d4d6144bc9b98f22230ee32119e184f6a8b3caeb4` |
| Howmet PDF | 120,628 | `9219e7f0d83a60977567568ce23f0d71572ea2ee7f6d8085b0c004fdb3ab9bc3` |

Microsoft의 [공식 공급업체 보안 안내](https://learn.microsoft.com/en-us/compliance/assurance/assurance-supplier-security-and-privacy-assurance-program), BASF의 [공식 구매 포털](https://procurement.basf.com/irj/portal/procurement), ABB의 [공식 공급업체 등록 안내](https://global.abb/group/en/about/supplying/becoming-a-supplier)도 확인했음. 이 자료는 안내·포털·초대 기반 등록 절차이며, 자유롭게 내려받아 작성할 수 있는 직원용 내부 기안 양식으로 분류하지 않았음.

현재 공통 매핑 엔진은 원본 파일과 확인된 입력 위치를 기준으로 동작하고, 원래 영문 항목 키를 유지함. Name, Author, Prepared by, Department, Applicant, Signature, E-mail, Phone, Consent, Vote, Account, SSN, Tax ID 등은 사용자 직접 입력 대상으로 검증함. 영어·한국어 혼합 항목, 영문 키를 한국어 본문 키로 바꾸는 우회, 반복 입력칸, PPTX 도형·표 문맥을 offline pytest로 검사했음. 이 규칙이 모든 언어의 개인정보·법적 동의 문구를 자동으로 판별하는 것은 아니므로 새로운 언어·서식의 매핑은 사용자 확인이 필요함.

공개 원본의 재배포 허용은 별도로 확인하지 않았으며 저장소에 원본을 재배포하지 않음. 실사용 기입 시험, 글꼴·표·배치 유지, 실제 Word·Excel·PowerPoint·PDF 뷰어 확인, 회사 내부 승인 규정은 별도 검증 범위임.

후속 확인: Siemens 영어·독일어 Word 원본에서 양식 보호를 유지한 활성 FORMTEXT 입력칸을 각각 34개 인식함. 영어 Responsible 한 칸의 부분 기입·독립 재열기·실제 Word 16.0 PDF 렌더 3쪽을 확인했으며, 증거는 `outputs/siemens_native_qa/native_visual_qa.json`에 저장함. 모든 항목의 작성 완료나 독일어 전 항목 검증을 뜻하지 않음. 해외 원본 5개의 카탈로그 전체 기입 확인 상태는 `fill_verified=False`로 유지함.
