"""선택적으로 설치된 변환기로 구형 자료를 읽기 전용 사본에서 변환함."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
CONVERSION_TIMEOUT = 60
TARGETS = {".doc": ".docx", ".rtf": ".docx", ".odt": ".docx", ".xls": ".xlsx"}
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HWP_MESSAGE = "HWP 보안 모듈의 한글 내 로드·자동화 소유권을 확인하지 못함. 한글에서 원본을 HWPX로 다른 이름으로 저장한 뒤 첨부해 주세요."

# 파라미터로 경로를 전달함. 업로드 파일명이나 내용을 PowerShell 코드에 삽입하지 않음.
OFFICE_WORKER = r'''
param([string]$Kind, [string]$InputPath, [string]$OutputPath, [string]$OwnerPidFile)
$ErrorActionPreference = 'Stop'
$app = $null
$document = $null
try {
    $processName = if ($Kind -eq 'word') { 'WINWORD' } else { 'EXCEL' }
    $priorIds = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $progId = if ($Kind -eq 'word') { 'Word.Application' } else { 'Excel.Application' }
    $app = New-Object -ComObject $progId
    $app.Visible = $false
    if ($Kind -eq 'word') { $app.DisplayAlerts = 0 } else { $app.DisplayAlerts = $false }
    $app.AutomationSecurity = 3
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class LegacyOfficePid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId); }'
    [uint32]$ownerPid = 0
    if ($app.Hwnd) {
        [void][LegacyOfficePid]::GetWindowThreadProcessId([IntPtr]$app.Hwnd, [ref]$ownerPid)
    } else {
        $created = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $priorIds })
        if ($created.Count -eq 1) { $ownerPid = $created[0].Id }
    }
    if ($ownerPid -gt 0 -and $ownerPid -notin $priorIds) {
        [System.IO.File]::WriteAllText($OwnerPidFile, [string]$ownerPid)
    }
    if (-not $InputPath) {
        Write-Output 'available'
    } elseif ($Kind -eq 'word') {
        $confirm = $false
        $readOnly = $true
        $recent = $false
        $format = 16
        $document = $app.Documents.Open([ref]$InputPath, [ref]$confirm, [ref]$readOnly, [ref]$recent)
        $document.SaveAs2([ref]$OutputPath, [ref]$format)
        Write-Output 'converted'
    } else {
        $document = $app.Workbooks.Open($InputPath, 0, $true)
        $document.SaveAs($OutputPath, 51)
        Write-Output 'converted'
    }
} finally {
    if ($null -ne $document) {
        $saveChanges = 0
        try { if ($Kind -eq 'word') { $document.Close([ref]$saveChanges) } else { $document.Close($false) } } finally { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
    }
    if ($null -ne $app) {
        $saveChanges = 0
        try { if ($Kind -eq 'word') { $app.Quit([ref]$saveChanges) } else { $app.Quit() } } finally { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
    }
}
'''


def _run_bounded(command: list[str], timeout: int, owner_pid_file: Path | None = None) -> str:
    options = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            if owner_pid_file is not None and owner_pid_file.exists():
                owned_pid = owner_pid_file.read_text(encoding="utf-8").strip()
                if owned_pid.isdigit() and int(owned_pid) > 0:
                    subprocess.run(["taskkill", "/PID", owned_pid, "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.communicate()
        raise TimeoutError("문서 변환 제한시간을 초과함. 문서를 확인한 뒤 다시 첨부해 주세요.") from None
    if process.returncode:
        # 원자료나 인증정보가 들어 있을 수 있는 외부 도구 로그를 사용자 오류에 노출하지 않음.
        raise RuntimeError("문서 변환기가 파일을 처리하지 못함. 해당 프로그램에서 DOCX/XLSX로 저장한 뒤 첨부해 주세요.")
    return stdout


def _powershell() -> str | None:
    return (shutil.which("powershell.exe") or shutil.which("pwsh")) if os.name == "nt" else None


def _office_command(kind: str, directory: Path, source: Path | None = None, target: Path | None = None) -> tuple[list[str], Path]:
    worker = directory / "office-worker.ps1"
    worker.write_text(OFFICE_WORKER, encoding="utf-8")
    owner = directory / "office-owner.pid"
    command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(worker), "-Kind", kind, "-OwnerPidFile", str(owner)]
    if source is not None:
        command += ["-InputPath", str(source), "-OutputPath", str(target)]
    return command, owner


@lru_cache(maxsize=2)
def _office_available(kind: str) -> bool:
    if not _powershell():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="report-office-probe-") as directory:
            command, owner = _office_command(kind, Path(directory))
            return "available" in _run_bounded(command, 20, owner).splitlines()
    except (OSError, RuntimeError, TimeoutError):
        return False


def _hwp_capability():
    from .hancom import hwp_capability
    return hwp_capability()


def available_converters() -> dict:
    executable = shutil.which("soffice") or shutil.which("soffice.exe")
    if not executable and os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if base and (Path(base) / "LibreOffice/program/soffice.exe").is_file():
                executable = str(Path(base) / "LibreOffice/program/soffice.exe")
                break
    return {
        "libreoffice": {"available": bool(executable), "path": executable, "formats": list(TARGETS)},
        "word_com": {"available": _office_available("word"), "formats": [".doc", ".rtf", ".odt"]},
        "excel_com": {"available": _office_available("excel"), "formats": [".xls"]},
        "hwp": _hwp_capability(),
    }


def _validate_input(source: Path) -> str:
    if not source.is_file():
        raise ValueError("변환할 파일이 없음")
    suffix = source.suffix.lower()
    if suffix == ".hwp":
        raise ValueError(HWP_MESSAGE)
    if suffix not in TARGETS:
        raise ValueError("구형 변환 지원 형식은 DOC, XLS, ODT, RTF임")
    size = source.stat().st_size
    if not size or size > MAX_FILE_BYTES:
        raise ValueError("변환 파일은 비어 있지 않은 50 MiB 이하 파일이어야 함")
    with source.open("rb") as stream:
        header = stream.read(16)
    if suffix in {".doc", ".xls"} and not header.startswith(OLE_SIGNATURE):
        raise ValueError("구형 Office 파일의 내용과 확장자가 일치하지 않음")
    if suffix == ".rtf" and not header.lstrip().startswith(b"{\\rtf"):
        raise ValueError("RTF 파일의 내용과 확장자가 일치하지 않음")
    if suffix == ".odt":
        try:
            with zipfile.ZipFile(source) as package:
                _check_package_limits(package)
                if package.read("mimetype") != b"application/vnd.oasis.opendocument.text":
                    raise ValueError("ODT 파일 형식이 잘못됨")
        except (zipfile.BadZipFile, KeyError):
            raise ValueError("ODT 파일이 손상됨") from None
    return TARGETS[suffix]


def _check_package_limits(package: zipfile.ZipFile):
    entries = package.infolist()
    if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise ValueError("문서 패키지의 압축 해제 용량 제한을 초과함")
    if len({entry.filename for entry in entries}) != len(entries):
        raise ValueError("문서 패키지에 중복 항목이 있음")
    if any(entry.flag_bits & 1 or ".." in entry.filename.replace("\\", "/").split("/") or entry.filename.startswith(("/", "\\")) or ":" in entry.filename.split("/")[0] for entry in entries):
        raise ValueError("문서 패키지에 허용되지 않는 경로나 암호화 항목이 있음")


def _validate_output(target: Path):
    if not target.is_file() or not 0 < target.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("변환 결과 파일이 없거나 용량이 잘못됨")
    main_part = "word/document.xml" if target.suffix == ".docx" else "xl/workbook.xml"
    try:
        with zipfile.ZipFile(target) as package:
            _check_package_limits(package)
            for name in ("[Content_Types].xml", main_part):
                content = package.read(name)
                if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                    raise ValueError("변환 결과 XML에 허용되지 않는 선언이 있음")
                ElementTree.fromstring(content)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ValueError("변환 결과가 유효한 DOCX/XLSX 문서가 아님") from None


def convert_legacy(path, output_dir=None, engine=None) -> Path:
    """engine은 (원본 사본 Path, 출력 Path)를 받는 테스트용 callable 또는 변환기 이름임."""
    source = Path(path).resolve()
    if source.suffix.lower() == ".hwp":
        from .hancom import convert_hwp
        return convert_hwp(source, output_dir, engine)
    target_suffix = _validate_input(source)
    destination = Path(output_dir).resolve() if output_dir is not None else source.parent
    output = destination / (source.stem + target_suffix)
    if output.exists():
        raise FileExistsError("같은 이름의 현대 형식 파일이 이미 있음. 다른 출력 폴더를 사용해 주세요.")
    capabilities = available_converters() if not callable(engine) else None
    if not callable(engine):
        choices = ["libreoffice", "excel_com" if source.suffix.lower() == ".xls" else "word_com"]
        selected = engine or next((name for name in choices if capabilities[name]["available"]), None)
        if selected not in choices or not capabilities[selected]["available"]:
            raise RuntimeError("설치된 변환기를 찾지 못함. Word/Excel/LibreOffice에서 DOCX/XLSX로 저장한 뒤 첨부해 주세요.")
    with tempfile.TemporaryDirectory(prefix="report-legacy-") as directory:
        workspace = Path(directory)
        copied = workspace / source.name
        candidate = workspace / (source.stem + target_suffix)
        shutil.copyfile(source, copied)
        if callable(engine):
            engine(copied, candidate)
        elif selected == "libreoffice":
            profile = (workspace / "lo-profile").as_uri()
            command = [capabilities[selected]["path"], f"-env:UserInstallation={profile}", "--headless", "--nologo", "--nodefault", "--nofirststartwizard", "--convert-to", target_suffix[1:], "--outdir", str(workspace), str(copied)]
            _run_bounded(command, CONVERSION_TIMEOUT)
        else:
            command, owner = _office_command("excel" if selected == "excel_com" else "word", workspace, copied, candidate)
            _run_bounded(command, CONVERSION_TIMEOUT, owner)
        _validate_output(candidate)
        destination.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream, candidate.open("rb") as converted:
            shutil.copyfileobj(converted, stream)
    return output
