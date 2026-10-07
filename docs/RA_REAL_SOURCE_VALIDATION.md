# RA 실제 공개 원자료 검증 기록

최신 단계는 [RA 공개 원자료·회사별 양식 교차 검증](RA_CROSS_COMPANY_VALIDATION.md)임. 허용 6회사·378쪽·28고유 사실을 5양식에 선택 기입하여 30건 통과·보호 5건 보류, 원문/출처 74/74·수치 포함 필드 61/61을 확인함. 아래 11건/41/35는 이전 단계의 보존 이력이며 최신 결과와 합산하지 않음.

## 이전 재검증 — 실제 출력 확인 통합

2026-10-03 KST 최종 검수 코드로 공식 공개 snapshot의 RA 선택 사례를 기본 출력·추가 출력 확인 두 모드에서 각각 13사례 실행함. **기입 11사례 통과·추출 권한 제한 2사례 보류, 전체 원문/출처 41/41·수치 포함 필드 35/35 일치**임. 출력 11개·42쪽을 독립 재열기했으며 두 모드 모두 기존 출력과 byte 동일함. 서로 다른 모드의 실행 수를 새로운 양식 수나 회사 수로 합산하지 않음.

최신 결과는 [EMA 9/9](../outputs/native-product/ra-regression-final/ema.json), [한국어 2/4](../outputs/native-product/ra-regression-final/korean.json), [추가 확인 EMA](../outputs/native-product/ra-native-final/ema.json), [추가 확인 한국어](../outputs/native-product/ra-native-final/korean.json), [원본·코드·출력 독립 감사](../outputs/native-product/ra-regression-final/audit.json)에 기록함. 추가 검수는 안전한 원본 PDF 사본을 판독하는 `pdf_copy` 방식임. Roche 기입 3사례는 통과하고, 법정 양식 기입 8사례는 별첨 전체 범위를 별도 검수로 확인해야 한다는 `native_annex_scope` 경고를 유지함. 기입 통과 11건을 추가 검수 11건 통과로 표시하지 않음.

각 성공 실행의 출력·출처 sidecar·미리보기는 같은 UUID 폴더에 연결함. 기본/추가 모드의 성공 실행 UUID 22개가 고유하며, 추가 미리보기 11개·42쪽의 실제 SHA·쪽수·선택 필드 분모가 검수 기록과 일치함. Windows의 긴 회사/제품 경로에서 실패한 6개 시도는 [수정 전 증거](../outputs/native-product/ra-regression-final/long-path-failed-attempt/failure.json)에 보존하고 짧은 UUID 폴더·고정 파일명으로 같은 긴 경로를 재실행함.

M1 전체 파싱 4종·374쪽·124표·19고유 사실, 정상17/17·선정 오류92/92, mock15/15·업무 규칙24/24, 이전 PNG42쪽의 증거는 관련 파서·검수 코드·입력·결과 SHA 불변을 대조하여 재사용함. 이번 최종 감사에서 다시 전체 파싱·오류셋·PNG42쪽 시각 검토를 수행했다고 세지 않음. 새 Office/고의 누락 검증의 104쪽·몽타주12개·선택18쪽 시각 QA는 [별도 출력 검수](NATIVE_OUTPUT_REVIEW.md) 범위임.

실제 모델 응답과 사람 수정 KPI 관측은 계속 0건임. 이 결과는 원문 복사 규칙과 선택 기입의 정확도를 시험한 것이며, 여러 회사의 비공개 내부 RA 양식 전수 확보·신약 허가 적합성·최신 법적 제출 완료·사람 수정 0%를 인증하지 않음. 전체 pytest와 최종 코드 SHA는 [검증 기록](VALIDATION.md)과 [종합 감사](../outputs/native-product/verification.json)에 연결함.

## 이전 XLSX 확장 검증 이력

