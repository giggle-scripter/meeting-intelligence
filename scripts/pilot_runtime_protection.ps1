[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string] $PythonExecutable = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'),

    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('backup', 'verify', 'restore', 'retention')]
    [string] $Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

# Thin wrapper only: it invokes the explicit Python CLI and never starts a
# backend, tunnel, provider, trainer, scheduled task, or deletion operation.
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python executable not found: $PythonExecutable"
}

& $PythonExecutable (Join-Path $PSScriptRoot 'pilot_runtime_protection.py') $Command @Arguments
exit $LASTEXITCODE
