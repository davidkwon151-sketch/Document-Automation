"""Capture three fictional buyer inquiries in the actual sales workspace."""

from __future__ import annotations

import asyncio
import json
import re

from docs.demo_sales.record_ui import BUYER, ROOT, record


CASES = {
    "en": BUYER,
    "es": ("Hola, equipo de ventas:\n\n"
           "Solicitamos una cotización de 500 sillas de oficina estándar para entregar en Dallas. "
           "Indiquen el precio unitario, el plazo estimado y las condiciones de entrega.\n\n"
           "Gracias.\nComprador de demostración"),
    "he": ("שלום לצוות המכירות,\n\n"
           "נשמח לקבל הצעת מחיר עבור 500 כיסאות משרדיים סטנדרטיים למשלוח לדאלאס. "
           "אנא ציינו מחיר ליחידה, זמן אספקה משוער ותנאי משלוח.\n\n"
           "תודה,\nקונה לדוגמה"),
}


def check_reply(language: str, reply: str) -> None:
    if not reply.strip():
        raise ValueError(f"{language}: empty model reply")
    if language == "es" and not re.search(r"\b(gracias|precio|entrega|cotizaci[oó]n|saludos)\b", reply, re.I):
        raise ValueError("Spanish reply did not contain expected Spanish business language")
    if language == "he" and len(re.findall(r"[\u0590-\u05ff]", reply)) < 25:
        raise ValueError("Hebrew reply did not contain enough Hebrew text")


async def main() -> None:
    for language in CASES:
        frames = ROOT / ".runtime" / "demo_sales_multilang" / language
        await record(live=True, buyer=CASES[language], language=language, frames=frames)
        provenance = json.loads((frames / "provenance.json").read_text(encoding="utf-8"))
        if (provenance.get("live_gemini") is not True or provenance.get("language") != language
                or provenance.get("model") != "gemini-3.6-flash"):
            raise ValueError(f"{language}: missing live capture provenance")
        reply = (frames / "model_reply.txt").read_text(encoding="utf-8")
        check_reply(language, reply)
        print(f"{language}: actual model reply captured ({len(reply)} characters)")


if __name__ == "__main__":
    asyncio.run(main())
