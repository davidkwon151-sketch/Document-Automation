# RA 공개 원자료·회사별 양식 교차 검증

2026-10-03 KST, 서브에이전트 3개로 공식 원자료 수집, 기업 양식 등록·PDF 보존, 출처·평가 검증을 병렬 수행함. 인터넷에서 확보한 서로 다른 회사의 실제 제품 설명서와 공개 빈 양식으로 **선택 기입 30건 통과·추출 권한 제한 5건 보류**를 확인함. 전체 프로젝트의 실사용 완료 선언은 아니며, 실제 LLM 초안과 RA 담당자의 수정 KPI는 아직 미평가임.

## 결과와 분모

| 검사 | 결과 | 범위 |
|---|---|---|
| 최종 전체 pytest | **2,608 passed·1 skipped·2 warnings·472.87초** | 실패·오류 0, 종료 코드 0. 동결 대상 238파일의 실행 전후·최종 SHA 일치. wrapper 시간은 474.47초임 |
| 계획한 교차 사례 | **35건 = 허용 30건 + 보호 보류 5건** | 원자료 7종 × 양식 5종. 보호 자료 1종의 5개 시도는 성공률 분모에서 숨기지 않음 |
| 운영 M1 전체 파싱 | **6종·378쪽·127표·선택 사실 28개** | 허용 원자료 전체를 읽음. 보호 PDF 3쪽·6사실은 자동 파싱·점수에서 제외함 |
| 선택 값·출처 | **전체 원문 74/74·유효 출처 74/74** | 28개 고유 사실을 여러 양식으로 옮긴 74회임. 물리적 기입 위치도 74개로 독립 대조함 |
| 수치 포함 항목 | **61/61 원문 일치** | 고유 수치 사실 23개가 여러 출력에 사용된 횟수임. 모든 제출 항목의 수치 정확도를 뜻하지 않음 |
| 독립 재열기 | **30출력·96쪽** | 값·페이지·원본 SHA·회사/제품/역할/관할·원문 위치·출처 sidecar·실행 UUID 연결을 확인함 |
| 추가 출력 검수 | **6통과·24경고** | `pdf_copy`: 별첨 범위 경고 12건, native 입력의 인쇄 위치 추가 확인 경고 12건. 30건 모두 추가 검수 통과로 표시하지 않음 |
| 시각 QA | **96쪽 렌더·45고유 이미지·몽타주 8개 직접 확인** | 51쪽은 확인 이미지와 PNG SHA 동일. 선택 15쪽을 확대 확인함(고유 이미지 13개). 모든 96쪽 확대 검토는 아님 |
| PDF 한글 재편집 | **한국어 3제품의 기입본 3쪽 + 재편집본 3쪽 직접 확인** | SGS 별도 회귀. 한글·숫자·`g` 아래획 표시와 원문/AP·경계·원본 밖 pixel 보존 확인. 최종 matrix 96쪽에 합산하지 않음 |
| 고의 오류 회귀 | **정상 17/17·변조 오류 92/92 검출** | EMA 3종의 선택 사실 14개에서 만든 오류셋임. 일반 검수 65/92, 운영 RA 검수 92/92, 별도 정답 대조 92/92를 구분함 |
| 연결·전문 업무 회귀 | **mock 상태 15/15·업무 규칙 24/24** | mock 점수 대상은 14초안, 되묻기 1건은 점수 제외. RA·Business·Office 각 8건임 |
| 실제 모델·사용자 KPI | **응답 0건·확정 사용자 관측 0건** | 현재 API 키 설정이 없어 모델 요청을 실행하지 않음. 사용자 수정 비율을 0%로 표시하지 않음 |

최종 [종합 감사](../outputs/ra-cross-company/verification.json), [matrix 감사](../outputs/ra-cross-company/matrix/audit.json), [사전 분모](../outputs/ra-cross-company/matrix-denominators.json), [JUnit](../outputs/ra-cross-company/pytest-full.xml), [테스트 감사](../outputs/ra-cross-company/tests-audit.json), [시각 확인 기록](../outputs/ra-cross-company/matrix/visual-qa.json)에 원본·코드·프롬프트·프로파일·출력·PNG 해시를 연결함. pytest의 skip 1개는 에리우스 원본 EXTRACT 권한 제한임. 경고 2개는 Starlette/httpx 사용 중단 예고와 openpyxl x14 확장 읽기 경고이며 해당 부품의 원본 보존 회귀는 통과함.

