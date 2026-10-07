"""원본을 저장하지 않는 선택적 네이티브 PDF 미리보기. 내용은 도구 로그에 남기지 않음."""

from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from .legacy import MAX_FILE_BYTES, _check_package_limits, _powershell

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
FORMATS = {".docx": ("word", "word/document.xml"), ".xlsx": ("excel", "xl/workbook.xml"), ".pptx": ("powerpoint", "ppt/presentation.xml")}
MAIN_TYPES = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"}


class NativeRenderingUnavailable(ValueError):
    """원본을 우회하지 않아 네이티브 미리보기만 보류해야 하는 경우임."""

# 고정 코드와 경로 인자를 분리하고 소유권 확인 전 앱 설정을 변경하지 않음.
OFFICE_WORKER = r'''
param([string]$Kind,[string]$InputPath,[string]$OutputPath,[string]$OwnerFile,[string]$ResultFile,[string]$RequestsFile)
$ErrorActionPreference='Stop'
$app=$null; $document=$null; $owned=$false
$result=@{status='unavailable'; formula_recalculated=$false; cell_evidence=@{}}
try {
    $processName=@{word='WINWORD';excel='EXCEL';powerpoint='POWERPNT'}[$Kind]
    $priorIds=@(Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $began=[DateTime]::UtcNow
    $progId=@{word='Word.Application';excel='Excel.Application';powerpoint='PowerPoint.Application'}[$Kind]
    $app=New-Object -ComObject $progId
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class NativeOfficePid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId); }'
    [uint32]$ownerPid=0
    # Word.Application has no Hwnd before a document window exists. A fresh
    # automation process must be unique; ambiguous ownership stays unavailable.
    if($Kind -ne 'word'){
        [void][NativeOfficePid]::GetWindowThreadProcessId([IntPtr]$app.Hwnd,[ref]$ownerPid)
    }else{
        $fresh=@(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object {$_.Id -notin $priorIds -and $_.StartTime.ToUniversalTime() -ge $began.AddSeconds(-1)})
        if($fresh.Count -ne 1){return}
        $ownerPid=[uint32]$fresh[0].Id
    }
    $process=Get-Process -Id $ownerPid -ErrorAction Stop
    if ($ownerPid -le 0 -or $ownerPid -in $priorIds -or $process.ProcessName -ne $processName -or $process.StartTime.ToUniversalTime() -lt $began.AddSeconds(-1)) { return }
    $created=Get-CimInstance Win32_Process -Filter ("ProcessId="+$ownerPid)
    if($created.CommandLine -notmatch '(?i)(?:^|\s)[/-](?:Embedding|Automation)(?:\s|$)'){return}
    $count=if($Kind -eq 'word'){$app.Documents.Count}elseif($Kind -eq 'excel'){$app.Workbooks.Count}else{$app.Presentations.Count}
    if($count -ne 0){return}
    $owned=$true
    @{pid=$ownerPid;start_ticks=$process.StartTime.ToUniversalTime().Ticks;process_name=$processName}|ConvertTo-Json -Compress|Set-Content -LiteralPath $OwnerFile -Encoding UTF8
    $app.AutomationSecurity=3
    if($Kind -ne 'powerpoint'){$app.Visible=$false}
    if($Kind -eq 'word'){
        $app.DisplayAlerts=0; $app.Options.UpdateLinksAtOpen=$false
        $confirm=$false; $readOnly=$true; $recent=$false
        $document=$app.Documents.Open([ref]$InputPath,[ref]$confirm,[ref]$readOnly,[ref]$recent)
        if(-not $document.ReadOnly -or $document.Permission.Enabled){throw 'guard'}
        $document.ExportAsFixedFormat($OutputPath,17,$false)
    }elseif($Kind -eq 'excel'){
        $app.DisplayAlerts=$false; $app.EnableEvents=$false; $app.AskToUpdateLinks=$false
        $document=$app.Workbooks.Open($InputPath,0,$true)
        if(-not $document.ReadOnly -or $document.Permission.Enabled -or $document.Connections.Count -gt 0){throw 'guard'}
        $app.CalculateFullRebuild()
        while([int]$app.CalculationState -eq 1){Start-Sleep -Milliseconds 100}
        $result.calculation_state=[int]$app.CalculationState
        $result.calculation_requested=$true
        $result.formula_recalculated=($result.calculation_state -eq 0)
        $requests=Get-Content -LiteralPath $RequestsFile -Raw -Encoding UTF8|ConvertFrom-Json
        foreach($request in $requests){
            $sheet=$document.Worksheets.Item($request.sheet_name)
            $cell=$sheet.Range($request.coordinate)
            $area=[string]$sheet.PageSetup.PrintArea
            $inside=$null
            if($area){
                $inside=$false
                $region=$cell.MergeArea
                foreach($section in $sheet.Range($area).Areas){
                    if($region.Row -ge $section.Row -and $region.Column -ge $section.Column -and
                       ($region.Row+$region.Rows.Count) -le ($section.Row+$section.Rows.Count) -and
                       ($region.Column+$region.Columns.Count) -le ($section.Column+$section.Columns.Count)){$inside=$true;break}
                }
            }
            $result.cell_evidence[$request.id]=@{id=$request.id;part=$request.part;coordinate=$request.coordinate;sheet_name=$request.sheet_name;text=[string]$cell.Text;value2=$cell.Value2;number_format=[string]$cell.NumberFormat;row_hidden=[bool]$cell.EntireRow.Hidden;column_hidden=[bool]$cell.EntireColumn.Hidden;sheet_hidden=([int]$sheet.Visible -ne -1);print_area=$area;within_print_area=$inside;has_formula=[bool]$cell.HasFormula;formula=[string]$cell.Formula;merged_range=[string]$cell.MergeArea.Address()}
        }
        $document.ExportAsFixedFormat(0,$OutputPath)
    }else{
        $app.DisplayAlerts=1
        $document=$app.Presentations.Open($InputPath,-1,0,0)
        if($document.ReadOnly -ne -1 -or $document.Permission.Enabled){throw 'guard'}
        $document.SaveAs($OutputPath,32)
    }
    $result.status='rendered'
}catch{
    # 예외 원문에는 파일 내용이나 개인 경로가 있을 수 있어 출력하지 않음.
    $result.status=if($_.Exception.Message -eq 'guard'){'blocked'}else{'unavailable'}
}finally{
    if($owned -and $null -ne $document){
        try{if($Kind -eq 'word'){$save=0;$document.Close([ref]$save)}elseif($Kind -eq 'excel'){$document.Close($false)}else{$document.Close()}}catch{}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document)
    }
    if($null -ne $app){
        if($owned){try{if($Kind -eq 'word'){$save=0;$app.Quit([ref]$save)}else{$app.Quit()}}catch{}}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
    $result|ConvertTo-Json -Depth 6 -Compress|Set-Content -LiteralPath $ResultFile -Encoding UTF8
}
'''

