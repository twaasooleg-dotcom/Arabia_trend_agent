param([ValidateSet('check','topics','run','audit','status')][string]$Command = 'check')
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$secretsDir=Join-Path $env:LOCALAPPDATA 'ArabiaTrendAgent'
$wp=Import-Clixml -LiteralPath (Join-Path $secretsDir 'wordpress-credential.xml')
$keys=Import-Clixml -LiteralPath (Join-Path $secretsDir 'api-secrets-v2.xml')
$env:WP_BASE_URL='https://arabiatrend.com'
$env:WP_USERNAME=$wp.UserName
$env:WP_APP_PASSWORD=$wp.GetNetworkCredential().Password
foreach($name in $keys.Keys) {
    $value=[System.Net.NetworkCredential]::new('', $keys[$name]).Password
    [Environment]::SetEnvironmentVariable($name,$value,'Process')
}
$env:PYTHONUTF8='1'
$OutputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
try {
    & "$PSScriptRoot\.venv\Scripts\python.exe" -X utf8 "$PSScriptRoot\agent_v2.py" $Command
    $result=$LASTEXITCODE
} finally {
    foreach($name in @('WP_APP_PASSWORD','GROQ_API_KEY','GEMINI_API_KEY','CF_API_TOKEN','CF_ACCOUNT_ID')) {
        [Environment]::SetEnvironmentVariable($name,$null,'Process')
    }
}
exit $result
