# Gmail 수신→초안→확정 발송 시험 연결

영업 작업실의 Gmail 연결은 Google OAuth 웹 애플리케이션 권한으로 동작함. 비밀번호나 API 키만으로 Gmail 수신·발송에 접근하지 않음. 서버가 켜져 있고 `GEMINI_API_KEY`가 설정된 경우 60초 간격으로 최근 받은편지함을 확인하고, 새 메일마다 출처 없는 회사 사실을 단정하지 않는 검토용 초안을 작성함. 초안은 자동 발송되지 않음. 담당자가 수신인·제목·본문을 확인하고 체크한 뒤 발송 버튼을 누르면 Gmail API로 한 번만 발송 시도함. 결과가 불확실한 경우 자동 재발송을 금지함.

1. Google Cloud 프로젝트에서 Gmail API를 사용 설정하고 OAuth 동의 화면을 구성함. 외부 시험 앱이라면 사용할 Google 계정을 테스트 사용자에 추가함.
2. OAuth 클라이언트 유형 `웹 애플리케이션`을 만들고 승인된 리디렉션 URI에 `https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site/oauth/gmail/callback`을 정확히 등록함.
3. 비공개 `.runtime/external-server.env`에 `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REDIRECT_URI`를 입력함. `GEMINI_API_KEY`는 프로젝트 루트의 비공개 `.env`에 입력함. 두 파일은 Git에 올리지 않음.
4. 외부 API 서버를 재시작하고 영업 작업실의 Gmail 연결 버튼에서 Google 계정을 승인함. 연결 후 새 메일 확인을 누르거나 자동 동기화를 기다림.

요청 권한은 `gmail.readonly`와 `gmail.send`임. Gmail 본문 읽기는 제한 범위이므로 외부 공개 운영에는 Google OAuth 검증과 경우에 따라 보안 평가가 필요함. 시험 단계의 허용 사용자와 운영 범위를 구분함. 받은편지함 최근 100건을 조회하며, PDF/첨부 파일의 회사 사실을 자동 근거로 승격하지 않음. 별도 회사 자료가 필요한 회신은 담당자가 기존 영업 작업실에서 업로드·보완하여 검수함. OAuth 연결 토큰은 사용자별 로컬 DB에 암호화해 저장하고, 연결 해제 시 로컬 토큰을 삭제함. Google 계정의 제3자 앱 접근도 별도로 철회할 수 있음.

실제 발송 시험은 본인 소유 테스트 메일함으로 수행하고, Google OAuth 설정·실제 Gmail 수신·Gemini 작성·Gmail 발송은 mock pytest 통과와 구분해 기록함.
