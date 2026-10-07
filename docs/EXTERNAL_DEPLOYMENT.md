# 외부 시험 배포

이 배포는 공개 안내 페이지와 로그인한 사용자의 문서 작업 공간을 분리한다. 현재 PC가 실행되는 동안만 문서 처리 서버와 Windows 한글 변환 서버를 사용할 수 있는 시험 운영 구조이다. 서버가 꺼지거나 임시 터널 주소가 바뀌면 연결을 복구해야 한다. 공개 HTML만 게시한 상태와 실제 문서 처리 API 연결은 구분하여 확인한다.

## 사용 흐름

1. 공개 사이트에서 문서 작업 공간을 열고 ChatGPT 계정으로 로그인한다. 로그인 요청은 Sites의 `/signin-with-chatgpt` 화면으로 이동한다.
2. 빈 양식과 원자료를 올린다. 양식은 DOCX/HWPX/XLSX/PDF 및 연결된 Windows 서버에서 변환 가능한 HWP를 받는다. 원자료는 PDF/DOCX/HWP/HWPX/XLSX/PPTX/TXT/CSV/TSV/PNG/JPEG를 받는다.
3. 원본 위치와 항목명을 보고 매핑을 확인한다. 이번 작성 항목, 제품명, 제형·함량, 작성 지시를 지정한다. 신청인·연락처·서명·동의 등 직접 입력 칸은 담당자가 입력한다. 원본 필수 항목과 선택·수치 제약을 완화할 수 없다.
4. 실제 AI 작성 또는 확인한 원문 기입을 명시적으로 선택한다. 원문 기입은 제안한 전체 인용과 제품·원위치를 확인한 항목만 사용하며 AI 생성으로 표시하지 않는다. 실제 AI 작성 실패를 원문 기입이나 mock으로 바꾸지 않는다.
5. 부족한 정보, 출처·수치·필수 항목 검수 결과를 확인한다. 같은 작업의 실제 보완 답변은 정확한 질문 문자열을 키로 누적·복원하며 재생성·설정 변경·API 실패에도 보존한다. 새 질문에 다른 질문의 답을 옮기지 않으며 새 업로드 작업은 빈 답변으로 시작한다. 답변을 제품 사실의 원자료로 승격하지 않는다. 실제 AI 초안을 수정한 경우 다시 의미·완결성 검수를 실행한다. 미확인 OCR/전사문은 확인 전 작성 근거로 사용하지 않는다.
6. 최종 담당자 확인을 하고 완성 문서와 별도 출처·검수 기록이 포함된 ZIP을 내려받는다. 현재 양식 SHA·초안·근거·검수를 다시 대조하고 저장 후 원위치 검수가 통과해야 다운로드한다. 이 결과는 법정 제출 적합성이나 제출 완료 인증이 아니다.

이미지·스캔 자료는 자동 텍스트 읽기가 되지 않으면 보류된다. 화면의 스캔·이미지 원문 확인에서 원본 SHA별 전체 페이지 전사문을 등록하고 원문·페이지·출처 지문에 연결한 확인 기록을 남긴다. 읽지 못한 자료가 남아 있으면 작성·출력을 보류하며 보호·추출 제한을 전사로 우회하지 않는다.

## 실행 구조와 보호

```text
사용자 브라우저
  → 공개 Sites 안내 / 로그인한 문서 작업 공간
  → Sites Worker: 로그인 ID 확인 + 요청 전체 HMAC 서명
  → HTTPS 임시 터널
  → 127.0.0.1:8600 FastAPI 문서 처리
  → 127.0.0.1:8601 Windows 한글 변환 서버 (별도 Bearer 인증)
```

게이트웨이 비밀·변환 서버 토큰은 브라우저에 전달하지 않는다. 기본 Gemini 경로에서는 사용자가 자기 API 키를 암호 입력칸에 넣고 AI 작성·사진 읽기·재검수 요청에만 보낸다. 작업 기록에는 키를 저장하지 않으며 서버의 `GEMINI_API_KEY`는 필요하지 않다. FastAPI의 `/api/*`는 로그인 ID를 주장하는 브라우저 헤더만으로 승인하지 않으며 Sites Worker가 서명한 요청만 처리한다. 사용자 ID, 메서드, 경로/쿼리, 시각, nonce, 원문 바이트 SHA가 서명에 포함된다. 서명 시각은 120초 범위에서 검사하며 nonce는 SQLite에 저장하여 서버 재시작 후에도 유효 기간 내 재사용을 거부한다. CORS나 기존 로컬 API 경로를 공개하지 않는다.