2026-10-03 KST XLSX 확장 후의 최신 규칙·기입 검증임. 인터넷에서 확보한 공식 원본 snapshot으로 RA 우선 교차 기입·독립 출력·오류 주입을 재실행함. **선택 출력 11건 통과·보호 PDF 2건 보류, 원문/출처41/41·수치 필드35/35, 정상17/17·선정 변조92/92 검출**임. 전체 pytest는 **2,272 passed·1 skipped·2 warnings·363.97초**임. 실제 LLM 작성 품질과 사람 수정 KPI는 미평가이며 국내 허가·최신 법적 제출 적합성·비공개 내부 양식 인증을 뜻하지 않음.

최신 실행은 [EMA9/9](../outputs/repeat-xlsx/ra/ema.json), [한국어2/4](../outputs/repeat-xlsx/ra/korean.json), [오류 주입](../outputs/repeat-xlsx/ra/adversarial.json), [원본·출력·코드 감사](../outputs/repeat-xlsx/ra/audit.json)에 기록함. 허용 원자료4종을 M1으로374쪽·124표 전체 파싱하고19고유 사실을 대조함. 41필드는 같은 사실을 여러 양식에 옮긴 횟수이며 보호 제한2사례를 성공으로 세지 않음.

새 출력PDF11개/42쪽은 이전repeat-row-ra 출력과 모두 byte동일함. 이전PNG42쪽과 직접 검토 reference의 현재 SHA를 재대조하여 이전22개 고유 PNG 직접 확인·20쪽 동일pixel 근거를 재사용함. 이번에 다시42쪽을 렌더하거나 직접 확인한 것으로 세지 않음. [재사용 감사](../outputs/repeat-xlsx/ra/audit.json)의visual_qa_reuse에 출력/PNG/reference SHA를 연결함. 미작성 칸·서명·승인은 유지하며 전항목 완료율·AI 문장 품질을 이 결과에 포함하지 않음.

RA 관련78파일은 실행 중 불변이며 이후 XLSX 예시 행/머리글 매핑2파일만 변경됨. RA 직접 검사4파일 SHA는 현재도 같고 마지막 전체pytest는 최종172코드/프롬프트 SHA 불변을 확인함. [최신 종합 근거](../outputs/repeat-xlsx/verification.json)에 각 검증 시점과 변경 범위를 구분함. 직전2,046개/319.29초의 근거는 [이전 반복 행 검증](../outputs/repeat-rows/verification.json)에 보존함.

## 원자료와 양식의 분모

| 구분 | 확인한 표본 | 검증 범위 |
| --- | --- | --- |
| 법정 RA 양식 | 등록 PDF 17종, 입력 위치 399칸 | 공식 법정 양식 원본에 연결한 입력 프로파일임. 별도 GMP HWP를 포함한 18종은 전체 파싱, PDF 17종은 시험값 기입을 통과함. GMP HWP 기입 1종은 대기임. 모든 자료를 모든 칸에 작성하거나 제출 필수 항목을 충족한 결과가 아님. |
| 기업 공개 RA·품질 양식 | 8개 기업 조사, 공식 빈 PDF 3종·21쪽 | 전체 페이지 파싱을 확인함. Roche 1종의 공급자 텍스트 33칸만 부분 기입 검증함. 법정 17종/399칸과 별도 표본임. |
| EMA 제품정보 | EU 판매허가권자 3개사, PDF 3종·372쪽, 선택 사실 14개 | 출처 페이지·원본 SHA·실제 인용 쪽·전체 원문·제형과 용법 역할을 보존함. 완성된 제품정보이며 빈 기업 양식이 아님. |
| 한국어 제품 설명서 | 원본 PDF 7종 확보, 평가 선택 2종·5쪽·11개 사실, 제외 5종 | 한미 5개 사실의 운영 파서 대조를 통과함. 에리우스 6개 사실은 추출 권한 제한으로 운영 평가 보류임. 미확보 HTTP 403 후보는 다운로드 건수에 포함하지 않음. |

법정 양식의 원본·버전은 [RA 카탈로그](../templates/ra_catalog.json), 등록 입력 위치는 `templates/profiles/ra_*.json`에 보관함. 기업 공개 양식의 조사·원본·필드 기록은 [기업 manifest](../evals/ra_corporate_sources.json)에 보관함. 회사별 제품정보와 공통 법정 양식을 결합한 시험을 회사 고유 내부 RA 양식 시험으로 세지 않음.

