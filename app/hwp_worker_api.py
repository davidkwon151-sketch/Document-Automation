"""Protected, loopback-only bridge to the existing owned Hancom converter."""
import base64
import binascii
import hashlib
import hmac
import json
import ntpath
import os
from pathlib import Path
import tempfile
import threading

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from parsers import hancom

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 2048
# ponytail: one Hancom conversion at a time; add isolated Windows workers for throughput.
NATIVE_LOCK = threading.Lock()


def create_app(token=None):
    secret = token if token is not None else os.getenv("RA_HWP_WORKER_TOKEN", "")
    valid_secret = isinstance(secret, str) and len(secret) >= 32 and secret.isascii()
    app = FastAPI(title="Windows HWP conversion worker", docs_url=None,
                  redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def protect(request, call_next):
        if not valid_secret:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "변환 서버 인증 설정이 필요함"}, status_code=503)
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "인증이 필요함"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def health():
        if not NATIVE_LOCK.acquire(blocking=False):
            return {"status": "busy", "available": False}
        try:
            capability = hancom.hwp_capability()
            # Keep native paths, process identity and registration details server-side.
            return {"status": "ready", "available": bool(capability.get("available")),
                    "module_active": bool(capability.get("module_active")),
                    "ownership_verified": bool(capability.get("ownership_verified"))}
        except Exception:
            return {"status": "unavailable", "available": False}
        finally:
            NATIVE_LOCK.release()

    @app.get("/health")
    async def get_health():
        return await run_in_threadpool(health)

    def convert(name, content):
        if not NATIVE_LOCK.acquire(blocking=False):
            raise HTTPException(429, "한글 변환이 진행 중임. 잠시 뒤 다시 요청해 주세요.")
        try:
            with tempfile.TemporaryDirectory(prefix="ra-hwp-http-") as folder:
                source = Path(folder) / "upload.hwp"
                source.write_bytes(content)
                source_sha = hashlib.sha256(content).hexdigest()
                output = hancom.convert_hwp(source, Path(folder) / "output")
                if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
                    raise ValueError("source changed")
                if output.resolve().parent != (Path(folder) / "output").resolve():
                    raise ValueError("unexpected output")
                hancom._validate_hwpx(output)
                converted = output.read_bytes()
                return {"name": name[:-4] + ".hwpx",
                        "base64": base64.b64encode(converted).decode("ascii"),
                        "source_sha256": source_sha,
                        "output_sha256": hashlib.sha256(converted).hexdigest()}
        except HTTPException:
            raise
        except TimeoutError:
            raise HTTPException(504, "한글 변환 제한시간을 초과함") from None
        except Exception:
            raise HTTPException(422, "HWP 변환을 확인하지 못함. 암호·보호 여부와 한글 보안 모듈을 확인하거나 HWPX로 저장해 주세요.") from None
        finally:
            NATIVE_LOCK.release()

    @app.post("/convert")
    async def post_convert(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, "HWP 업로드는 10 MiB 이하만 지원함")
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or set(data) != {"name", "base64"}:
                raise ValueError()
            name = data["name"]
            if (not isinstance(name, str) or not 1 <= len(name) <= 180
                    or ntpath.basename(name) != name or any(c in name for c in '/\\:')
                    or any(ord(c) < 32 for c in name) or not name.lower().endswith(".hwp")
                    or name.rstrip() != name):
                raise ValueError()
            if not isinstance(data["base64"], str):
                raise ValueError()
            content = base64.b64decode(data["base64"], validate=True)
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise HTTPException(400, "HWP 파일 이름과 base64 내용을 확인해 주세요.") from None
        if not 0 < len(content) <= MAX_UPLOAD_BYTES:
            raise HTTPException(413, "HWP 업로드는 비어 있지 않은 10 MiB 이하만 지원함")
        return await run_in_threadpool(convert, name, content)

    return app


app = create_app()