자료는 서버가 선택한 `SHA256(사용자 ID)/무작위 작업 ID/` 디렉터리로 분리한다. 사용자가 임의 저장 경로나 다른 사용자의 작업 ID를 제공해도 접근하지 못한다. 파일명 경로·Windows 장치명·대체 데이터 스트림, 압축 파일의 경로·팽창 한도를 검사한다. 응답은 캐시하지 않으며 서버 로컬 경로·공급자 원시 오류·키를 표시하지 않는다.

시험 운영 한도는 요청 본문 16 MiB, 파일당 10 MiB, 원자료 12개, 사용자당 30개 작업·256 MiB, 전체 파일 저장 4 GiB이다. 추출한 전체 원문은 2,097,152자, 단일 문단·페이지 블록·표 행은 20,000자, 양식 입력칸은 2,000개, 저장 작업 JSON은 64 MiB 이하이다. 원문 문맥이 검색 조각마다 과도하게 복제되는 것을 막기 위해 조각을 만들기 전에 검사하며 초과한 원문을 잘라내지 않고 자료 묶음을 나누도록 안내한다. JSON·전사문·출처/검수 기록도 사용자·전체 저장 분모에 포함하고 원자적 교체 전에 한도를 검사한다.

인증 사용자당 분당 60개 요청을 허용하며 실제 AI 작업은 최대 2개를 동시에 처리한다. 실 AI 생성·재검수는 비동기 작업으로 접수하고 3초 간격의 상태 조회를 사용하여 HTTPS 터널 응답 제한을 피한다. 처리 중인 작업의 매핑 변경·삭제·출력을 차단한다. 재시작으로 중단된 작업은 실패로 표시하며 과거 완성 문서를 다시 다운로드하지 않는다.

Windows 변환 서버는 외부에 직접 공개하지 않는다. 실제 한글 보안 모듈 활성화·소유한 새 인스턴스·읽기 전용 사본·원본/결과 SHA·HWPX 구조 확인을 기존 변환 엔진에서 수행한다. 원본 HWP를 보존하고 변환 결과를 덮어쓰지 않으며 변환 후 이름이 충돌하는 첨부는 접수하지 않는다.

## 운영 설정

FastAPI 실행 명령:

```powershell
.venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv('.runtime/external-server.env'); import uvicorn; uvicorn.run('app.web_api:production_app',factory=True,host='127.0.0.1',port=8600,access_log=False)"
```

환경 설정은 외부 공개 저장소와 게시 빌드에 포함하지 않는다. `RA_GATEWAY_SECRET`은 32자 이상 무작위 비밀로 설정하고 동일 비밀을 Sites Worker의 비밀 환경 변수에 등록한다. `RA_WEB_DATA_ROOT`는 전용 서버 저장소이다. `RA_HWP_WORKER_URL`, `RA_HWP_WORKER_TOKEN`은 별도 변환 서버 연결을 지정한다. 실제 모델 설정은 기존 `llm/client.py`와 비공개 `.env`를 사용한다. 변환 서버 토큰과 게이트웨이 비밀은 서로 다르게 설정한다.

PC 시험 운영은 단일 FastAPI 프로세스를 전제로 한다. 상시 서버로 이전할 때는 작업 큐·자료 저장소·만료/삭제 정책·백업·접근 감사·이용자 허용 목록을 운영 환경에 맞춰 구성하고 검증한다. 공개 사이트의 ChatGPT 로그인은 사용자 신원을 제공하며 특정 제약회사 직원임을 자동 인증하지 않는다. 현재 사이트에 로그인할 수 있는 이용자의 범위와 사내 자료 업로드 정책은 운영자가 별도로 결정한다.

## API 계약

