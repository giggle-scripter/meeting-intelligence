[CmdletBinding()]
param(
    [string]$CaseId = "W4-LONG-C5-N1-SW-STATE-009",
    [int]$TimeoutSeconds = 3600,
    [switch]$SkipAutomatedTests,
    [switch]$AllowQualityFailures
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dataset = Join-Path $projectRoot "data\validation"
$evaluator = Join-Path $PSScriptRoot "evaluate_dataset.py"
$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$outputDirectory = Join-Path $projectRoot "evaluation\runtime\local-openai-$runId"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found: $python"
}
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw 'Set $env:OPENAI_API_KEY before running this script.'
}
if (-not (Test-Path -LiteralPath (Join-Path $dataset $CaseId))) {
    throw "Validation case was not found: $CaseId"
}

$env:OPENAI_MODEL = if ($env:OPENAI_MODEL) { $env:OPENAI_MODEL } else { "gpt-5-mini" }
$env:OPENAI_REASONING_EFFORT = if ($env:OPENAI_REASONING_EFFORT) {
    $env:OPENAI_REASONING_EFFORT
} else {
    "medium"
}
$env:AI_TIMEOUT_SECONDS = [string]$TimeoutSeconds
$env:JOB_TIMEOUT_SECONDS = [string]$TimeoutSeconds
$env:PIPELINE_VERSION = "v1"
$env:MEETING_CONTEXT_MODE = "assist"
$env:AI_MAX_BATCH_CONTEXT_CLAUSES = "56"
$env:PIPELINE_TRACE_ENABLED = "false"

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

if (-not $SkipAutomatedTests) {
    Write-Host "Running isolated backend automated tests (no auth/provider calls)"
    & $python -m pytest (Join-Path $projectRoot "backend\tests") -q
    if ($LASTEXITCODE -ne 0) {
        throw "Backend automated tests failed."
    }
}

$variants = @(
    @{ Name = "without-notes"; ExtraArguments = @("--without-meeting-notes") },
    @{ Name = "with-notes"; ExtraArguments = @() }
)
$totalProviderCalls = 0
$qualityFailures = @()

foreach ($variant in $variants) {
    $reportPath = Join-Path $outputDirectory "$($variant.Name).json"
    $arguments = @(
        $evaluator,
        $dataset,
        "--local-openai",
        "--pipeline-version", "v1",
        "--context-mode", "assist",
        "--case-id", $CaseId,
        "--timeout", $TimeoutSeconds,
        "--full-timeout", $TimeoutSeconds,
        "--report", $reportPath
    ) + $variant.ExtraArguments

    Write-Host "Running local OpenAI smoke: $($variant.Name) / $CaseId"
    & $python @arguments
    $evaluationExitCode = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $reportPath)) {
        throw "Local evaluator did not write its report: $reportPath"
    }

    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if (@($report.execution_errors).Count -gt 0) {
        throw "Local execution failed: $($variant.Name). Review $reportPath"
    }
    if ($evaluationExitCode -ne 0 -or [int]$report.metrics.failed_case_count -gt 0) {
        $qualityFailures += $variant.Name
        Write-Warning (
            "Quality comparison failed for $($variant.Name); continuing the " +
            "A/B run. Review $reportPath"
        )
    }
    $providerCalls = @(
        $report.case_execution |
            Measure-Object -Property ai_provider_call_count -Sum
    )[0].Sum
    $totalProviderCalls += [int]$providerCalls
    if ([int]$providerCalls -lt 1) {
        $routes = @($report.case_execution | Select-Object -ExpandProperty route)
        $expectedSkipRoutes = @("rule_only", "ai_fallback_skipped_no_candidate")
        $unexpectedRoutes = @($routes | Where-Object { $_ -notin $expectedSkipRoutes })
        if ($unexpectedRoutes.Count -gt 0) {
            throw (
                "OpenAI was unexpectedly not called for $($variant.Name) " +
                "(route=$($unexpectedRoutes -join ',')). Review $reportPath"
            )
        }
        Write-Host (
            "Passed $($variant.Name): provider call not required " +
            "(route=$($routes -join ','))"
        )
        continue
    }
    if ([int]$report.pipeline_diagnostics.ai_fallback_error_count -ne 0) {
        throw "OpenAI fallback returned an error for $($variant.Name). Review $reportPath"
    }
    Write-Host "Passed $($variant.Name): OpenAI calls=$providerCalls"
}

if ($qualityFailures.Count -gt 0) {
    $qualityMessage = (
        "Local A/B quality check failed for: $($qualityFailures -join ', '). " +
        "Reports: $outputDirectory"
    )
    if ($AllowQualityFailures) {
        Write-Warning $qualityMessage
        Write-Host (
            "Diagnostic run completed. Total OpenAI calls=$totalProviderCalls."
        )
        exit 0
    }
    throw $qualityMessage
}

Write-Host (
    "Local checks passed. Total OpenAI calls=$totalProviderCalls. " +
    "Reports: $outputDirectory"
)