CLEANUP_WORKER = r'''
param([string]$OwnerFile)
$ErrorActionPreference='Stop'
try {
    $owner=Get-Content -LiteralPath $OwnerFile -Raw -Encoding UTF8|ConvertFrom-Json
    if($owner.process_name -notin @('WINWORD','EXCEL','POWERPNT') -or [int]$owner.pid -le 0){return}
    $process=Get-Process -Id ([int]$owner.pid) -ErrorAction Stop
    if($process.ProcessName -eq $owner.process_name -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$owner.start_ticks){$process.Kill()}
}catch{}
'''


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _xml(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("허용되지 않는 XML 선언이 있음")
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("문서 XML이 손상됨") from None


def _pdf_pages(path, *, input_document=False):
    blocked = NativeRenderingUnavailable if input_document else ValueError
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise blocked("암호·권한 보호 PDF는 자동 렌더링하지 않음")
        root = reader.trailer["/Root"]
        if root.get("/Perms") or any(field.get("/FT") == "/Sig" and field.get("/V") for field in (reader.get_fields() or {}).values()):
            raise blocked("서명된 PDF는 자동 렌더링하지 않음")
        names = root.get("/Names") or {}
        if root.get("/OpenAction") or root.get("/AA") or root.get("/AF") or "/JavaScript" in names or "/EmbeddedFiles" in names:
            raise blocked("실행 동작이 있는 PDF는 자동 렌더링하지 않음")
        pages = list(reader.pages)
        if not pages:
            raise ValueError("PDF에 페이지가 없음")
        for page in pages:
            if page.get("/AA"):
                raise blocked("실행 동작이 있는 PDF는 자동 렌더링하지 않음")
            for annotation in page.get("/Annots", []):
                obj = annotation.get_object()
                action = obj.get("/A") or {}
                parent = obj.get("/Parent")
                parent = parent.get_object() if parent else {}
                if (obj.get("/FT", parent.get("/FT")) == "/Sig" and obj.get("/V", parent.get("/V"))) or obj.get("/Subtype") == "/FileAttachment":
                    raise blocked("서명·첨부 파일이 있는 PDF는 자동 렌더링하지 않음")
                if obj.get("/AA") or action.get("/S") in {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData"}:
                    raise blocked("실행 동작이 있는 PDF는 자동 렌더링하지 않음")
            if not all(math.isfinite(float(value)) for value in page.mediabox) or page.mediabox.width <= 0 or page.mediabox.height <= 0:
                raise ValueError("PDF 페이지 크기가 잘못됨")
        return len(pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError("유효한 PDF를 확인할 수 없음") from None


def _package(path):
    with path.open("rb") as stream:
        header = stream.read(32)
    if header.startswith((b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"NASCA DRM FILE")):
        raise NativeRenderingUnavailable("암호·DRM 보호 Office 원본은 네이티브 렌더링하지 않음")
    try:
        with zipfile.ZipFile(path) as package:
            if any(entry.flag_bits & 1 for entry in package.infolist()):
                raise NativeRenderingUnavailable("암호화된 문서 패키지는 렌더링하지 않음")
            _check_package_limits(package)
            if package.testzip() is not None:
                raise ValueError("문서 패키지의 무결성이 잘못됨")
            parts = {entry.filename: package.read(entry) for entry in package.infolist() if not entry.is_dir()}
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise ValueError("현대 Office 파일이 손상되었거나 암호·DRM 보호됨") from None
    required = FORMATS[path.suffix.lower()][1]
    if required not in parts or "[Content_Types].xml" not in parts or "_rels/.rels" not in parts:
        raise ValueError("현대 Office 파일의 내용과 확장자가 일치하지 않음")
    content_types = _xml(parts["[Content_Types].xml"])
    declarations = [item.get("ContentType") for item in content_types if item.get("PartName") == "/" + required]
    if declarations != [MAIN_TYPES[path.suffix.lower()]] or any("macroenabled" in item.get("ContentType", "").lower() for item in content_types):
        raise ValueError("주 문서 부품의 형식·매크로 선언이 잘못됨")
    expected_roots = {".docx": "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document", ".xlsx": "{" + S + "}workbook", ".pptx": "{http://schemas.openxmlformats.org/presentationml/2006/main}presentation"}
    if _xml(parts[required]).tag != expected_roots[path.suffix.lower()]:
        raise ValueError("주 문서 XML 형식이 잘못됨")
    primary = [item for item in _xml(parts["_rels/.rels"]) if item.get("Type", "").endswith("/officeDocument")]
    if len(primary) != 1 or primary[0].get("Target", "").lstrip("/") != required or primary[0].get("TargetMode", "").lower() == "external":
        raise ValueError("주 문서 연결이 잘못됨")
    for name, content in parts.items():
        lower = name.lower()
        if lower.startswith("_xmlsignatures/") or any(token in lower for token in ("vbaproject", "macrosheets/", "activex/", "embeddings/", "externallinks/", "connections.xml", "querytables/", "webextensions/", "customui/")):
            raise NativeRenderingUnavailable("서명·매크로·외부 연결·실행 가능한 부품은 렌더링하지 않음")
        if not lower.endswith((".xml", ".rels")):
            continue
        node = _xml(content)
        if lower.endswith(".rels"):
            identifiers = [rel.get("Id") for rel in node]
            if node.tag != "{" + P + "}Relationships" or any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
                raise ValueError("문서 관계의 형식·식별자가 잘못됨")
            for rel in node:
                if rel.get("TargetMode", "").lower() == "external" and not rel.get("Type", "").endswith("/hyperlink"):
                    raise NativeRenderingUnavailable("외부 자산·연결을 가진 문서는 렌더링하지 않음")
                if rel.get("TargetMode", "").lower() != "external":
                    target = rel.get("Target", "")
                    owner_dir = "" if name == "_rels/.rels" else posixpath.dirname(name.replace("/_rels/", "/").removesuffix(".rels"))
                    resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(owner_dir, target))
                    if not target or "\\" in target or ":" in target or resolved.startswith("../") or resolved not in parts:
                        raise ValueError("문서 내부 연결이 없거나 패키지 밖을 가리킴")
        for element in node.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            attributes = {key.rsplit("}", 1)[-1]: value for key, value in element.attrib.items()}
            if tag == "documentProtection" and attributes.get("enforcement") in {"1", "true", "on"}:
                raise NativeRenderingUnavailable("보호된 Word 문서는 렌더링하지 않음")
            if tag in {"fileSharing", "modifyVerifier", "encryptedPackage"} and attributes:
                raise NativeRenderingUnavailable("암호·쓰기 보호 문서는 렌더링하지 않음")
            if tag == "workbookProtection" and (any(attributes.get(key) in {"1", "true", "on"} for key in ("lockStructure", "lockWindows", "lockRevision")) or any("password" in key.lower() or "hash" in key.lower() for key in attributes)):
                raise NativeRenderingUnavailable("보호된 통합문서는 렌더링하지 않음")
            if tag == "sheetProtection" and attributes.get("sheet", "1") not in {"0", "false", "off"}:
                raise NativeRenderingUnavailable("보호된 시트는 렌더링하지 않음")
            if tag in {"f", "instrText", "fldSimple"} and ("|" in (element.text or "") or re.search(r"\b(?:DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|LINK|WEBSERVICE|RTD)\b", (element.text or "") + " " + attributes.get("instr", ""), re.I)):
                raise NativeRenderingUnavailable("외부 데이터·실행 필드는 렌더링하지 않음")
    return parts


def _requests(requests, parts, suffix):
    if requests is None or requests == []:
        return []
    if suffix != ".xlsx" or not isinstance(requests, list) or len(requests) > 2000:
        raise ValueError("셀 표시 요청은 XLSX의 2,000개 이하 목록이어야 함")
    rels = {item.get("Id"): item.get("Target", "") for item in _xml(parts["xl/_rels/workbook.xml.rels"])}
    sheets = {}
    for sheet in _xml(parts["xl/workbook.xml"]).findall(f"{{{S}}}sheets/{{{S}}}sheet"):
        target = rels.get(sheet.get(f"{{{R}}}id"), "")
        part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if part.startswith("xl/worksheets/") and part in parts:
            sheets[part] = sheet.get("name")
    result = []
    ids = set()
    for request in requests:
        if not isinstance(request, dict) or set(request) != {"id", "part", "coordinate"}:
            raise ValueError("셀 표시 요청의 필드가 잘못됨")
        identifier, part, coordinate = (request[key] for key in ("id", "part", "coordinate"))
        if not isinstance(identifier, str) or not identifier or identifier in ids or part not in sheets or not isinstance(coordinate, str):
            raise ValueError("셀 표시 요청의 ID·시트가 잘못됨")
        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", coordinate)
        column = 0
        for letter in match[1] if match else "":
            column = column * 26 + ord(letter) - 64
        if not match or column > 16384 or int(match[2]) > 1048576:
            raise ValueError("셀 표시 요청의 주소가 잘못됨")
        result.append({**request, "sheet_name": sheets[part]})
        ids.add(identifier)
    return result


def _run_worker(command, timeout, owner, directory):
    options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(command, **options)
    try:
        process.wait(timeout=timeout)
        if process.returncode:
            raise RuntimeError("네이티브 렌더러를 실행하지 못함")
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise TimeoutError("네이티브 렌더링 제한시간을 초과함") from None
    finally:
        if owner.is_file():
            cleanup = directory / "cleanup.ps1"
            cleanup.write_text(CLEANUP_WORKER, encoding="utf-8-sig")
            subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-File", str(cleanup), "-OwnerFile", str(owner)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _office(source, target, requests, directory, kind, timeout):
    executable = _powershell()
    if not executable:
        return {"status": "unavailable"}
    worker, owner, result, requested = (directory / name for name in ("native.ps1", "owner.json", "result.json", "requests.json"))
    worker.write_text(OFFICE_WORKER, encoding="utf-8-sig")
    requested.write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
    command = [executable, "-NoProfile", "-NonInteractive", "-File", str(worker), "-Kind", kind, "-InputPath", str(source), "-OutputPath", str(target), "-OwnerFile", str(owner), "-ResultFile", str(result), "-RequestsFile", str(requested)]
    _run_worker(command, timeout, owner, directory)
    if not result.is_file():
        return {"status": "unavailable"}
    try:
        return json.loads(result.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        raise ValueError("네이티브 렌더러의 확인 기록이 손상됨") from None


def render_native_pdf(path, output_path, *, timeout=60, engine=None, cell_requests=None):
    """engine callable은 (읽기용 사본, 임시 PDF, *, cell_requests)→dict 계약임."""
    source, output = Path(path).resolve(), Path(output_path).resolve()
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("렌더링할 파일이 없거나 용량이 잘못됨")
    if output == source or output.exists() and os.path.samefile(source, output):
        raise ValueError("원본을 출력으로 덮어쓸 수 없음")
    if output.suffix.lower() != ".pdf" or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("출력 형식·제한시간이 잘못됨")
    suffix, digest = source.suffix.lower(), _hash(source)
    metadata = {"status": "unavailable", "engine": None, "source_sha256": digest, "source_unchanged": True, "formula_recalculated": False}
    if suffix not in {*FORMATS, ".pdf"}:
        metadata["reason"] = "이 형식의 보안 우회 없는 네이티브 렌더러가 준비되지 않음"
        return metadata
    parts = _package(source) if suffix in FORMATS else {}
    requests = _requests(cell_requests, parts, suffix)
    if suffix == ".pdf":
        _pdf_pages(source, input_document=True)
        selected = "pdf_copy"
    elif callable(engine):
        selected = "callable"
    else:
        selected = engine or FORMATS[suffix][0] + "_com"
        if selected != FORMATS[suffix][0] + "_com" or not _powershell():
            metadata.update(engine=selected, reason="요청한 네이티브 렌더링 엔진을 사용할 수 없음")
            return metadata
    metadata["engine"] = selected
    with tempfile.TemporaryDirectory(prefix="report-native-") as name:
        directory = Path(name)
        copied, candidate = directory / ("input" + suffix), directory / "rendered.pdf"
        shutil.copyfile(source, copied)
        if _hash(copied) != digest or _hash(source) != digest:
            raise ValueError("렌더러 실행 전에 원본 또는 사본이 변경됨")
        try:
            if selected == "pdf_copy":
                shutil.copyfile(copied, candidate)
                result = {"status": "rendered"}
            elif callable(engine):
                result = engine(copied, candidate, cell_requests=requests) or {"status": "rendered"}
            else:
                result = _office(copied, candidate, requests, directory, FORMATS[suffix][0], timeout)
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
            result = {"status": "unavailable"}
        finally:
            if _hash(source) != digest or _hash(copied) != digest:
                raise ValueError("렌더링 중 원본 또는 읽기용 사본이 변경됨")
        if isinstance(result, dict) and result.get("status") == "blocked":
            raise NativeRenderingUnavailable("네이티브 앱에서 보호·외부 연결이 확인되어 렌더링을 차단함")
        if not isinstance(result, dict) or result.get("status") not in {"rendered", "unavailable"}:
            raise ValueError("네이티브 렌더러 상태가 잘못됨")
        if result["status"] != "rendered":
            metadata["reason"] = "엔진 실행·소유권·렌더링 완료를 확인할 수 없음"
            return metadata
        if not candidate.is_file() or not 0 < candidate.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError("렌더링 PDF가 없거나 용량이 잘못됨")
        page_count = _pdf_pages(candidate)
        recalculated = result.get("formula_recalculated", False)
        if not isinstance(recalculated, bool) or suffix != ".xlsx" and recalculated:
            raise ValueError("재계산 확인 기록이 잘못됨")
        calculation_state = result.get('calculation_state')
        if calculation_state is not None and (suffix != '.xlsx' or type(calculation_state) is not int
                or calculation_state not in {0, 1, 2} or recalculated != (calculation_state == 0)):
            raise ValueError('재계산 완료와 계산 상태 확인 기록이 다름')
        evidence = result.get("cell_evidence", {})
        if not isinstance(evidence, dict) or set(evidence) != {request["id"] for request in requests}:
            raise ValueError("요청한 셀 표시 확인 기록이 누락됨")
        for request in requests:
            entry = evidence[request["id"]]
            if not isinstance(entry, dict) or any(entry.get(key) != request[key] for key in ("id", "part", "coordinate", "sheet_name")) or any(not isinstance(entry.get(key), str) for key in ("text", "number_format", "print_area", "formula", "merged_range")):
                raise ValueError("셀 표시 확인 기록의 위치가 잘못됨")
            if any(not isinstance(entry.get(key), bool) for key in ("row_hidden", "column_hidden", "sheet_hidden", "has_formula")) or entry.get("within_print_area") is not None and not isinstance(entry.get("within_print_area"), bool):
                raise ValueError("셀 표시·숨김·인쇄 확인 기록이 잘못됨")
            if "value2" not in entry or not isinstance(entry["value2"], (str, int, float, bool, type(None))) or isinstance(entry["value2"], float) and not math.isfinite(entry["value2"]):
                raise ValueError("셀의 계산 값 확인 기록이 잘못됨")
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, staged = tempfile.mkstemp(prefix=".native-", suffix=".pdf", dir=output.parent)
        os.close(handle)
        try:
            shutil.copyfile(candidate, staged)
            if _hash(source) != digest:
                raise ValueError("PDF 저장 전 원본이 변경됨")
            os.replace(staged, output)
        finally:
            Path(staged).unlink(missing_ok=True)
        metadata.update(status="rendered", formula_recalculated=recalculated, page_count=page_count, output_path=str(output), output_sha256=_hash(output))
        if calculation_state is not None:
            metadata['calculation_state'] = calculation_state
        if requests:
            metadata["cell_evidence"] = evidence
        return metadata
