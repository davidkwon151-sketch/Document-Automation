"""Persisted report inputs and numerical evidence."""

from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRecord(Record):
    source_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]+$")
    filename: str = Field(min_length=1)
    text: str
    location: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    sheet: str | None = None


class NumericEvidence(Record):
    value: Decimal
    unit: str = Field(min_length=1)
    as_of: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_location: str = Field(min_length=1)
    formula: str | None = None
    input_source_ids: list[str] = Field(default_factory=list)


class ReportRecord(Record):
    report_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[A-Za-z0-9_-]+$")
    instruction: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    brief: dict = Field(default_factory=dict)
    placeholders: dict[str, str] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    numbers: list[NumericEvidence] = Field(default_factory=list)
    review: dict = Field(default_factory=dict)
    status: Literal["draft", "review", "final"] = "draft"

    @model_validator(mode="after")
    def check_numeric_sources(self):
        for number in self.numbers:
            if number.source_id not in self.source_ids:
                raise ValueError("수치의 원자료 ID가 보고서 출처 목록에 없음")
            if any(source_id not in self.source_ids for source_id in number.input_source_ids):
                raise ValueError("계산 입력의 출처가 보고서 출처 목록에 없음")
            if number.formula and not number.input_source_ids:
                raise ValueError("계산 수치에는 입력 원자료 ID가 필요함")
        return self
