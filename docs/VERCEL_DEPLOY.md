# Vercel 시험 배포

- 공개 주소: https://document-standardization-agent.vercel.app/
- GitHub 저장소: `davidkwon151-sketch/Document-Automation`, 프로젝트 루트 디렉터리: `share_site`
- Vercel은 정적 작업실 화면과 초대 세션을 검증하는 Node 게이트웨이를 실행함. Python 문서 처리와 한글 HWP→HWPX 변환은 Windows PC 서버에서 실행함.
- Vercel Production 환경 변수 `RA_BACKEND_URL`과 `RA_GATEWAY_SECRET`을 **Secret**으로 등록하고 재배포해야 작성 API가 동작함. 비밀값은 Git·브라우저 코드·문서에 넣지 않음. 백엔드 주소와 서명 비밀값은 현재 PC의 운영 설정과 일치해야 함.
- 작업실 API는 운영자가 발급한 일회 초대 링크로 만든 HttpOnly 세션만 사용함. Vercel 방문자의 임의 `oai-authenticated-user-id` 헤더는 제거함.
- PC 서버·HTTPS 터널이 꺼지면 Vercel 화면은 열려도 작성 API는 사용 불가함. 임시 `trycloudflare.com` 터널 주소가 바뀌면 Vercel 환경 변수를 갱신하고 재배포해야 함. 상시 운영에는 고정 주소와 서버·저장소 이전이 필요함.
- Vercel Functions의 요청/응답 크기 제한은 4.5 MB임. 큰 스캔·PDF의 직접 업로드는 별도 안전한 파일 업로드 경로가 마련되기 전까지 제한될 수 있음.
- Gmail OAuth 클라이언트를 연결할 때 Google Cloud에 Vercel 주소 `https://document-standardization-agent.vercel.app/oauth/gmail/callback`을 승인된 리디렉션 URI로 등록하고, PC 서버의 `GMAIL_REDIRECT_URI`와 일치시켜야 함. OAuth 동의 화면·테스트 사용자·`gmail.readonly`/`gmail.send` 권한은 별도 설정임. 모델 키만으로 Gmail 권한을 얻을 수 없음.
- 검증 구분: Vercel의 화면 HTTP 200·비인증 API 401, mock 단위 테스트, 실제 초대 세션 업로드/작성/다운로드, 실제 Gmail 연결/발송, HWP 네이티브 변환은 각각 별도로 기록함.
