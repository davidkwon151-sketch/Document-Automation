param([string]$OwnerFile)
$ErrorActionPreference='Stop'
try {
    $owner=Get-Content -LiteralPath $OwnerFile -Raw -Encoding UTF8|ConvertFrom-Json
    if($owner.process_name -ne 'Hwp' -or [int]$owner.pid -le 0){return}
    $process=Get-Process -Id ([int]$owner.pid) -ErrorAction Stop
    if($process.ProcessName -eq 'Hwp' -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$owner.start_ticks){$process.Kill()}
}catch{}