전문 입력 프로파일의 실제 등록 합계는 **24종·496칸**임. RA는 법정 17종·399칸과 Roche 1종·33칸을 합친 18종·432칸이며, Business 3종·42칸과 Office 3종·22칸은 별도 도메인임. 이 등록 수는 양식의 모든 항목을 실제 원자료로 작성한 사례 수나 회사별 내부 양식 수가 아님. 공식 원본의 인쇄 버전·확인 날짜를 보존했으며 현재 국내 허가·최신 법령/서식·회사 내부 최신 버전은 별도로 확인해야 함.

## 선택한 제품정보와 회사 역할

| 제품정보 | 원문으로 확인한 회사 역할 | 선택 범위와 제한 |
| --- | --- | --- |
| [Herzuma 공식 EMA 자료](https://www.ema.europa.eu/en/medicines/human/EPAR/herzuma) | Celltrion Healthcare Hungary Kft., EU 판매허가권자 | 150mg 주입용 바이알을 선택함. 성상·미개봉 사용기간과 성인 전이성 유방암 3주 간격 초기/유지 용량을 구분함. 420mg·주간 요법·조제 후 안정성을 섞지 않음. |
| [Benepali 공식 EMA 자료](https://www.ema.europa.eu/en/medicines/human/EPAR/benepali) | Samsung Bioepis NL B.V., EU 판매허가권자 | 25mg 사전충전 시린지를 선택함. 성인 류마티스 관절염의 병용 조건과 주 2회 용법/주 1회 대안을 보존함. 50mg 펜·시린지로 바꾸지 않음. |
| [Keppra 공식 EMA 자료](https://www.ema.europa.eu/en/medicines/human/EPAR/keppra) | UCB Pharma SA, EU 판매허가권자 | 250mg 필름코팅정을 선택함. 정당 성분·연령·단독요법·체중 조건·하루 2회 용법의 역할을 보존함. 다른 함량·액제·주사제를 섞지 않음. |
| [한미플루 공식 제품 페이지](https://www.hanmi.co.kr/business/product/finder/detail-1245.hm?prodSeq=1245) | 한미약품(주), 문서 발행사/제품문의처 | 30·45·75mg 공통 설명서에서 75mg 캡슐 본문을 선택함. 성인/13세 이상 감염 치료 용법이며 예방·소아 체중표·신장애 조절 용량은 제외함. 설명서 개정일은 2024-09-03임. |
| [에리우스 공식 게시사](https://www.organon.com/korea/home/) | PDF에 인쇄된 수입자 한국엠에스디(주); 웹 게시사 한국오가논 | 5mg 정제와 정제 전체 질량 약 106mg을 구분함. 성인/12세 이상 용법을 선택함. 2021-01-26 작성 자료의 수입자 역할이 현재도 같다고 확정하지 않음. 자동 추출은 권한 제한으로 보류함. |

전체 제품명·선택 문단·실제 페이지·SHA와 확인한 날짜는 [EMA manifest](../evals/ra_public_sources.json), [한국어 manifest](../evals/ra_korean_sources.json), [한국어 조사 기록](RA_KOREAN_PUBLIC_SOURCES.md)에 보관함. EU 판매허가권자, 문서 발행사, 과거 인쇄 수입자를 한국 신청인·현재 제조원·판매원으로 대입하지 않음. 신청인·회사·제조소·허가번호·서명·계좌·승인 판단은 직접 입력과 별도 근거 확인 대상임.

원문에 숫자 사용기간이 없는 한미·에리우스에는 임의 기간을 추가하지 않음. 해외 승인 자료는 국내 허가 사실을 증명하지 않음. 수집일, EMA 게시 갱신일, 원문 개정일, 법령 시행일은 각각 기록하며 서로 치환하지 않음. 원본 스냅샷의 진위·인용 위치 확인과 현재 국내 허가/최신 설명서 확인은 별개임.

## 기업 공개 양식 검증

- **Roche:** [공식 공급자 지침](https://suppliers.roche.com/suppliers/site-specific-instructions)에 연결된 변경관리 PDF 6쪽임. 공급자 A~F의 텍스트 33칸에 시험값을 입력하고 G의 Roche 결정·서명 및 선택칸은 유지함. 원본 전체 6쪽과 시험 결과 6쪽, 입력 영역 밖 픽셀·기존 안내문·전체 입력 문구·SHA 불변을 확인함. 진단·품질 공급자 문맥이며 의약품 판매허가 신청서 또는 회사 비공개 내부 RA 문서와의 동일성을 인증하지 않음.
- Roche의 재료 설명 1~3행에는 EMA 세 제품의 전체 제품명을 교차 입력함. [별도 sidecar](../outputs/ra_corporate_form_qa/roche_ema_cross_fill_sidecar.json)에 실제 원문과 인용을 보존함. 해당 약품의 Roche 공급 관계·실제 변경 발생·변경 영향은 주장하지 않으며 공급자 식별자·계약·변경 내용·승인란은 비워 둠.
- **Pfizer Canada:** [공식 공급자 설문 PDF](https://channels.pfizer.ca/sites/default/files/Pfizer_Canada_Supplier_Survey_distributed.pdf) 12쪽을 읽고 95개 canonical 필드를 관찰함. `/Perms/UR3` 사용권 인증 서명이 있어 현재 엔진은 기입을 차단함. 서명을 제거하거나 보호를 우회하지 않음. 서명 유효성·작성 권한·최신 버전은 미평가임.
- **Terumo Medical:** [공식 변경통지 PDF](https://www.terumomedical.com/en-us/support/08-1TFORM-04-Supplier-Change-Notification-Form.pdf) 3쪽을 읽고 전체 원본을 시각 확인함. 원문이 Word 제출을 요구하고 정적 입력 안내가 남아 있어 기입 평가는 보류함.

공개 빈 양식 3종 중 기입 검증 1종, 보호 차단 1종, Word 원본/수동 매핑 대기 1종임. 인쇄 버전은 Pfizer 2017-01-20, Roche 2023-06-30/04, Terumo Rev 12이며 현재 회사 내부 최신성은 확인하지 않음. 상세 검색 결과·시각 확인 쪽수·미확보 이유는 [기업 조사 기록](RA_CORPORATE_FORM_RESEARCH.md)에 기록함. 체크박스·서명·회사 내부 승인·추가 첨부를 자동으로 완료한 것으로 표시하지 않음.

## 이전 RA 조건 보강의 검증 이력과 미평가 항목

| 검사 | 실행 범위 | 확인 상태 |
| --- | --- | --- |
| 직전 전체 원자료 파싱 | EMA 3종·372쪽·112표·14개 사실, 한미 1종·2쪽·12표·5개 사실을 대조함. 합계 허용 원자료 4종·374쪽·124표·19개 사실과 원본 불변을 확인함. | 에리우스 3쪽·6개 사실은 EXTRACT 권한 제한으로 차단/보류함. 수집 manifest의 2종/11개 사실과 운영 허용 1종/5개 사실을 구분함. |
| 한국어 교차 기입 재실행 | 입력 항목 유형 반영 재실행에서 한미 5개 사실 × 법정 양식 2종 = 2/2개 통과함. 에리우스 포함 선택 사례는 4개임. | [typed-korean 결과](../outputs/ra-real-source-typed-korean.json)는 2/4개 통과이며 에리우스 2개 사례는 보호 제한 보류임. 이번에 시각 확인 범위를 늘리지 않음. |
| EMA 법정/기업 양식 교차 기입 재실행 | 입력 항목 유형을 반영한 3개 EMA 자료 × 법정 양식 4·20 및 Roche = 9개 사례임. | [typed-ema 결과](../outputs/ra-real-source-typed-ema.json)는 9/9개 선택 기입 출력 검사를 통과함. 법정 양식과 Roche 중립적 재료명 입력을 구분하며 재실행 출력의 새 시각 확인은 미수행임. |
| 직전 선택 입력·인용·수치 | 직전 한미 2개와 EMA 9개의 통과 출력 11개에서 선택 원문 전체 41/41개 및 인용 연결 41/41개, 수치 포함 선택 필드 35/35개를 확인함. | 직전 선택 필드의 문자·출처·수치 보존 검사임. 이번 조건 검수의 추가 출력 수나 양식 전항목 작성률·LLM 사실 정확도로 해석하지 않음. |
| 직전 출력 시각 QA | 직전 통과 출력 11개 PDF, 합계 42쪽을 렌더링함. 입력·별첨 19쪽을 직접 시각 확인했고 나머지 23쪽은 렌더 및 독립 원본 보존 검사를 수행함. | 그 실행에서 직접 확인한 19쪽에 겹침·잘림을 발견하지 않았으며 메타데이터 갱신 PNG 42쪽의 동일성도 확인함. 이후 typed 재실행 출력과 이번 조건 검수로 시각 확인 숫자를 확대하지 않음. |
| 법정 RA 코퍼스 | 고유 SHA 18개 원본을 18/18개 전체 파싱함. PDF 17종·등록 399칸 시험값 기입과 GMP HWP 원문 읽기를 구분함. | 기입 통과 17개, GMP HWP 기입 대기 1개, 실패 0개임. 최신성·제출 적합성 검증이 아님. |
| 이전 전체 pytest | 1,258 passed, 1 skipped, 1 warning, 172.62초임. | skip 1개는 에리우스 EXTRACT 권한 제한에 따른 자동 원문 대조 보류임. 이 실행과 조건 집중 회귀 185개는 상단 최신 전체 2,046개 결과의 이전 기록임. |
| 이번 집중 pytest | 새 조건 회귀 34개를 포함한 RA 관련 6파일, 185 passed, 42.47초임. | 실제 M1 파서→검색 조각 회귀를 포함함. 프로젝트 전체 테스트 건수가 아님. |
| 이번 오류 주입 평가 | 정상 17/17개, 선정 변형 92개에서 generic 65개·RA 92개·정답 원문 비교 92개를 검출함. 검사 예외 0개, PDF 원본 3개의 SHA 불변임. | [운영 조건 검수 결과](../outputs/ra-public-adversarial-operating.json)임. RA 단독 92/92는 이 선정 변형셋의 결과이며 모든 새 임상 오류의 검출 보증이 아님. |
| 직전 오류 주입 평가 | 정상 17/17개, generic 65개·RA 88개·정답 원문 비교 92개, 예외 0개였음. | 직전 generic+RA 합집합 89/92개·정답 비교만 검출한 3개라는 결과는 [이전 실행](../outputs/ra-public-adversarial-final.json)에 보존함. 이번 수정 후 남은 사례 수가 아님. |
| 실제 LLM(직전 실행 시도) | EMA 9개·한국어 4개 사례 실행 시도, 실제 모델 응답 0개, 모델 평가 사례 0개임 | API 키 미설정 실패임. 시도 13개를 평가 합격 또는 mock 성공으로 바꾸지 않으며 이번 조건 검수도 모델 호출을 수행하지 않음. |
| 사람 업무 KPI | 실제 사용자 관측 0건임 | 작성 소요시간·수정률·반려율·업무 성공률은 미측정임. 0% 수정률 또는 100% 업무 성공률을 주장하지 않음. |
| 전문 RA/법적 검토 | 허가 상태·추가 첨부·최신 개정·정식 제출 적합성 미평가임 | 전항목 작성 완료·국내 허가 적합성·사내 승인 완료 인증을 제공하지 않음. |

`rules` 평가는 사람이 대조한 실제 원문을 복사하여 출처·단위/역할·양식 기입·넘침 처리를 시험하는 방식임. 모델이 자료를 찾아 이해하고 정확한 초안을 생성하는 능력의 점수가 아님. 긴 원문은 기본 넘침 차단을 유지하며, 명시적으로 허용한 PDF 별첨은 원본 모든 페이지 뒤에 전체 원문·필드명·출처를 보존해야 함. 직접 입력·선택·서명 항목은 별첨으로 우회하지 않음. 검수 이후 원문·프로파일·매핑을 바꾸면 다시 검수해야 함.

자동 독립 출력 검사는 원본 SHA, 전체 입력 문구, 원래 페이지·표·안내문과 입력 영역 밖 픽셀 등을 대조함. PDFium으로 직접 확인한 범위는 각 QA 기록에 따로 남기며 자동 검사 통과를 모든 문서 프로그램의 네이티브 표시·법적 판단 통과로 확대하지 않음. 공개 접근 가능 여부는 추출·수정·재배포 권한의 확인을 대신하지 않음.

## 이번 운영 RA 조건 검수 변경

직전 실행에서 정답 비교만 검출한 3개는 Herzuma 성상 문장에 제품명 출처 ID를 붙인 사례, Benepali의 기존 치료 반응 불충분·메토트렉세이트 예외 조건 누락, Keppra의 의사 판단에 따른 낮은 초기용량 대안·2주 후 증량 조건 누락임. 이번 운영 검사에서는 모두 차단하며 성상에 출처가 없는 사례도 RA 자체 검사로 차단함. 약품명이나 evaluator의 정답 역할·완결성 메타데이터를 운영 규칙에 하드코딩하지 않음.

M1이 읽은 같은 블록의 전체 원문을 검색 결과 `context_text`에 보존하고 `context_start/context_end`로 인용 조각의 정확한 위치를 연결함. `context_text[start:end]`가 근거 `text`와 일치하지 않으면 `ra_context_invalid` 오류로 차단함. 검증된 주변 원문은 연결 조건의 누락 검사에만 사용하며 수치·성상 근거를 인용 조각 밖으로 넓히지 않음. 기존 명시 문단에 context가 없으면 해당 `text` 자체를 검사함. 실제 Benepali 136쪽·Keppra 170쪽을 각각 한 번 전체 파싱한 후 첫 임상 문장이 잘린 운영 검색 조각에서도 누락을 차단하는 회귀를 통과함.

성상은 인용 문구와 선택 제품 범위를 확인하며 임상 문장은 같은 문장의 `when/unless` 제한 조건과 명시 연결된 의사 판단 대안·증량 조건을 검사함. 본문 및 임의 JSON 항목의 임상 문장, 사용자 확인 프로파일의 `label`이 성상·용법용량인 입력칸에도 적용함. 누락은 `ra_condition_omitted` 등 error로 남겨 출력 차단 계약을 유지하며 수치·단위·원문을 자동 교정하지 않음. 명확한 초기용량 scalar 입력칸과 근거를 유지한 간결한 요약은 허용하고 다른 제품의 같은 조건이나 무관한 다른 섹션을 근거로 쓰지 않음.

검사 범위는 동일 원문 문장과 최대 3개의 명시 후속 문장임. 다른 페이지·원거리 문단의 연결, 의미가 같은 번역·요약, 아직 규칙이 인식하지 못한 조건은 의미 검수와 담당자 확인이 필요함. 조건이 없는 원자료 전체를 복사하도록 강제하는 방식은 아니며 모든 임상 조건의 자동 인증도 아님. 실제 LLM의 조건 누락·인용 연결 검출 능력은 API 응답이 없어 계속 미평가임.

2026-10-02 21:22:43 UTC 실행의 RA 코드 SHA-256은 `bc7aa2d6e9237dccc4e9cad5d225c5b09bdc4102751aabcc0c90b42d460c98d0`임. [운영 결과의 checker_sha256](../outputs/ra-public-adversarial-operating.json)에 저장했으며 기록 시점의 `agent/ra.py`와 일치함을 대조함. 해당 실행은 실제 원문 기반 합성 변형 평가이며 네트워크·모델 요청 0회임.

## 결과 파일

- 출처 원본: `data/ra_public_validation/`, `data/ra_public_validation/kr/`, `data/public_templates/ra/`, `data/public_templates/ra_corporate/`의 SHA 고정 스냅샷임.
- 실제 원자료 교차평가: [EMA 9개 사례](../outputs/ra-real-source-ema.json), [한국어 4개 사례](../outputs/ra-real-source-korean.json)임.
- 최신 전체 회귀: [pytest 결과 XML](../outputs/repeat-xlsx/pytest.xml)의 2,272 passed·1 skipped·2 warnings·363.97초 기록임. [이전 1,258 passed](../outputs/pytest-ra-real-sources-final.xml)와 [이전 실패 기록](../outputs/pytest-ra-real-sources.xml)을 이력으로 보존함.
- 공식 법정 RA 코퍼스: [18종 전체 파싱·17종 기입 결과](../outputs/ra-compatibility-real-sources.json)임. 다른 기업·정부 전체 코퍼스의 분모를 합치지 않음.
- 직전 출력 렌더·시각 QA: [42쪽 이미지 및 확인 범위 인덱스](../outputs/ra-real-source-round/previews/rendered-pages.json)임. 새 시각 확인으로 확대하지 않음.
- 입력 항목 유형 반영 재실행: [EMA 9/9개](../outputs/ra-real-source-typed-ema.json), [한국어 2/4개](../outputs/ra-real-source-typed-korean.json)임. 재실행 출력의 시각 확인은 미수행임.
- 이번 오류 주입 결과: [정상 17개·선정 변형 92개](../outputs/ra-public-adversarial-operating.json), generic 65/92·RA 92/92·정답 비교 92/92임. [직전 실행](../outputs/ra-public-adversarial-final.json)의 RA 88/92·합집합 89/92·정답 비교만 검출한 3개와 구분함.
- 통합 근거: [종합 검증·해시 기록](../outputs/ra-real-source-verification.json)임. 실제 모델·KPI·보호 제한·기입 대기 항목을 별도로 기록함.
- 실제 모델 실행 실패: [EMA 실행 기록](../outputs/ra-real-source-live.json), [한국어 실행 기록](../outputs/ra-real-source-korean-live.json)임. 응답이 없어 모델 평가 점수는 산출하지 않음.
- 기업 부분 기입 QA: `outputs/ra_corporate_form_qa/`임. 원본·filled 결과·sidecar·페이지 PNG의 확인 범위를 개별 기록함.
- 전체 테스트·집중 회귀·교차평가·시각 확인·실모델·KPI 결과는 실행 시점과 분모를 구분하며 미평가를 성공으로 합치지 않음.

## 재현

프로젝트 루트의 Python 3.11 가상환경에서 현재 보관한 원본으로 실행함. 다운로드 옵션을 사용하지 않음. `--pdf-pages 0`은 evals.compatibility의 코퍼스 전체 파싱 옵션이며 evals.ra_public에는 없음. RA 원자료 전체 읽기는 `--check-parser`로 지정함.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_adversarial --output outputs/ra-public-adversarial-operating.json
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_ra_conditions.py tests/test_ra.py tests/test_ra_real_text.py tests/test_ra_public_adversarial.py tests/test_ra_public_evals.py tests/test_ra_korean_review.py
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --junitxml=outputs/pytest-ra-real-sources-final.xml
.\.venv\Scripts\python.exe -X utf8 -m evals.compatibility --provenance official_ra --no-samples --pdf-pages 0 --output outputs/ra-compatibility-real-sources.json
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --manifest evals/ra_public_sources.json --mode rules --check-parser --profile ra_law_form_4_pdf --profile ra_law_form_20_pdf --profile corporate_roche_supplier_change_request --output outputs/ra-real-source-ema.json --artifact-dir outputs/ra-real-source-round/ema
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --manifest evals/ra_korean_sources.json --mode rules --check-parser --profile ra_law_form_4_pdf --profile ra_law_form_20_pdf --output outputs/ra-real-source-korean.json --artifact-dir outputs/ra-real-source-round/korean
```

한국어 교차평가 명령은 에리우스 2개 사례가 보호 제한으로 통과하지 않아 종료 코드 1을 반환함. 한미 2개 통과와 에리우스 보류를 결과 JSON에서 구분해야 함. 전체 pytest의 skip 1개와 교차평가의 보호 보류 2개는 같은 제한을 서로 다른 검사 단위로 기록한 값임. `rules` 결과를 실제 LLM 평가 점수로 사용하지 않음.
