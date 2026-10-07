"""Small local JSON store; caller-controlled IDs never become directory paths."""

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from app.schemas import ReportRecord, SourceRecord


class LocalStore:
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)

    def _path(self, kind: str, record_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", record_id):
            raise ValueError("잘못된 저장 ID임")
        return self.root / kind / f"{record_id}.json"

    def _save(self, kind: str, record: SourceRecord | ReportRecord, record_id: str):
        path = self._path(kind, record_id)
        self._write(path, record.model_dump_json(indent=2))
        return record

    @staticmethod
    def _write(path: Path, contents: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(contents)
            temp_path.replace(path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def save_source(self, source: SourceRecord) -> SourceRecord:
        return self._save("sources", source, source.source_id)

    def load_source(self, source_id: str) -> SourceRecord:
        return SourceRecord.model_validate_json(self._path("sources", source_id).read_text(encoding="utf-8"))

    def save_report(self, report: ReportRecord) -> ReportRecord:
        report = ReportRecord.model_validate(report.model_dump())
        for source_id in report.source_ids:
            try:
                self.load_source(source_id)
            except FileNotFoundError as exc:
                raise ValueError(f"원자료 {source_id}가 저장되지 않음") from exc
        for number in report.numbers:
            if self.load_source(number.source_id).location != number.source_location:
                raise ValueError("수치의 출처 위치가 원자료 위치와 다름")
        return self._save("reports", report, report.report_id)

    def load_report(self, report_id: str) -> ReportRecord:
        return ReportRecord.model_validate_json(self._path("reports", report_id).read_text(encoding="utf-8"))


def save_record(record: dict, directory: str | Path = "data") -> str:
    """Store a complete pipeline run, including original paths and source links."""
    if not isinstance(record, dict):
        raise ValueError("파이프라인 저장 내용은 dict여야 함")
    record_id = uuid4().hex
    store = LocalStore(directory)
    # Preview PDFs are transient UI data; persist their verification hashes only.
    saved = {key: value for key, value in record.items() if key != '_native_preview_bytes'}
    store._write(store._path("records", record_id), json.dumps(saved, ensure_ascii=False, indent=2))
    # File timestamps can tie; UUID order does not represent save order.
    store._write(store.root / 'latest_record_id', record_id)
    return record_id


def load_record(record_id: str, directory: str | Path = "data") -> dict:
    record = json.loads(LocalStore(directory)._path("records", record_id).read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("저장된 파이프라인 내용이 dict가 아님")
    return record


def latest_runs(directory: str | Path = 'data') -> list[dict]:
    """One latest snapshot per logical run; keep reopened reports out of final KPIs."""
    runs = []
    for folder in (Path(directory) / 'runs').glob('*'):
        if not folder.is_dir():
            continue
        try:
            pointer = folder / 'latest_record_id'
            if pointer.is_file():
                snapshot = LocalStore(folder)._path('records', pointer.read_text(encoding='utf-8').strip())
            else:
                # Existing stores without a pointer retain their previous lookup.
                snapshots = sorted((folder / 'records').glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
                if not snapshots:
                    continue
                snapshot = snapshots[0]
            latest = json.loads(snapshot.read_text(encoding='utf-8'))
            if isinstance(latest, dict) and latest.get('run_id') == folder.name and latest.get('metrics'):
                runs.append(latest)
        except (OSError, ValueError):
            continue
    return runs
