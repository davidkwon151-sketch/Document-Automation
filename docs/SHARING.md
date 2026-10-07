# 외부 HTML 공유 페이지

## 최신 버전3 — 2026-10-04

같은 공개 링크에 **데이터로 자동 작성** 실제 화면과 합성 TXT→DOCX 5/5칸 기입·Word 1쪽 출력 이미지를 추가함. 로그인 없이 누구나 볼 수 있는 `public` 접근과 게시 `succeeded`를 확인함. 기존 3개 화면도 유지함. HTML의 전체 테스트 수치는 앞선 `outputs/ra-auto-final-8e21/audit.json`의 4,261통과·1건너뜀·실패0·261.50초를 표시함. 이번 정적 게시 변경은 별도 페이지 pytest **5통과**, JavaScript 구문 검사 성공임.

버전3 소스: `955251bf1efea541ccc70ba5c378f49c41a57bb4`. 공개 자산 12개와 정규화 hosting manifest 1개를 패키징하고 원소스와 대조함(`outputs/ra-share-v3-archive-check.json`). 빈 초기 화면과 명시된 합성 출력 이미지만 게시하며 개인 파일·키·사용자 문서는 포함하지 않음. Windows bash 패키징 제약은 공식 prepare-site-build와 Windows tar로 처리함.

**이 링크는 공개 웹 미리보기임.** 화면 탭·확대 이미지·빈 입력 틀·검증 기록을 제공함. 파일 업로드·실제 AI 작성·HWP 변환 백엔드는 로컬 프로그램에서 실행함. API 잔액으로 실제 AI 품질은 검증 대기이고 직원 KPI는 미측정임. 아래 버전1/2 기록은 이전 게시 이력으로 보존함.

2026-10-04 공개 게시 확인: [문서 표준화 AI AGENT · RA 작업실](https://ra-document-workspace-20261004.sooyeon-jun-0389.chatgpt.site)

이 주소는 RA 시험 버전의 실제 초기 화면 3개, 사용 순서, 전체 pytest 및 공개 원문 기입·HWP 변환의 검증 분모와 미완료 사항을 공유하는 고정 HTML 기록임. 버전2에서 RA 공식 변경 기입·글로벌 예제4종·공통 멀티모달 입력의 구현과 합성 검증 범위를 추가함. 실제 업로드·문서 생성·한글 변환 프로그램은 로컬에서 실행하며 외부 백엔드를 이 주소로 제공하지 않음.

공개 자산은 `share_site/dist/`의 HTML/CSS/JS, 실제 빈 초기 화면 이미지 3개, 프로젝트 아이콘, 개인정보 없는 빈 CSV/TXT 입력 틀, 집계 검증 JSON으로 한정함. API 키·환경 파일·사용자 자료·전체 원본 양식·실제 출력 문서는 게시하지 않음.

등록 정보는 `share_site/.openai/hosting.json`에 저장함. 다음 변경도 같은 Site를 재사용하며 새 Site를 등록하지 않음. Git credential은 파일에 저장하지 않음.

페이지 검사: `python -m pytest -q tests/test_share_site.py` → 4통과. `node --check share_site/dist/app.js` → 성공. Sites 게시 상태 `succeeded`, 공개 접근 `public`을 확인함.

현재 소스: `2dbf780617add9b5840ea7a6a6143f2bfc3dd64f`. Site 버전2 게시 상태 `succeeded`, 기존 공개 접근을 유지함. HTML의 전체 프로젝트 검사 **4,188통과·1건너뜀·실패/오류0·3경고·246.14초**는 `outputs/ra-global-9a31/audit.json`을 요약함. 독립 ID Counter·377대상 SHA 검수와 정적 페이지4테스트 재실행을 완료함.

이전 버전1 소스 `3157e78095c36d4bafc2dccf5658f7213e86e4d7`은 기존3,982통과 스냅샷으로 보존함. 새 버전의 RA 공식 변경14/18칸·독립판독14/14·1쪽과 글로벌4예제 Word4쪽·추가문구28/48검사·20미평가는 합성 입력의 검증이며 실제 AI/직원 KPI와 구분함.

Windows의 기본 게시 스크립트는 소스 push 후 패키징용 bash를 시작하지 못했음. 같은 push HEAD에서 공식 prepare-site-build 도구와 Windows tar로 dist만 패키징하고 네이티브 save/deploy로 게시 완료함. 개인정보·전체 원본·API 키·Python 백엔드를 게시하지 않았음.
