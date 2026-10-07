"""FastAPI entry point for the report agent."""

from fastapi import FastAPI, HTTPException

from app.schemas import ReportRecord, SourceRecord
from app.storage import LocalStore


def create_app(store: LocalStore | None = None) -> FastAPI:
    application = FastAPI(title="문서 표준화 AI AGENT", version="0.1.0")
    application.state.store = store or LocalStore()

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/sources", response_model=SourceRecord, status_code=201)
    def save_source(source: SourceRecord):
        return application.state.store.save_source(source)

    @application.get("/sources/{source_id}", response_model=SourceRecord)
    def get_source(source_id: str):
        try:
            return application.state.store.load_source(source_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "원자료를 찾을 수 없음") from exc

    @application.post("/reports", response_model=ReportRecord, status_code=201)
    def save_report(report: ReportRecord):
        try:
            return application.state.store.save_report(report)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/reports/{report_id}", response_model=ReportRecord)
    def get_report(report_id: str):
        try:
            return application.state.store.load_report(report_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "보고서를 찾을 수 없음") from exc

    return application


app = create_app()