## 실제 원자료

회사 역할과 제품 제형을 원문 구간에 묶어 저장함. EMA의 판매허가권자를 제조원·한국 신청인으로 바꾸지 않으며, 설명서 발행 회사도 제조원으로 추정하지 않음.

| 공식 공개 원자료 | 원문 회사 역할 | 전체 쪽 | 선택 사실 |
|---|---|---:|---:|
| [Herzuma — EMA](https://www.ema.europa.eu/en/documents/product-information/herzuma-epar-product-information_en.pdf) | Celltrion Healthcare Hungary Kft., EU 판매허가권자 | 66 | 4 |
| [Benepali — EMA](https://www.ema.europa.eu/en/documents/product-information/benepali-epar-product-information_en.pdf) | Samsung Bioepis NL B.V., EU 판매허가권자 | 136 | 5 |
| [Keppra — EMA](https://www.ema.europa.eu/en/documents/product-information/keppra-epar-product-information_en.pdf) | UCB Pharma SA, EU 판매허가권자 | 170 | 5 |
| [한미플루 설명서 — 한미약품](https://common.hanmi.co.kr/upfile/ces/product/40bba798-dedc-4ec2-8768-201a152df463.pdf) | 한미약품(주), 설명서 발행 주체 | 2 | 5 |
| [액티민주사 설명서 — 동국제약](https://www.dkpharm.co.kr/library/download_prod.php?idx=82) | 동국제약(주), 원문 명시 제조원 | 2 | 5 |
| [로수로드정 설명서 — 종근당](https://ckdpharm.com/downloadCkd.do?attFileNo=39741&saveFileName=3ebfa274a6afd054b98634ddd7bc3bc37a63f46600f02bce6c1ecbdbe74ed93b) | 종근당, 설명서 발행 주체 | 2 | 4 |

정답에는 선택 제형·함량의 문단 전체를 저장함. 액티민의 염산염 5.46mg/유효성분 5mg, 로수로드 5mg의 칼슘염 5.20mg/유효성분 5mg, 투여 경로·증감 조건·첨가제·1~30℃ 등을 생략하거나 임의 환산하지 않음. 설명서에 없는 사용기간은 생성하지 않음. 긴 원문은 본칸의 별첨 참조와 검토용 별첨 전체 원문·출처로 보존함.

에리우스는 별도 3쪽·6사실 snapshot이 있으나 추출 허용 확인이 안 되어 5양식 모두 보류함. 보호를 OCR로 우회하지 않음. 신규 국내 후보는 8개 PDF 중 2개를 선정하고 이미지 전용·손상 문자·제품명 불확실·미주석 6개를 제외함. 다운로드 실패 2건도 구분함. [추가 수집 기록](RA_ADDITIONAL_PUBLIC_SOURCES.md)에 공식 페이지·버전·SHA·제외 사유를 기록함.

최종 평가에는 확보한 고정 snapshot을 사용함. 추가 원격 확인에서 Benepali·Keppra·한미플루 3개는 HTTP 200과 기존 SHA 일치를 확인했고 Herzuma는 HTTP 429로 현재 원격 버전 재확인을 보류함. [원격 확인](../outputs/ra-cross-company/remote-snapshots.json)은 모든 자료의 현행성 인증이 아님. 신규 설명서의 인쇄 버전은 액티민 2021-04-20, 로수로드 2025-08-14이며 최신 국내 허가 내용과 별도 대조해야 함.

## 서로 다른 작성 양식

| 양식 | 실제 사실 교차 기입 | 구분 |
|---|---:|---|
| [의약품 품목허가 등 별지 제4호](https://www.law.go.kr/LSW/lsBylInfoP.do?bylSeq=18000713&lsiSeq=284019&efYd=20260305&directYn=Y) | 6회사 | 공개 법정 양식의 선택 약품 항목·검토용 별첨임 |
| [의약품 갱신 별지 제20호](https://law.go.kr/LSW/lsBylInfoP.do?bylSeq=18000749&lsiSeq=284019&efYd=20260305) | 6회사 | 선택 약품 항목임. 실제 갱신 대상 여부·허가번호·유효기간·제출 요건은 미확인임 |
| [Roche Supplier Initiated Change Request](https://assets.roche.com/f/94122/x/9c208e373e/supplier-initiated-change-request-form.pdf) | 6회사 | 공개 공급업체 변경 양식의 제품/재료 설명 선택칸임 |
| [Eurofins Sample Submission Form](https://cdnmedia.eurofins.com/european-east/media/2854407/ebpt-sweden-sample-submission-form_ifyllbar.pdf) | 6회사 | 공개 의약품 시험 의뢰 양식의 제품명 선택칸임 |
| [SGS Health Science Sample Submission Form](https://www.sgs.com/en/-/media/sgscorp/Documents/Corporate/Brochures/SGS-HN-US-Health-Science-Sample-Submission-Form-EN.cdn.en.pdf) | 6회사 | 공개 의약품 시험 의뢰 양식의 제품명 native 입력칸임 |

이 교차 기입은 실제 거래·공급 관계·시료 시험 의뢰를 뜻하지 않음. 제조소·신청인·연락처·실제 로트·서명·선택/승인·필수 첨부는 확인 없이 채우지 않고 미작성 목록으로 남김. 공개 회사 양식은 외부 제출용이며 해당 회사의 비공개 사내 RA 양식이라고 표시하지 않음.

이번에 Eurofins·SGS·[Thermo Fisher/Gibco](https://documents.thermofisher.com/TFS-Assets/LSG/brochures/gibco-custom-serum-request.pdf) 3양식을 추가 등록함. 원본 canonical 필드 232개 중 선택 13칸만 합성 기입·보존 검증함. RA 프로파일은 현재 21종·445등록칸임. 이 수치가 전체 칸 작성 완료율은 아님. Thermo Fisher는 맞춤 혈청 요청이라는 다른 업무이므로 의약품 사실의 교차 평가에서 제외하고 합성 부분 기입만 확인함. 신규 원본·합성/기존 제품명 선택 QA 26쪽은 별도 범위임.

## 발견한 오류와 수정

- 회사명 문자열만 일치해도 제조원으로 인정하던 판단을 보강함. 회사+역할 원문 구간, 국지적 미확인/부정 표현, 회사명 접두어 혼동, 페이지를 넘는 역할 제목과 다음 절의 경계를 검사함. 회사·역할·제품·관할은 검색 자료와 검수 fingerprint에도 포함함.
- PDF 입력칸의 위치가 바뀌어도 값만 같으면 지나가던 독립 검수를 수정함. 원래 페이지·사각형·부모·컨트롤 종류·옵션·길이·미선택 상태·기존 자원을 대조함.
- SGS의 간접 글꼴 자원 처리 실패를 수정함. 한글 `/V`와 표시 `/AP`를 함께 확인하고 미기입 컨트롤과 원래 글꼴 자원을 보존함.
- `▪/℃` 누락에 공식 Noto Sans KR/OFL 글꼴 fallback을 추가함. 원문 기호를 바꾸지 않음. 원본이 자동 글꼴 크기 0을 선언한 native 칸만 실제 글꼴 metrics로 맞추며 고정 크기 양식의 넘침은 차단함.
- 한글 PDF의 외부 재편집 때 글자가 사각형으로 변하는 CID 매핑과 `g` 아래획 잘림을 수정함. 글리프 ID·폭·ToUnicode의 일관성, 기존 자원 유지·새 버전 자원, 재편집 후 전체 Unicode·표시 경계를 회귀로 확인함. 원문과 글리프 역매핑이 모호하면 조용히 잘못 저장하지 않고 차단함.
- 프로파일의 `demo_values`, `qa`, 검증용 메타데이터를 실제 초안/검수 모델 payload에서 제외함. 수집된 시험값을 사용자 정보나 실제 작성 근거로 사용하지 않음.

최종 PDF 관련 집중 테스트는 173개 통과이며 [한글 재편집 근거](../outputs/ra-cross-company/pdf-reedit-fix/qa.json)에 새 기입본·재편집 probe·PNG·원본/코드 SHA를 보존함. `AGENTS.md`에 회사 역할·원본 입력칸 보존·glyph·재편집 검증 규칙을 추가함.

## 실패·중단 기록

최초 matrix는 35시도 중 27통과·보호 5보류·SGS 한글 처리 실패 3건이었음. 간접 자원 수정 후 두 번째 matrix는 30통과였으나 외부 재편집에서 별도 glyph 문제를 발견함. 재편집 수정 후 최종 matrix를 새 UUID로 다시 실행하여 30통과를 확인함. 세 실행을 새 회사나 고유 사실 수로 합산하지 않음.

[최초 실행 보존](../outputs/ra-cross-company/matrix-first-attempt/preservation.json), [두 번째 실행 보존](../outputs/ra-cross-company/matrix-second-attempt/preservation.json), [첫 pytest 중단](../outputs/ra-cross-company/interrupted-full-test/interrupted.json), [두 번째 pytest 중단](../outputs/ra-cross-company/interrupted-full-test-2/preservation.json)에 당시 결과를 남김. 이 단계의 전체 pytest는 수정 때문에 2회 의도적으로 중단했으며 마지막 1회만 완료된 전체 결과임. 중단 로그를 전체 통과 기록으로 세지 않음.

초기 SGS 수정 QA의 일부 leaf 출력 경로가 후속 probe 실행에서 재사용된 문제가 있었음. 원래 기입본 3개는 당시 UUID 출력에서 복구하여 최초 SHA와 일치를 확인했지만, 복구한 과거 실패 probe는 최초 SHA가 없으므로 byte 동일성을 검증하지 못함. [복구 공개 기록](../outputs/ra-cross-company/sgs-hangul-fix/evidence-recovery.json)에 이 한계를 남김. 최초/두 번째 matrix와 원자료는 유지했고 최종 재편집 QA는 별도 경로의 새 증거임.

## 재실행

원자료와 공개 양식은 Git에서 제외된 `data/`에 보관함. 다른 환경에서는 manifest·프로파일의 공식 URL에서 파일을 확보하고 SHA를 맞춰야 함. 원본 누락·변경은 실자료 평가 실패로 기록하며 원본이 없는 pytest 실자료 항목의 skip을 성공으로 해석하지 않음.

프로젝트 루트의 PowerShell에서 실행함. 아래는 검증 보존 경로와 다른 `recheck` 경로에 저장함.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
$raProfiles = @('--profile','ra_law_form_4_pdf','--profile','ra_law_form_20_pdf','--profile','corporate_roche_supplier_change_request','--profile','corporate_ra_eurofins_sample_submission','--profile','corporate_ra_sgs_sample_submission')
foreach ($raManifest in @('ra_public_sources','ra_korean_sources','ra_additional_sources')) {
    .\.venv\Scripts\python.exe -X utf8 -m evals.ra_public --mode rules --native-review auto --check-parser --manifest "evals/$raManifest.json" --output "outputs/ra-cross-company/recheck/$raManifest.json" --artifact-dir "outputs/ra-cross-company/recheck/$raManifest" @raProfiles
}
.\.venv\Scripts\python.exe -X utf8 -m evals.ra_adversarial --output outputs/ra-cross-company/recheck/adversarial.json
```

한국어 manifest의 종료 코드 1은 에리우스 보호 보류 5건을 포함한 결과임. 각각 EMA 15/15·기존 한국어 5/10·추가 한국어 10/10 기입으로 확인함. 보호 보류를 없애려고 권한을 변경하지 않음.

실제 LLM 평가는 `.env` 설정 후 `evals.ra_public --mode live`를 같은 manifest·profile·새 출력 경로로 실행해야 함. 프롬프트/모델/원자료/프로파일 해시와 응답 획득 수를 남기고 오류를 mock 성공으로 대체하지 않음. 담당자의 최초 AI 초안과 실제 최종본을 수집해 수정 비율·초안→확정 시간·반려/재작업 횟수를 측정해야 프로젝트 KPI 개선을 판단할 수 있음.

**남은 검증:** 실제 모델의 한국어 초안·오탈자·요약/번역의 의미 동등성, RA 담당자 최종본 비교, 최신 국내 허가/서식 버전·필수 첨부·서명·제출 경로, 실제 Acrobat/Hancom 및 여러 PDF 편집기, 미등록 항목·장문·이미지 전용 원자료임. 이번 `rules` 원문 복사 평가를 사람 수정 0%·전체 회사/관공서 호환·신약 허가 제출 완료로 확대하지 않음.

**커밋 메시지 제안:** `feat: validate cross-company RA sources and preserve editable PDF forms`
