# RA 추가 공개 원자료·기업 양식 조사 기록

2026-10-03에 확보한 국내 제약사 공식 설명서 **2개사·2개 PDF·전체 4쪽·선택 사실 9개**와 기업 공개 의뢰 양식 **3개사·3개 PDF·전체 7쪽·등록 선택칸 13개**를 기록함. 설명서의 사실 정답과 빈 양식의 작성 위치를 별도로 관리함. 비공개 사내 RA 양식, 국내 현재 허가, 전체 제출 완료를 확인한 자료로 표시하지 않음.

이 문서는 수집·주석·선택칸 QA의 근거 목록임. 최종 교차 평가와 전체 pytest 결과는 별도 검증 보고서에서 다룸.

## 국내 공식 제품 설명서

고정 manifest: [ra_additional_sources.json](../evals/ra_additional_sources.json). 수집 확인 시각은 `2026-10-03T01:51:24.017443+00:00`임. 아래 직접 다운로드 URL과 최종 응답 URL은 같았음. 공식 페이지의 링크·버튼 값과 공식 JavaScript 경로를 관찰하여 내려받았고, 페이지·핸들러 스냅샷도 manifest에 연결함.

| 원자료 | 공식 제품 페이지 / 관찰한 다운로드 | 회사 역할과 원문 근거 | 선택 제품·범위 | 인쇄 버전 | 전체 쪽 / 선택 사실 |
|---|---|---|---|---|---:|
| 액티민주사 설명서 | [동국제약 제품 페이지](https://www.dkpharm.co.kr/product/view.php?idx=82) / [설명서 PDF](https://www.dkpharm.co.kr/library/download_prod.php?idx=82) | 동국제약(주), `manufacturer`. 2쪽의 `제 조 원`과 동국제약(주)가 같은 원문에 결합됨 | 액티민주사, 앰플주사제. 1mL 기준 단일 조성. 괄호 성분명은 별도 표제 문구이며 법정 허가 제품명 확인은 수행하지 않음 | 설명서 최종 개정 2021-04-20, 2쪽 | 2 / 5 |
| 로수로드정 공통 설명서 | [종근당 제품 페이지](https://ckdpharm.com/product/productView.do?prodCode=CKD0000300) / [설명서 PDF](https://ckdpharm.com/downloadCkd.do?attFileNo=39741&saveFileName=3ebfa274a6afd054b98634ddd7bc3bc37a63f46600f02bce6c1ecbdbe74ed93b) | 종근당, `document_publisher`. 2쪽의 첨부문서 작성일·종근당 인터넷 홈페이지 안내와 공식 다운로드 도메인으로 발행 주체를 확인함. 제조원·국내 신청인으로 추정하지 않음 | 본문 함량 라벨 전체인 `로수로드정 5mg`을 선택함. 공통 PDF의 10mg·20mg 조성·성상을 합치지 않음 | 첨부문서 작성일 2025-08-14, 2쪽 | 2 / 4 |

동국제약 역할 근거는 `▪제 조 원: 동국제약(주) …`임. 종근당 역할 근거는 첨부문서 작성일 이후 변경 내용을 “종근당 인터넷 홈페이지(www.ckdpharm.com)”에서 확인하도록 한 안내임. 정확한 전체 인용·탭·줄바꿈·페이지는 manifest의 `company.role_exact_quote`에 보존함. 종근당의 발행 역할을 제조·수입·판매허가 역할로 바꾸지 않음.

| 로컬 원본 | 크기(bytes) | SHA256 |
|---|---:|---|
| `data/ra_public_validation/additional/dongkook_actimin.pdf` | 92,095 | `69fb4da106fc8a4854c8e0488c3e9616406360efa4e39f4f9a0992478079f72b` |
| `data/ra_public_validation/additional/ckd_rosulod.pdf` | 531,735 | `db440db7a72d82f10202b1d99e35fe98037dfe2955842b9794bd805d77b66506` |

### 선택 사실의 정답 범위

| 선택 항목 | 액티민주사 | 로수로드정 5mg |
|---|---|---|
| 제품명 | 전면 브랜드 제품명 전체, 1쪽 | 본문 5mg 함량 라벨 전체, 1쪽 |
| 원료약품 및 분량 | 1mL 중 유효성분·기타첨가제 전체 블록, 1쪽. 염산염 5.46mg과 푸르설티아민 5mg을 구분함 | 1정 중 5mg 선택 조성·동물유래성분·기타첨가제 전체 블록, 1쪽. 칼슘염 5.20mg과 로수바스타틴 5mg을 구분함 |
| 성상 | 갈색 앰플주사제의 전체 성상 문장, 1쪽 | 5mg의 노란색 원형 필름코팅정제 문장, 1쪽 |
| 용법 용량 | 성인 1일 5~100mg, 피하·근육내·정맥내 투여 및 증상에 따른 증감 조건을 포함한 전체 선택 문단, 1쪽 | 이번 선택 사실에 포함하지 않음 |
| 저장방법 및 유효기간 | 차광밀봉용기·실온 1~30℃ 보관, 2쪽 | 기밀용기·실온 1~30℃ 보관, 2쪽 |

두 설명서의 선택 저장 문구에는 사용기간 숫자가 없으므로 개월·년을 생성하지 않음. 액티민의 1mL 조성을 10mL 앰플 총질량으로 환산하지 않음. 임상시험 표·다른 함량·다른 제형을 정답에 섞지 않음. `value`와 `exact_quote`는 선택 문단 전체를 보존하고, 인용 원문을 출력에 맞추어 자르거나 기호를 대체하지 않음.

수집 시 M1 확인은 **2/2 자료·4/4쪽·9/9 선택 사실**, 추출 표는 **3개**였음. 원본 모든 4쪽의 선택 문구·수치·회사 역할을 PDFium PNG로 대조한 수집 기록이 있음. 이 9개는 고유 선택 사실 수이며 같은 사실을 여러 양식에 넣은 실행 건수와 다름. [수집 감사 JSON](../outputs/ra-cross-company/sources/audit.json)에 원본 전후 SHA, 파서 결과와 최초 실행을 보존함.

### 수집에서 제외·실패한 후보

설명서 후보는 총 **8개 PDF를 실제 내려받았고**, 그중 선택 2개와 제외 6개를 분리함. 다운로드 실패 2건은 PDF 8개에 포함하지 않음.

| 제외 후보 | 전체 쪽 | 제외 사유 |
|---|---:|---|
| 보령 듀리세프 | 2 | 이미지 전용, 텍스트 0자. OCR 없는 자동 사실 평가에 사용하지 않음 |
| JW 산테쿨 | 1 | 이미지 전용, 텍스트 0자 |
| JW 산테쿨하이 | 1 | 이미지 전용, 텍스트 0자 |
| 동국 센스온 | 3 | 수치·날짜 문자가 U+FFFD로 손상되어 수치 대조 불가 |
| 종근당 제스판 | 1 | 제품명 브랜드 표제가 그래픽이므로 전체 제품명 텍스트 대조 불가 |
| 종근당 오엠피에스 | 2 | 같은 회사의 추가 탐색 후보이며 선택 사실 미주석 |

유한 디오살탄 공식 PDF 링크는 HTML 오류 페이지로 리디렉션되어 PDF로 집계하지 않음. 대원 펠루비서방정 링크는 HTTP 404로 원본 미확보임. 후보별 관찰 URL·최종 URL·SHA·실패 응답 정보는 manifest의 `excluded_sources`와 `download_failures`에 있음. 신규 선택 2개는 비암호화·일반 텍스트 추출 허용을 확인했으며, 보호를 해제하지 않음. 기존 별도 한국어 manifest의 에리우스 추출 권한 보류는 이 신규 2자료의 분모에 포함하지 않음.

## 기업 공식 외부 의뢰용 빈 양식

고정 manifest: [ra_additional_templates.json](../evals/ra_additional_templates.json). 공개 원본을 확인한 날짜는 **2026-10-03**, 선택칸 QA 기록 시각은 `2026-10-03T02:12:56.510605+00:00`임. 세 회사의 역할은 **`public_form_issuer`**, 즉 공개 양식 발행 주체임. 제품 제조원·판매허가권자·시험 신청인으로 옮겨 적지 않음. 공개 직접 PDF 주소가 source page이며, 관찰한 최종 응답 주소도 같았음.

| 발행 주체·공식 원본 | 업무·인쇄 버전 범위 | 전체 쪽 | 원본 canonical 필드 / 등록 선택칸 |
|---|---|---:|---:|
| [Eurofins BioPharma Product Testing Sweden AB — Sample Submission Form](https://cdnmedia.eurofins.com/european-east/media/2854407/ebpt-sweden-sample-submission-form_ifyllbar.pdf) | 의약품 시험 외부 시료 의뢰. 이번 수집 기록에서 문서 개정일·번호를 확정하지 못함 | 3 | 66 / 5 |
| [SGS Health Science — Sample Submission Form](https://www.sgs.com/en/-/media/sgscorp/Documents/Corporate/Brochures/SGS-HN-US-Health-Science-Sample-Submission-Form-EN.cdn.en.pdf) | 의약품 시험 외부 시료 의뢰. 1쪽 인쇄 `FRM-001582, Rev. 3.0`; 개정 날짜·현행성 미확인 | 1 | 100 / 4 |
| [Thermo Fisher Scientific / Gibco — Custom Serum Request Form](https://documents.thermofisher.com/TFS-Assets/LSG/brochures/gibco-custom-serum-request.pdf) | 바이오생산·규제지원 인접 업무의 맞춤 혈청 요청. 2쪽 ©2016은 저작권 연도이며 개정일로 사용하지 않음 | 3 | 66 / 4 |

| 로컬 원본 | 크기(bytes) | SHA256 |
|---|---:|---|
| `data/public_templates/ra_cross_company/eurofins_public_form.pdf` | 508,455 | `91cec1cc881ca4466c969a4e707ce7589bebd61a1723a9b4f54742f6e3676905` |
| `data/public_templates/ra_cross_company/sgs_public_form.pdf` | 350,763 | `7fa3a5ff25941a789317618a25591985372c9d49a409ecfdc4c2f028ff8f8c1e` |
| `data/public_templates/ra_cross_company/thermofisher_public_form.pdf` | 107,975 | `c7e2e2eee4343f60e847b72f42a69e2d579d6a8de3e047989d506ee5b307ae1d` |

**232개 canonical 필드를 관찰했으나 등록·선택 작성 검증은 13칸만 수행함.** 모든 필드를 작성한 것으로 표시하지 않음. 등록 항목은 다음과 같음.

| 양식 | 등록한 선택 항목 | 직접 확인할 항목 |
|---|---|---|
| Eurofins | 제출 회사, 시료 제출자, 연락 이메일, 제품명, 시료 관련 메모 | 제출 회사·제출자·이메일 |
| SGS | 시험 의뢰 연락 담당자, 시험 의뢰 회사, 제품명, 시료 로트 번호 | 담당자·의뢰 회사·실제 로트 |
| Thermo Fisher/Gibco | 의뢰 회사, 의뢰 연락 담당자, 의뢰 날짜, 맞춤 혈청 요청 설명 | 회사·담당자·날짜 |

선택·서명·계정·견적·의뢰 승인·실제 시료 정보는 실제 사용자 확인이 필요한 업무 값임. 원본의 필수 플래그를 완화하지 않고 미등록 칸과 안내문을 유지함. 출처 ID는 짧은 신원칸에 억지로 인쇄하지 않고 sidecar로 보존함.

### 합성 예시와 인터넷 사실 검증의 구분

1. **합성 부분 기입 QA:** 3개 양식 각각에 테스트용 값을 넣고 선택 위치·원본 안내·페이지·위젯 상태를 독립 검수함. 합성 출력 7쪽은 인터넷에서 검증된 실제 신청 정보가 아님.
2. **기존 공개 제품명 교차 QA:** 기존 EMA 3개사 제품명 원문을 Eurofins·SGS 2양식의 제품명 칸에 넣은 **6개 부분 기입**, 출력 **12쪽**을 별도로 보존함. 제품명 원문과 작성 위치의 대조이며 실제 시료·판매·거래 관계나 시험 의뢰를 증명하지 않음. 제출자 등의 테스트 값과 제품명 원자료를 구분함.
3. **Thermo Fisher/Gibco는 의약품 사실 교차 대상에서 제외함.** 맞춤 혈청 요청 설명을 의약품 제품명·용량 칸으로 해석하지 않으며, 약품 사실을 이 양식에 넣은 교차 QA는 0건임.

위 선택칸 QA의 PDFium PNG 시각 확인 분모는 원본 **7쪽** + 합성 출력 **7쪽** + 기존 제품명 교차 출력 **12쪽** = **26쪽**임. 실제 Acrobat·Word·Excel 프로그램으로 확인한 양식은 이 기록에서 **0건**임. PDFium의 form appearance 렌더를 실제 업무 프로그램 검증으로 확대하지 않음. 개별 [Eurofins QA](../outputs/ra-cross-company/templates/eurofins_qa.json), [SGS QA](../outputs/ra-cross-company/templates/sgs_qa.json), [Thermo Fisher QA](../outputs/ra-cross-company/templates/thermofisher_qa.json)와 [공개 제품명 교차 QA](../outputs/ra-cross-company/templates/real_public_identity_cross_fill.json)에 원본·프로파일·출력 SHA와 확인 범위를 기록함.

## 원문 기호를 보존하는 출력 글꼴

최초 수집 시 실행에서는 액티민 원문의 `▪` 글리프가 양식 출력 글꼴에 없어 두 출력이 차단되었음. [최초 수집 감사](../outputs/ra-cross-company/sources/audit.json)의 실패를 유지하며 원문 기호를 다른 문자로 바꾸지 않음. 이후 동봉한 글꼴의 지원 범위와 출력별 검수를 구분함.

[공식 Google Fonts Noto Sans KR 원본](https://github.com/google/fonts/blob/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf)을 보존하고 wght=400 정적 Regular를 파생함. [SIL OFL 1.1 원문](https://github.com/google/fonts/blob/main/ofl/notosanskr/OFL.txt), 정확한 다운로드 출처와 파생 방법은 [글꼴 README](../templates/fonts/README.md)에 있음. 확인 시각은 `2026-10-03T02:02:34.374797+00:00`임.

| 파일 | SHA256 | 역할 |
|---|---|---|
| `NotoSansKR[wght].ttf` | `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252` | 수정하지 않은 공식 variable 원본 |
| `NotoSansKR-Regular.ttf` | `338025c2a71401a91e7bd78b13369d6678c984b87b0ac08e094c6ec9f26e53d9` | 정적 wght=400 PDF fallback |

ReportLab 등록과 cmap 검사에서 신규 manifest의 선택 원문 **149종 문자**, 기존 EMA **51종**, 기존 한국어 **164종**에 누락이 없었음. `▪/℃/≥/µ`도 지원함. manifest별 문자 수는 서로 중복될 수 있으므로 합산하여 고유 문자 수로 표시하지 않음. NanumGothic은 `▪/℃` 미지원으로 제외함. 시스템 글꼴 설치나 원문 기호 치환은 하지 않음. 글리프 지원만으로 개별 출력의 배치·줄바꿈·AcroForm 표시 보존을 통과한 것으로 표시하지 않음.

## 최신성·권한·검증 한계

- 공식 URL에서 받은 스냅샷의 SHA·크기·전체 페이지와 선택 원문을 고정함. 현재 국내 허가 상태, 신청인, 최신 설명서·양식 버전은 미검증임. 설명서 개정일·양식 개정번호·저작권 연도를 혼합하지 않음.
- 신규 선택 설명서 2개와 기업 양식 3개는 공개로 읽을 수 있는 비암호화 원본임. 이미지 전용·손상 문자·권한 제한을 우회하지 않고 제외 또는 보류로 남김.
- 제품 설명서와 기업 공개 외부 의뢰 양식의 원본 재배포 허락은 확인하지 않았음. 로컬 검증용 원본을 Git에 무단 재배포하지 않음. 동봉 글꼴의 OFL 조건과 제품·양식 저작물의 권리를 구분함.
- 일부 선택칸의 부분 기입·독립 구조 검수·PDFium PNG 확인이며 전체 기입, 서명·발송·접수·계약, 법정 제출 적합성은 미평가임. 실제 제출 사례와 비공개 내부 양식 검증 사례는 0건임.
- 이 수집·주석·선택칸 QA에는 외부 LLM 호출·모델 학습·실사용 사용자 수정 KPI 관측이 없음. 원문 복사 규칙 검사, 합성 예시, 실제 모델 생성 평가와 사람의 실사용 품질을 구분함.

## 고정 자료 연결

| 자료 | 고정 SHA256 |
|---|---|
| `evals/ra_additional_sources.json` | `e0a3644133b65509f5f4f052fd3c724361240e7071b4cfe725ed9fc653986368` |
| `evals/ra_additional_templates.json` | `b25bf46744fe53606dfc94a84ceaeef2c63389e334d60f819ecd107ed5cc9bf1` |

다운로드 URL·최종 URL·회사 역할·선택 제형·원문 전체 인용·페이지·원본 SHA는 위 manifest가 기준임. 과거 실행 기록은 당시 코드의 결과로 보존하며, 이후 최종 교차 평가·전체 테스트의 수치와 동일한 것으로 취급하지 않음.
