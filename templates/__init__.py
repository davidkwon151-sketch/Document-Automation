"""원본 ZIP 부품과 서식을 유지하는 DOCX/HWPX 자리표시자 치환."""

from .fill import TemplateError, fill_template
from .compatibility import analyze_template, fill_compatible_template
from .profiles import load_form_profile

__all__ = ["TemplateError", "fill_template", "analyze_template", "fill_compatible_template", "load_form_profile"]
