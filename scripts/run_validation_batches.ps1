[CmdletBinding()]
param(
    [ValidateSet("targeted", "w1", "w2", "w3", "w4", "w5", "all")]
    [string]$Batch = "targeted",

    [string]$Endpoint = "http://127.0.0.1:8010/api/v1/meetings/process",

    [string]$JobEndpoint = "http://127.0.0.1:8010/api/v1/meetings/jobs/process-file",

    [string]$ApiKey = $(if ($env:POWER_AUTOMATE_API_KEY) {
        $env:POWER_AUTOMATE_API_KEY
    } else {
        "mi-demo-secret"
    }),

    [int]$TimeoutSeconds = 600,

    [int]$PollIntervalSeconds = 15,

    [string]$Report = "",

    [switch]$RuleOnly,

    [switch]$JobApi
)

$ErrorActionPreference = "Stop"

if ($RuleOnly -and $JobApi) {
    throw "-RuleOnly runs the pipeline in-process and cannot be combined with -JobApi."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$evaluator = Join-Path $PSScriptRoot "evaluate_dataset.py"
$dataset = Join-Path $projectRoot "data\validation"
$evaluationDir = Join-Path $projectRoot "evaluation"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found: $python"
}

if (-not (Test-Path -LiteralPath $dataset)) {
    throw "Validation dataset was not found: $dataset"
}

$targetedCases = @(
    "W1-SHORT-C1-N0-IT-DASG-ABS-018",
    "W2-SHORT-C2-N1-IT-NOOWN-022",
    "W2-SHORT-C3-N0-PROD-CANC-027",
    "W3-MED-C3-N1-OPS-INT-020",
    "W4-LONG-C4-N1-IT-STATE-006"
)

if ($Batch -eq "targeted") {
    $caseIds = $targetedCases
} elseif ($Batch -eq "all") {
    $caseIds = Get-ChildItem -LiteralPath $dataset -Directory |
        Sort-Object Name |
        Select-Object -ExpandProperty Name
} else {
    $prefix = $Batch.ToUpperInvariant()
    $caseIds = Get-ChildItem -LiteralPath $dataset -Directory |
        Where-Object { $_.Name.StartsWith("$prefix-") } |
        Sort-Object Name |
        Select-Object -ExpandProperty Name
}

if (-not $caseIds) {
    throw "No validation cases were found for batch '$Batch'."
}

New-Item -ItemType Directory -Force -Path $evaluationDir | Out-Null

$mode = if ($RuleOnly) { "rule-only" } else { "hybrid" }
if (-not $Report) {
    $Report = Join-Path $evaluationDir "validation-$Batch-$mode.json"
} elseif (-not [System.IO.Path]::IsPathRooted($Report)) {
    $Report = Join-Path $projectRoot $Report
}

$arguments = @(
    $evaluator,
    $dataset,
    "--timeout", $TimeoutSeconds,
    "--report", $Report
)

if (-not $RuleOnly) {
    if ($JobApi) {
        $arguments += @(
            "--job-endpoint", $JobEndpoint,
            "--poll-interval", $PollIntervalSeconds,
            "--api-key", $ApiKey
        )
    } else {
        $arguments += @("--endpoint", $Endpoint, "--api-key", $ApiKey)
    }
}

foreach ($caseId in $caseIds) {
    $arguments += @("--case-id", $caseId)
}

Write-Host "Validation batch : $Batch"
Write-Host "Mode             : $mode"
Write-Host "Case count       : $($caseIds.Count)"
Write-Host "Report           : $Report"

if (-not $RuleOnly) {
    if ($JobApi) {
        Write-Host "Job endpoint     : $JobEndpoint"
        Write-Host "Poll interval    : $PollIntervalSeconds seconds"
        Write-Host "This run submits jobs and polls the local API; it may use OpenAI fallback."
    } else {
        Write-Host "Endpoint         : $Endpoint"
        Write-Host "This run calls the local API and may use OpenAI fallback."
    }
} else {
    Write-Host "This run executes in-process without an AI provider."
}

Write-Host ""

$aiEnvironmentNames = @(
    "OPENAI_API_KEY",
    "AI_FALLBACK_ENDPOINT",
    "AI_FALLBACK_API_KEY"
)
$savedAiEnvironment = @{}

try {
    if ($RuleOnly) {
        foreach ($name in $aiEnvironmentNames) {
            $savedAiEnvironment[$name] = [Environment]::GetEnvironmentVariable(
                $name,
                [EnvironmentVariableTarget]::Process
            )
            [Environment]::SetEnvironmentVariable(
                $name,
                $null,
                [EnvironmentVariableTarget]::Process
            )
        }
    }

    & $python @arguments
    $exitCode = $LASTEXITCODE
} finally {
    if ($RuleOnly) {
        foreach ($name in $aiEnvironmentNames) {
            [Environment]::SetEnvironmentVariable(
                $name,
                $savedAiEnvironment[$name],
                [EnvironmentVariableTarget]::Process
            )
        }
    }
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "Batch '$Batch' passed."
} else {
    Write-Warning "Batch '$Batch' contains failed cases. Review: $Report"
}

exit $exitCode
