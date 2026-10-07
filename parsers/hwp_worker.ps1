param([string]$InputPath,[string]$OutputPath,[string]$OwnerFile,[string]$ResultFile)
$ErrorActionPreference='Stop'
$app=$null; $owned=$false
$result=@{status='unavailable';module_registered=$false;ownership_verified=$false;reason='ownership_unverified';source_saved=$false}
try {
    $priorIds=@(Get-Process -Name 'Hwp' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $began=[DateTime]::UtcNow
    $app=New-Object -ComObject 'HWPFrame.HwpObject'
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class HwpConversionPid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId); }'
    [uint32]$ownerPid=0
    if($app.XHwpWindows.Count -gt 0){
        $window=$app.XHwpWindows.Item(0)
        [void][HwpConversionPid]::GetWindowThreadProcessId([IntPtr]$window.WindowHandle,[ref]$ownerPid)
    }
    if($ownerPid -le 0){
        $fresh=@(Get-Process -Name 'Hwp' -ErrorAction SilentlyContinue | Where-Object {$_.Id -notin $priorIds -and $_.StartTime.ToUniversalTime() -ge $began.AddSeconds(-1)})
        if($fresh.Count -ne 1){return}
        $ownerPid=[uint32]$fresh[0].Id
    }
    $process=Get-Process -Id $ownerPid -ErrorAction Stop
    if($ownerPid -le 0 -or $ownerPid -in $priorIds -or $process.ProcessName -ne 'Hwp' -or $process.StartTime.ToUniversalTime() -lt $began.AddSeconds(-1)){return}
    $created=Get-CimInstance Win32_Process -Filter ('ProcessId='+$ownerPid)
    if($created.CommandLine -notmatch '(?i)(?:^|\s)[/-](?:Embedding|Automation)(?:\s|$)'){return}
    $count=[int]$app.XHwpDocuments.Count
    if($count -gt 1){return}
    if($count -eq 1){
        $blank=$app.GetTextFile('TEXT','')
        if(-not [string]::IsNullOrWhiteSpace([string]$blank)){return}
        if($app.XHwpDocuments.Item(0).Path){return}
    }
    $owned=$true
    $result.ownership_verified=$true
    @{pid=$ownerPid;start_ticks=$process.StartTime.ToUniversalTime().Ticks;process_name='Hwp'}|ConvertTo-Json -Compress|Set-Content -LiteralPath $OwnerFile -Encoding UTF8
    if($app.XHwpWindows.Count -gt 0){$app.XHwpWindows.Item(0).Visible=$false}
    $result.reason='module_activation_failed'
    if(-not $app.RegisterModule('FilePathCheckDLL','FilePathCheckerModuleExample')){throw 'module'}
    $result.module_registered=$true
    if(-not $InputPath){$result.status='available';$result.reason='';return}
    $result.reason='conversion_failed'
    $inputFile=Get-Item -LiteralPath $InputPath -ErrorAction Stop
    if(-not ($inputFile.Attributes -band [System.IO.FileAttributes]::ReadOnly)){throw 'guard'}
    if(-not $app.Open($InputPath,'HWP','suspendpassword:true;forceopen:true;setcurdir:false')){throw 'open'}
    if(-not $app.SaveAs($OutputPath,'HWPX','')){throw 'save'}
    $result.status='converted';$result.reason=''
}catch{
    $result.status='unavailable'
}finally{
    if($null -ne $app){
        if($owned){try{[void]$app.Clear(1);[void]$app.Quit()}catch{}}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
    $result|ConvertTo-Json -Depth 4 -Compress|Set-Content -LiteralPath $ResultFile -Encoding UTF8
}
