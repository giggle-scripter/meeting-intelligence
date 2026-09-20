[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$SmokeArgs
)

$ErrorActionPreference = "Stop"

if (-not $PythonExecutable) {
    $candidate = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $PythonExecutable = $candidate
    } else {
        $PythonExecutable = "python"
    }
}

$client = Join-Path $PSScriptRoot "unified_core_smoke.py"
& $PythonExecutable $client @SmokeArgs
exit $LASTEXITCODE
