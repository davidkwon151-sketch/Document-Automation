# 별도 Windows 한글 변환 서버

`app/hwp_worker_api.py`는 문서 작성 서버와 별도의 프로세스로 실행하는 HWP→HWPX HTTP 서비스임. Windows에 한글과 승인된 보안 모듈이 설치되어 있어야 함. 기존 `parsers.hancom`의 새 인스턴스 소유권·실제 모듈 활성화·읽기 전용 사본·원본 SHA·HWPX 구조 검증을 그대로 수행함.

## 실행

외부 공유용 정적 파일과 Git에서 제외한 `.runtime/external-server.env`에 충분히 긴 임의 값의 `RA_HWP_WORKER_TOKEN`을 설정함. 문서 작성 서버에도 같은 값을 별도 보안 환경 변수로 설정함. 토큰은 사용자 화면·공개 HTML·URL에 넣지 않음.

```powershell
.venv\Scripts\python.exe -X utf8 -c "from dotenv import load_dotenv; load_dotenv('.runtime/external-server.env', override=True); import uvicorn; uvicorn.run('app.hwp_worker_api:app', host='127.0.0.1', port=8601, access_log=False)"
```

한 프로세스로 실행하며 다른 한글 작업을 동시에 넣지 않음. 현재 PC 시험 배포에서는 루프백만 수신하고 사용자에게 이 포트를 직접 공개하지 않음. 다른 Windows 서버로 옮길 때는 사설 네트워크 또는 TLS로 보호한 전용 경로를 사용함. 인터넷에 HTTP 포트를 그대로 열지 않음.

## 호출

모든 호출에 `Authorization: Bearer <RA_HWP_WORKER_TOKEN>`이 필요함. 누락·틀린 인증은 401, 서버 토큰 미설정은 503으로 차단함.

- `GET /health`: 현재 네이티브 활성화·소유권 확인 결과를 반환함. 경로·레지스트리 값·PID는 응답에 포함하지 않음.
- `POST /convert`: JSON `{"name":"양식.hwp","base64":"..."}`을 받아 `name`, `base64`, `source_sha256`, `output_sha256`을 반환함. 파일 이름은 표시용이며 서버가 임시 입력·출력 경로를 생성함. 요청에 경로나 추가 필드를 넣을 수 없음.
- 원본은 비어 있지 않은 10 MiB 이하의 HWP만 허용함. 보호·암호·서명·실행 스크립트·미확인 구조는 기존 어댑터가 거부함. 정상 변환도 결과 HWPX를 다시 파싱하며 원본·결과 SHA를 응답에 연결함. 요청별 임시 파일은 처리 후 삭제함.
- 실행 중 다른 요청은 429, 변환 실패는 안전한 안내문과 422, 변환 제한시간 초과는 504를 반환함. 원시 네이티브 오류나 인증 정보를 응답에 노출하지 않음.

## 검증 기록

`tests/test_hwp_worker_api.py`의 15개 테스트는 네트워크·실제 한글 없이 인증·파일 격리·경로 거부·용량 제한·SHA·임시 파일 삭제·직렬화·안전한 오류를 검증함.

별도의 실제 HTTP 네이티브 확인은 `outputs/external-hwp-1b8d156e/audit.json`에 기록함. 확보된 공개 NH농협은행 민원 HWP 원본을 보호된 로컬 서비스로 전송해 200 응답, HWPX 파싱, 원본 불변, 원본/출력 SHA 일치를 확인함. 이 결과는 해당 선정 원본의 변환 확인이며 모든 HWP 양식의 배치 보존이나 법정 제출 적합성을 보장하지 않음. AI 생성 평가와도 구분함.

현재 시험 서비스 프로세스의 정확한 PID·시작 시각·세션은 `outputs/external-hwp-worker-owner.json`에 저장함. 재시작·종료 전 실제 프로세스와 이 기록을 대조하고 다른 사용자 한글 프로세스는 종료하지 않음.
