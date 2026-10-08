"""Record the real sales-workspace UI against an isolated, deterministic test API.

Only fictional input is used. The application HTML, JavaScript, parser, job API,
progress events, review, and output widgets run unchanged; the model is a test
double, so the recording must never be described as a live Gemini result.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from playwright.async_api import async_playwright
from dotenv import dotenv_values

from app import web_api


ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "share_site" / "dist"
FRAMES = ROOT / ".runtime" / "demo_sales_frames"
SECRET = "demo-only-gateway-secret-" + "x" * 40
BUYER = """Hello Sales Team,

Could you quote 500 standard office chairs for delivery to Dallas?
Please include the estimated lead time and delivery terms.

Thank you.
Demo Buyer"""


class DemoModel:
    def generate_json(self, name, payload):
        time.sleep(1.5)
        if name == "buyer_email":
            return {
                "opening": "Dear Demo Buyer,\nThank you for your inquiry.",
                "closing": "Best regards,\nSales Team",
                "requests": [
                    {"buyer_quote": "Could you quote 500 standard office chairs for delivery to Dallas?",
                     "answer": "Could you confirm the preferred chair specifications?",
                     "evidence": []},
                    {"buyer_quote": "Please include the estimated lead time and delivery terms.",
                     "answer": "Could you confirm your preferred delivery date?",
                     "evidence": []},
                ],
            }
        assert name == "buyer_email_review"
        return {"complete": True, "email_prose_supported": True,
                "items": [{"index": 1, "supported": True}, {"index": 2, "supported": True}]}


def mime(path: Path) -> str:
    return {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}.get(path.suffix, "application/octet-stream")


async def record(*, live: bool = False) -> None:
    gemini_key = (dotenv_values(ROOT / ".env").get("GEMINI_API_KEY") or "").strip() if live else "demo-key-only-aaaaaaaaaaaaaaaa"
    if live and not gemini_key:
        raise RuntimeError("GEMINI_API_KEY is missing")
    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("*.png"):
        old.unlink()
    with tempfile.TemporaryDirectory(prefix="sales-video-api-") as private, TestClient(
            web_api.create_app(root=private, secret=SECRET,
                               client_factory=None if live else DemoModel),
            raise_server_exceptions=False) as api:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1,
                                          accept_downloads=True)

            async def route(request_route):
                request = request_route.request
                path = request.url.split("demo.invalid", 1)[-1].split("?", 1)[0]
                if path.startswith("/api/"):
                    body = request.post_data_buffer or b""
                    stamp, nonce, user = str(int(time.time())), uuid4().hex, "demo-viewer"
                    headers = {"X-RA-User": user, "X-RA-Time": stamp, "X-RA-Nonce": nonce,
                               "X-RA-Signature": web_api.signature(SECRET, request.method, path, stamp, nonce, user, body)}
                    if body:
                        headers["Content-Type"] = "application/json"
                    reply = api.request(request.method, path, headers=headers, content=body)
                    await request_route.fulfill(status=reply.status_code, body=reply.content,
                                                content_type=reply.headers.get("content-type", "application/json"))
                    return
                target = DIST / ("sales.html" if path in {"", "/"} else path.lstrip("/"))
                if not target.is_file() or not target.resolve().is_relative_to(DIST.resolve()):
                    await request_route.fulfill(status=404, body="Not found")
                    return
                await request_route.fulfill(body=target.read_bytes(), content_type=mime(target))

            await page.route("https://demo.invalid/**", route)
            await page.goto("https://demo.invalid/sales.html", wait_until="networkidle")
            await page.locator("#sales-status").wait_for()

            captures: list[dict] = []

            async def snap(label: str, hold: float = 0.65):
                path = FRAMES / f"{len(captures):03d}.png"
                await page.screenshot(path=str(path), animations="disabled")
                captures.append({"file": path.name, "label": label, "hold": hold})

            await snap("실제 해외영업 작업실 화면", 2)
            await page.locator("#sales-intake").scroll_into_view_if_needed()
            await snap("받은 메일 입력", 1.6)
            box = page.locator("#buyer-email")
            for segment in ["Hello Sales Team,\n\n",
                            "Could you quote 500 standard office chairs for delivery to Dallas?\n",
                            "Please include the estimated lead time and delivery terms.\n\n",
                            "Thank you.\nDemo Buyer"]:
                await box.type(segment, delay=13)
                await snap("가상 바이어 문의 입력", 2)
            if live:
                await page.locator("#sales-sources").set_input_files(str(ROOT / "docs" / "demo_sales" / "sample_company_facts.txt"))
                await snap("가상 회사 원자료 첨부", 2)
            await page.locator("#sales-upload").click()
            await page.wait_for_function("document.querySelector('#sales-writing-gate').textContent.includes('원자료 준비 완료') || document.querySelector('#sales-writing-gate').textContent.includes('이메일만 등록')")
            await snap("메일 등록과 요청 준비 완료", 2)
            await page.locator("#sales-writing").scroll_into_view_if_needed()
            await snap("회신 언어·톤 선택", 2)
            await page.locator("#sales-gemini-key").fill(gemini_key)
            await page.locator("#sales-draft").click()
            await snap("실제 작성 작업 시작 · " + ("Gemini 모델 호출" if live else "시험 모델 응답"), 1.5)
            await page.wait_for_function("!document.querySelector('#sales-progress').hidden")
            await snap("요청 분석과 독립 검수 진행", 2)
            await page.wait_for_function("!document.querySelector('#sales-result').hidden || !document.querySelector('#sales-draft-error').hidden", timeout=90000 if live else 25000)
            error = await page.locator("#sales-draft-error").inner_text() if not await page.locator("#sales-draft-error").is_hidden() else ""
            if error:
                raise RuntimeError("Live model did not complete: " + error)
            await snap("초안 생성과 검수 결과", 2)
            await page.locator("#sales-requests").scroll_into_view_if_needed()
            await snap("요청별 답변과 확인 항목", 2)
            await page.locator("#sales-reply").scroll_into_view_if_needed()
            await snap("영문 회신 초안 표시", 2)
            reply = await page.locator("#sales-reply").input_value()
            assert reply.strip(), "No reply in actual UI"
            assert "추가 확인" in await page.locator("#sales-review").inner_text()
            (FRAMES / "model_reply.txt").write_text(reply, encoding="utf-8")
            (FRAMES / "provenance.json").write_text(json.dumps({"live_gemini": live,
                "model": "gemini-3.5-flash" if live else "deterministic-test-double",
                "app_ui": "share_site/dist/sales.html", "backend": "app/web_api.py",
                "company_data": "fictional" if live else "none"}, ensure_ascii=False, indent=2), encoding="utf-8")
            (FRAMES / "manifest.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use local .env Gemini key for actual model calls")
    asyncio.run(record(live=parser.parse_args().live))
