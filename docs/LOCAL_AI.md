# API 잔액 없이 개발하고 작성하는 방법

## 바로 사용할 수 있는 기능

파일 읽기·양식 분석·자리표시자 치환·수치/출처 검사·파일 저장과 pytest의 mock 시험은 OpenAI API 잔액 없이 실행함. RA 화면의 ‘공개자료 기입 데모’도 제공한 원문의 선정 항목을 기입하며 외부 AI를 호출하지 않음. 데모와 mock을 실제 AI 생성 정확도·직원 수정률로 집계하지 않음.

실제 AI 초안이 필요하면 설치한 로컬 모델로 작성·임베딩 검색을 실행할 수 있도록 `llm/client.py`에 local 연결을 추가함. 일반 화면과 RA 화면의 기존 작성·이중 검수 흐름을 사용함. 자동으로 유료 API에 전환하지 않으며, 로컬 모드에서는 저장된 OpenAI 키를 보내지 않음. OpenAI 호환 Chat Completions JSON 응답과 embeddings를 제공하는 로컬 서버가 필요함.

## 이 PC에서 시작하는 방법

확인한 PC는 RAM 약 16GB·Intel Iris Xe임. 설치된 Ollama와 실행 중인 11434/1234 포트는 없었음. 아래 4B 모델은 소형 시험 후보이며 실제 속도·한국어 RA 정확도를 측정한 추천 순위가 아님. 큰 모델과 긴 문맥은 추가 메모리가 필요하고 CPU 작성은 오래 걸릴 수 있음.

1. [Ollama 공식 Windows 안내](https://docs.ollama.com/windows)에 따라 설치함. 모델별 라이선스와 회사의 로컬 소프트웨어 사용 정책을 확인함.
2. Ollama 서버에 `OLLAMA_NO_CLOUD=1`을 설정하고 재시작함. 이는 문서 프로그램의 `.env`가 아니라 **Ollama 프로세스의 환경 설정**임. [공식 FAQ](https://docs.ollama.com/faq)에 설정 방법이 있음. cloud 모델·별칭을 사용하지 않음.
3. 다음 명령으로 작성용 모델과 검색용 모델을 내려받음. 최초 다운로드에는 인터넷과 디스크 공간이 필요함. 현재 공식 모델 페이지 기준 Qwen3 4B는 약 2.5GB, EmbeddingGemma는 약 622MB이며 실행 메모리와 프로그램 설치 용량은 별도임.

```powershell
ollama pull qwen3:4b
ollama pull embeddinggemma
ollama list
```

4. 프로젝트의 `.env`에 다음 설정을 추가함. 기존 `OPENAI_API_KEY` 값은 덮어쓰거나 공유할 필요가 없음. 환경변수가 `.env`보다 우선하므로 기존 `LLM_PROVIDER` 환경 설정도 확인함. 예시 파일은 [config/local.env.example](../config/local.env.example)임.

```dotenv
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_LLM_MODEL=qwen3:4b
LOCAL_EMBEDDING_MODEL=embeddinggemma
```

5. 문서 프로그램을 다시 실행하고 RA 화면에서 ‘실제 AI 작성’을 선택함. 서버가 준비되지 않으면 연결 오류로 중단하며 데모 응답으로 바꾸지 않음.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/ra_mvp_ui.py --server.port 8502
```

기본 local 호출 시간 제한은 180초이며 재시도·토큰 사용량 기록과 JSON 검사 경계를 함께 적용함. 긴 지시나 원자료는 모델 문맥 한도를 넘을 수 있음. 입력을 조용히 잘라내거나 미완료 응답을 성공으로 처리하지 않음. Ollama의 문맥 설정은 공식 FAQ를 참고하되 가용 메모리와 생성/검수 품질을 함께 시험함. `qwen3:4b`는 텍스트 모델이므로 스캔/OCR·새 PDF 이미지 분석에는 별도의 **이미지 입력 지원 모델**을 선택해야 함.

## 검증 범위와 품질 기준

현재 local 연결의 생성·이미지 요청·임베딩·JSON·재시도·타임아웃·오류·주소 경계는 네트워크 없는 mock 시험으로 확인함. 이 PC에 로컬 런타임/모델을 설치하거나 실제 로컬 추론을 실행한 결과가 아님. 실제 로컬 모델 응답과 담당자 수정 KPI는 아직 미측정임.

RA 제품·제형·시험번호·기간·용량·회사 역할·원문 절·출처와 원본 양식의 위치/서식 검수를 로컬 작성에도 적용함. 작은 모델의 생성 결과가 기존 검수에서 차단될 수 있으며 사실·법적 적합성·수정 0%를 보장하지 않음. 모델을 바꿀 때 `evals/`의 실제 모델 평가와 담당자 최종본으로 수정 비율·시간·반려를 별도 측정함.

원격 URL·인증 정보 포함 주소·HTTP 리다이렉트·환경 프록시를 local 연결에서 허용하지 않음. localhost 주소는 127.0.0.1로 고정함. 이 경계가 로컬 서버의 임의 사용자 별칭까지 검사하는 것은 아니므로 Ollama의 cloud 비활성화와 실제 설치 모델 확인을 함께 수행함. LM Studio·llama.cpp의 로컬 OpenAI 호환 서버도 연결 설정을 지정할 수 있으나 실제 제품별 버전 호환은 아직 검증하지 않았음.

공식 근거: [Ollama의 OpenAI 호환 API](https://docs.ollama.com/api/openai-compatibility), [로컬/클라우드 구분과 설정](https://docs.ollama.com/faq), [Qwen3 4B](https://ollama.com/library/qwen3:4b), [EmbeddingGemma](https://ollama.com/library/embeddinggemma). OpenAI JSON 형식 동작은 [공식 OpenAI 문서](https://developers.openai.com/api/docs/guides/structured-outputs)를 참고함.