| 경로 | 요청 / 결과 |
|---|---|
| `GET /api/health` | 서명한 요청만 운영 상태 반환 |
| `POST /api/jobs` | `{template:{name,base64},sources:[{name,base64}]}` → 작업 ID, 원본 프로파일, 매핑 행, 원자료 목록 |
| `GET /api/jobs/{id}` | 현재 작업·처리 상태와 사용자 표시 초안/검수 결과 |
| `POST /api/jobs/{id}/configure` | `{confirmed:true,rows,selected_keys,product_name,variant,instruction,field_values,ra_workflow?}` |
| `POST /api/jobs/{id}/intake` | `{transcriptions?,receipts?,confirmed?}`; 전사문은 첨부 SHA, 확인은 출처 ID/현재 지문에 연결 |
| `POST /api/jobs/{id}/proposals` | 현재 원문 후보·전체 확인 지문 반환 |
| `POST /api/jobs/{id}/generate` | 실제 작성 `{mode:"live",answers?}` 또는 원문 기입 `{mode:"source_copy",confirmed_proposals,confirmed_keys}` |
| `POST /api/jobs/{id}/review` | 실제 AI 초안 수정본 `{draft}`; 새 의미·완결성 검수 |
| `POST /api/jobs/{id}/export` | `{confirmed:true}` → 현재 저장 후 검수를 통과한 문서와 출처 기록 ZIP |
| `DELETE /api/jobs/{id}` | 진행 중이 아닌 본인 작업 자료 삭제 |

실 AI 작업 접수는 HTTP 202와 `status:"processing"`을 반환한다. 완료 상태는 `ready` 또는 `needs_revision`, 실패는 `failed`와 안전한 `error:{code,message}`이다. `llm_quota`는 API 잔액/할당량 부족이며 `llm_rate_limit`과 구분한다. 한도가 부족하면 실제 AI 작성은 이용할 수 없으며 원문 기입은 사용자가 별도 선택한 경우에만 작동한다.

## 검증 기록

`tests/test_web_api.py`는 실제 합성 DOCX·TXT를 업로드하여 후보 확인·5개 칸 기입·독립 저장본 재열람·출처 ZIP을 검증한다. 서로 다른 사용자, 서명 변조/재전송/재시작, 경로 주입, 압축 팽창, 입력/저장/요청 한도, 원본 변경, 과거 검수 재사용, 누락 항목 출력 차단, 비동기 실패와 최초 KPI 기준 보존도 검사한다. API tests의 LLM은 mock/실패 주입만 사용한다. 실제 모델 생성, 실제 Windows 변환, 실제 외부 로그인·사용자 평가 결과는 별도 운영 점검 증거로 기록하며 이 테스트 통과만으로 주장하지 않는다.

게시 URL, 최종 전체 pytest 결과, 실제 HTTP·네이티브 점검 증거는 해당 배포 완료 보고에서 연결한다.

## 게시 및 실제 연결 확인 — 2026-10-05

