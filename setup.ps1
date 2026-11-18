$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
# Pause only this project's old task while replacing runtime files/dependencies.
$existingTask=Get-ScheduledTask -TaskName 'ArabiaTrend-Tech-Agent' -ErrorAction SilentlyContinue
if($existingTask) {
    $existingTask | Disable-ScheduledTask | Out-Null
    if($existingTask.State -eq 'Running') {
        Stop-ScheduledTask -TaskName 'ArabiaTrend-Tech-Agent'
    }
}
Write-Host 'ArabiaTrend Agent v2 - one-time setup'
Write-Host 'Uses Groq Free, Gemini API Free, and Cloudflare Workers AI Free only.'
Write-Host 'Do not proceed with billed/developer/paid plans. Local budgets are NOT a provider billing lock.'
Write-Host 'This installs automatic daily execution once per day at 09:00 to schedule 10:00 and 20:00 Africa/Cairo posts (category 101).'
$answer=Read-Host 'Confirm all three API accounts/projects have no paid billing enabled. Type FREE'
if($answer -cne 'FREE') { throw 'Stopped. Check provider plans before setup.' }
$dir=Join-Path $env:LOCALAPPDATA 'ArabiaTrendAgent'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$wpFile=Join-Path $dir 'wordpress-credential.xml'
if(!(Test-Path -LiteralPath $wpFile)) {
    $password=Read-Host 'WordPress Application Password for arabia_trend_agent' -AsSecureString
    [PSCredential]::new('arabia_trend_agent',$password) | Export-Clixml -LiteralPath $wpFile
}
$keyFile=Join-Path $dir 'api-secrets-v2.xml'
if(Test-Path -LiteralPath $keyFile) { $keys=Import-Clixml -LiteralPath $keyFile } else { $keys=@{} }
foreach($entry in @(
    @('GROQ_API_KEY','Groq API key'),
    @('GEMINI_API_KEY','Google AI Studio Gemini API key'),
    @('CF_ACCOUNT_ID','Cloudflare Account ID'),
    @('CF_API_TOKEN','Cloudflare token with Workers AI Read/Edit for this account')
)) {
    if(!$keys.ContainsKey($entry[0])) {
        $secret=Read-Host $entry[1] -AsSecureString
        if($secret.Length -eq 0) { throw 'Empty secret. Run setup again to finish.' }
        $keys[$entry[0]]=$secret
        $keys | Export-Clixml -LiteralPath $keyFile
    }
}
@{free_only=$true;checked_at=[DateTime]::UtcNow.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'free-tier-confirmed.json') -Encoding UTF8
if(!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & python -m venv .venv
    if($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements-v2.txt
if($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if($LASTEXITCODE -ne 0) { throw 'Tests failed. Task not activated.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\run-v2.ps1" check
if($LASTEXITCODE -ne 0) { throw 'Connection check failed. Task not activated. Details above.' }
# Register task to run ONCE per day at 09:00 AM (instead of every 15 minutes)
$task='ArabiaTrend-Tech-Agent'
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSScriptRoot\run-v2.ps1`" run" -WorkingDirectory $PSScriptRoot
$trigger=New-ScheduledTaskTrigger -Daily -At '09:00'
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host 'READY: task installed to run ONCE per day at 09:00 AM (schedules both 10:00 AM and 8:00 PM posts).'
Write-Host 'Status: open STATUS.cmd. Pause: open PAUSE.cmd. Resume: open RESUME.cmd.'
