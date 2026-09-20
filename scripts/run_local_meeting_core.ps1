[CmdletBinding()]
param(
    [ValidateSet("v1-frozen", "v2-adaptive")]
    [string]$Core = "v1-frozen",
    [string]$PythonExecutable = "",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8011
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable was not found: $PythonExecutable"
}
if ($BindHost -ne "127.0.0.1" -or $Port -ne 8011) {
    throw "The local meeting core contract is fixed to 127.0.0.1:8011."
}

$required = @{
    POWER_AUTOMATE_API_KEY = $env:POWER_AUTOMATE_API_KEY
    MEETING_FEEDBACK_TENANT_ID = $env:MEETING_FEEDBACK_TENANT_ID
    MEETING_FEEDBACK_DIRECTORY = $env:MEETING_FEEDBACK_DIRECTORY
    MEETING_JOB_SQLITE_PATH = $env:MEETING_JOB_SQLITE_PATH
}
foreach ($name in $required.Keys) {
    if ([string]::IsNullOrWhiteSpace($required[$name])) {
        throw "Set the required common setting `$env:$name before starting the local meeting core."
    }
}

$env:MEETING_CORE = $Core
# The V2 artifact checker retains these aliases for the optional plugin.
$env:V227_FEEDBACK_TENANT_ID = $env:MEETING_FEEDBACK_TENANT_ID
$env:V227_FEEDBACK_DIRECTORY = $env:MEETING_FEEDBACK_DIRECTORY
$env:PYTHONPATH = $projectRoot

Write-Host "Running offline meeting core preflight: $Core"
if ($Core -eq "v2-adaptive") {
    Write-Host "Running the existing offline V2 artifact preflight"
    & $PythonExecutable -m scripts.local_v227_preflight --root $projectRoot --text
    if ($LASTEXITCODE -ne 0) {
        throw "V2 artifact preflight failed; the API was not started."
    }
}
& $PythonExecutable -m scripts.core_preflight --core $Core --root $projectRoot --text
if ($LASTEXITCODE -ne 0) {
    throw "Meeting core preflight failed; the API was not started."
}

Write-Host "Starting backend.app.core_api:app on 127.0.0.1:8011 with one worker"
& $PythonExecutable -m uvicorn backend.app.core_api:app `
    --host 127.0.0.1 `
    --port 8011 `
    --workers 1
exit $LASTEXITCODE
