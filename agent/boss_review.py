"""상사 관점 질문을 생성하고 인용 자료에서 확인되는 보완만 반영함."""

import re

from agent.brief import model_profile
from agent.review import CITATION_PATTERN, inspect_draft, validate_draft


def _grounded(answer: str, sources: list[dict]) -> bool:
    ids = CITATION_PATTERN.findall(answer)
    source_map = {source["source_id"]: source["text"] for source in sources}
    if not ids or any(source_id not in source_map for source_id in ids):
        return False
    evidence = re.sub(r"\s+", "", " ".join(source_map[source_id] for source_id in ids))
    clean = CITATION_PATTERN.sub("", answer)
    words = re.findall(r"[가-힣A-Za-z]+", clean)
    stems = [re.sub(r"(?:함|임|은|는|을|를|의|이며|이고)$", "", word) for word in words]
    ignored = {"결과", "확인", "기록", "기준", "자료", "보고", "추가", "보완"}
    content = [stem for stem in stems if stem and stem not in ignored]
    # ponytail: 보완문은 원문의 내용어가 확인되는 범위로 한정함. 의미 유사도 승인 판단은 사람이 담당함.
    return bool(content) and all(stem in evidence for stem in content)


def boss_review(draft: dict, brief: dict, sources: list[dict], client=None, *, template_profile=None) -> dict:
    draft = validate_draft(draft)
    if client is None:
        from llm.client import LLMClient

        client = LLMClient()
    payload = {"draft": draft, "brief": brief, "sources": sources}
    if template_profile is not None:
        payload['template_profile'] = model_profile(template_profile)
    response = client.generate_json("boss_review", payload)
    if not isinstance(response, dict):
        raise ValueError("상사 리뷰 응답은 JSON 객체여야 함")
    questions = response.get("questions")
    if (
        not isinstance(questions, list)
        or len(questions) != 3
        or any(not isinstance(question, str) or not question.strip() for question in questions)
        or len({question.strip() for question in questions}) != 3
    ):
        raise ValueError("상사 예상 질문은 서로 다른 질문 3개여야 함")
    questions = [question.strip() for question in questions]
    answers = response.get("answers", [])
    if not isinstance(answers, list) or any(not isinstance(item, dict) for item in answers):
        raise ValueError("상사 질문 답변은 질문·답변 객체 목록이어야 함")
    answer_map = {}
    for item in answers:
        question, answer = item.get("question"), item.get("answer", "")
        if question not in questions or not isinstance(answer, str) or question in answer_map:
            raise ValueError("질문 답변의 연결이 잘못됨")
        answer_map[question] = answer.strip()
    updated = dict(draft)
    additional = []
    for question in questions:
        answer = answer_map.get(question, "")
        if not answer or not _grounded(answer, sources):
            additional.append(f"추가 확인 필요: {question}")
            continue
        if not re.match(r"^[□○-]\s*", answer):
            answer = "○ " + answer
        candidate = dict(updated, 본문=updated["본문"] + "\n" + answer)
        baseline = inspect_draft(updated, sources)
        issues = inspect_draft(candidate, sources)
        new_line = len(candidate["본문"].splitlines())
        if any(issue["field"] == "본문" and issue["line"] == new_line for issue in issues):
            additional.append(f"추가 확인 필요: {question}")
        elif sum(issue["severity"] == "error" for issue in issues) > sum(issue["severity"] == "error" for issue in baseline):
            additional.append(f"추가 확인 필요: {question}")
        elif answer not in updated["본문"]:
            updated = candidate
    return {"draft": updated, "questions": questions, "additional_checks": additional}