- Site 버전5 게시 성공: https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site/workspace
- Site 소스 커밋: `605418113359bbec4862d554f5f376487b109f1d`; 게시 환경 개정1.
- 실제 외부 HTTPS 합성 DOCX/TXT→5/5칸 기입·출처 ZIP·독립 재판독·익명/타사용자/오래된 출력 차단: `outputs/external-http-b0ca1405/audit.json`.
- 실제 외부 HTTPS→작성 API→별도 Windows8601→공개 HWP 변환·SHA·원본 불변·HWPX 파싱: `outputs/external-hwp-link-1244c6e1/audit.json`.
- 두 실행의 인증은 서명된 시험 신원이며 실제 ChatGPT 브라우저 로그인을 대신 검증하지 않는다. 실제 모델 호출0·실사용 KPI 미측정이며 실제 AI에는 이용 가능한 모델/API 잔액이 필요하다.
- 같은 작업의 실제 보완 답변은 정확한 질문별로 저장·누적한다. 실패·재생성·설정 보완 뒤에도 유지하며 새 작업에는 넘기지 않는다.
- 장시간 HWP 일괄 업로드는 동기 처리이므로 시험용 터널의 응답 제한을 넘을 수 있다. 느린 원본은 나누어 올리거나 HWPX 변환 사본으로 첨부하며 원문을 잘라서 성공 처리하지 않는다.
- Quick Tunnel은 시험용이고 PC/연결 프로그램 중단 시 사용할 수 없다. 고정 주소·상시 운영 서버 이전은 별도 작업이다. [Cloudflare 공식 안내](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

최종 전체 테스트: **4,329통과·1보류·실패/오류0**, 334.28초. 수집/XML 4,330개 고유 ID와 현재 SHA 독립 대조를 통과함. `outputs/external-final-f419/audit.json`, `independent.json`, `outputs/external-deployment-final.json`에서 증거를 확인함.

## 가입 없는 접근과 연결 복구 — 2026-10-05, 버전9

배포 환경에서 `fetch`의 리다이렉트 오류 옵션을 거부하여 켜진 PC에도 연결 오류가 표시되었음. 수동 리다이렉트 모드로 바꾸고 300~399 응답 전체를 거부함. API 인증 정보는 다른 주소에 전달하지 않음. Site 소스 커밋은 `10f000ad9df3604fcab05ef2e667197a31b62a26`, 환경 개정3임.

현재 사용자의 실제 브라우저에서 새로고침 후 “서버 연결 완료”를 확인함. 별도로 플랫폼 로그인 없는 초대 사용자의 실제 HTTPS 업로드→선택 5칸 기입→검수→ZIP 다운로드→독립 DOCX 판독을 확인함. 다른 초대의 작업 접근, 폐기한 세션과 로그아웃한 세션을 차단함. HTTP 검증은 합성 자료이며 실제 직원의 브라우저 전체 작업·AI 작성·KPI 평가와 구분함.

담당자별 일회 초대는 [가입 없는 사용법](RA_NO_SIGNUP_GUIDE.md)에 따라 로컬 운영자만 발급함. 개인별 링크를 해당 담당자에게만 전달하며 일반 `/workspace` 주소만 보내면 가입 없는 입장이 되지 않음. 원문 후보는 담당자 확인 후 적용하고, 신원·서명·동의는 직접 입력함.

작성 API(8600), Windows 변환 서비스(8601), 터널은 숨김 백그라운드 프로세스로 실행함. 소유 PID·시작 시각 기록은 `outputs/external-*-owner.json`에 저장함. PC를 켜고 절전 없이 네트워크와 연결 프로그램을 유지해야 함. 자동 부팅·상시 서버·고정 터널은 아직 구성하지 않았음. 임시 터널 프로세스를 다시 만들면 새 주소를 Site 서버 환경에 반영해야 함. 단순 공개 HTML만으로 PC 중단을 해결할 수 없음.

최신 전체 pytest는 **4,413통과·1보류·실패/오류0**, 593.81초임. `outputs/guest-ra-final-7d63/`와 `outputs/guest-site-proof-964d3fac/audit.json`에 근거를 보존함. 실제 모델 연결은 `outputs/ra-model-connection-c91a22a9/audit.json`에서 quota 실패를 확인했으며 자동 유료 API/mock 대체를 하지 않음. 현재 Windows 변환 서비스의 실제 보안 모듈·소유권 health는 ready임. 실제 HWP 변환 증거는 앞선 `outputs/external-hwp-link-1244c6e1/` 기록과 구분함.
# Claude 구독 연결 추가 — 2026-10-05

기존 공개 Site 버전11의 `/claude-mcp`에 개인 키 인증 Streamable HTTP 도구를 게시함. 각 담당자는 작업실에서 전용 연결 키를 발급하고 본인 Claude의 custom connector Request headers에 입력함. 공개 소개·계정 로그인·가입 없는 초대와 Windows 변환 서비스는 유지함. `/mcp`는 호스팅 플랫폼 예약 주소여서 공개 연결에 사용하지 않으며 내부 PC HMAC 경로로만 사용함.

서버 유료 API를 사용하지 않는 작성/판독/검수 응답 전달 경로임. 웹사이트의 서버 API 작성 버튼과 구분하며, 실제 Claude 모델/구독 신원을 서버가 인증했다는 뜻은 아님. [설정·이어쓰기·다운로드](CLAUDE_MCP.md)와 [현재 검증](VALIDATION.md)을 참조함.

버전11 source `be17e0bee05732ebd4befcf6614f3288c217e362`, deployment `appgdep_6ac31c1e10ac8191bdbc060dab6e62a3`, env revision3임. `outputs/claude-mcp-deployment-final.json`에 전체4,509통과·1보류, 실제 HTTPS 합성/mock 응답 기입·다운로드와 개인 키 폐기/사용자 분리, 공개 자산 비밀값 검사·소유 프로세스를 연결함. 실제 Claude 계정 등록·모델 품질·직원 KPI·상시 서버 운영은 아직 검증하지 않음.

## CTD 외부 작업실 및 개인 Claude 미리보기 — 2026-10-05

Site 버전12 소스 `a540b400ed5a5941ab8c335d9b29317e7e91e89c`를 게시함. [외부 작업실](https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site/workspace)에서 CTD Module 1·3 절별 원문·출처·누락 미리보기와 확인 후 DOCX/HWPX 양식 사본·별도 근거 JSON 다운로드를 제공함. 개인 Claude 연결 주소는 동일한 `https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site/claude-mcp`이며 읽기 전용 `ra_ctd_preview`가 추가됨.

Site는 환경 개정4에서 현재 PC의 새 임시 HTTPS 터널에 연결함. 처리 API 8600과 Windows 한글 변환 8601의 소유 프로세스가 실행 중이며 변환 서비스 health의 available/module_active/ownership_verified가 모두 참임. 공개 Site의 JS와 합성 CTD 예제 양식 HTTP 200을 확인함. 실제 HTTPS에서 가입 없는 합성 사용자 업로드→선택 3절 원문 후보→저장 후 검수 통과 DOCX/출처 ZIP 다운로드, 다른 초대 접근 차단, 개인 키 MCP tools/list와 `ra_ctd_preview`를 확인함. 증거: `outputs/ctd-site-31d8524d/audit.json`. 실제 Claude 계정 설정·모델 작성, 실제 회사 자료, 직원 수정률/KPI는 확인하지 않음. PC/임시 터널이 중단되면 외부 작성도 중단되므로 상시 서비스로 주장하지 않음.

전체 pytest 재실행은 **4,558통과·1보류·실패/오류0**, 4프로세스, 실행 전후 코드/시험 입력 SHA 동일임(`outputs/ctd-share-final/audit.json`). 합성 예제 DOCX가 공개 허용 자산 목록에 누락된 시험 기대값을 수정해 다시 검증함.

## DMF → CTD 2.3.S 작업 초안 — 2026-10-05

Site 버전14에 DMF 원문 선택·원료명·제조원·완제 관계 확인, 2.3.S 절별 미리보기와 출처·누락 기록 다운로드를 추가함. 공개 양식 자산은 **합성 프로젝트 예제** `qos_dmf_demo_template.docx`임. 서버는 인증된 사용자 작업의 현재 원자료와 원본 SHA를 다시 대조하여 DOCX/HWPX 사본을 기입하고 저장 후 검수함. 입력 근거는 3.2.S 원문 발췌이며 실제 품질요약 평가·법정 제출 적합성이나 LLM 생성으로 표시하지 않음. [사용법](CTD_QOS_DMF.md)을 참조함.

현재 시험 연결은 새 Windows HWP 서비스 `127.0.0.1:8602`, 작성 API `127.0.0.1:8603`, 별도 임시 터널을 사용함. 로컬 실행 설정은 `app/serve_external_trial.py`가 사용자 전용 `%LOCALAPPDATA%/DocumentStandardAI/external-runtime.json`에서 읽으며 인증값은 코드·Site 소스에 저장하지 않음. 이 파일은 현재 Administrator와 SYSTEM만 읽도록 ACL을 제한함. Site 환경 개정5에서 새 터널과 서명 비밀을 함께 갱신했으며 이전 8600/8601/터널 소유 프로세스는 종료함. PC나 새 터널이 중단되면 외부 작성도 중단되므로 재시작 때 새 터널 주소를 Site 환경에 반영해야 함.

검증: 공개 Site 배포 상태 성공, 새 터널의 인증된 HTTPS 2.3.S 절 목록 7개 응답, Windows 변환 서비스 health의 보안 모듈 활성화·인스턴스 소유권 확인, 합성 DMF·DOCX/HWPX의 서버 시험 클라이언트 기입·출처·사용자 격리 및 수정 후 재검수 30개 관련 pytest 통과. 실제 외부 사용자의 브라우저 전 과정, 실제 제조원 DMF, 실제 Word/한글 출력과 실제 모델 생성은 이번 변경에서 별도 검증하지 않음. 전체 pytest 4,567개 통과·1개 보류 후 Site 자산/화면 테스트 2개가 새 기능 때문에 실패하여 해당 기대값을 수정했고 관련 회귀 30개를 다시 통과시킴; 수정 뒤 전체 집합의 재실행은 아직 수행하지 않음.

