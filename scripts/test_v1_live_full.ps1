[CmdletBinding()]
param(
    [string]$ApiKey = $(if ($env:POWER_AUTOMATE_API_KEY) {
        $env:POWER_AUTOMATE_API_KEY
    } else {
        "mi-demo-secret"
    }),

    [int]$Port = 8010,

    [int]$TimeoutSeconds = 3600,

    [int]$PollIntervalSeconds = 15,

    [string]$OutputDirectory = "",

    [ValidateSet("smoke", "gate-b", "full")]
    [string]$Scope = "smoke",

    [int]$FullTimeoutSeconds = 28800,

    [switch]$Resume,

    [switch]$RequireAllCasesPass,

    [double]$MinimumPrecision = -1,

    [double]$MinimumRecall = -1,

    [int]$MaximumUnexpectedTasks = -1,

    [int]$MaximumProviderCalls = -1,

    [int]$MaximumTotalTokens = -1,

    [int]$MaximumContractErrors = 0,

    [double]$MinimumFieldAccuracy = -1,

    [double]$MaximumFieldAccuracyDrop = -1,

    [string]$OpenAIModel = $(if ($env:OPENAI_MODEL) {
        $env:OPENAI_MODEL
    } else {
        "gpt-5-mini"
    }),

    [ValidateSet("minimal", "low", "medium", "high")]
    [string]$OpenAIReasoningEffort = $(if ($env:OPENAI_REASONING_EFFORT) {
        $env:OPENAI_REASONING_EFFORT
    } else {
        "medium"
    }),

    [string]$PromptVersion = $(if ($env:PROMPT_VERSION) {
        $env:PROMPT_VERSION
    } else {
        "v1-ledger-proposal-v1"
    })
)

# Run the reviewed V1 corpus through the real job API. Meeting notes are
# intentionally enabled: the evaluator packages each sidecar note with its
# transcript before upload. This script never prints OPENAI_API_KEY.

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$evaluator = Join-Path $PSScriptRoot "evaluate_dataset.py"
$usageSummarizer = Join-Path $PSScriptRoot "summarize_openai_usage.py"
$candidateIdentityAuditor = Join-Path $PSScriptRoot "audit_ai_candidate_identity.py"
$dataset = Join-Path $projectRoot "data\validation"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found: $python"
}
if (-not (Test-Path -LiteralPath $dataset)) {
    throw "Validation dataset was not found: $dataset"
}
if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is required to verify the live OpenAI fallback."
}

$caseCount = (Get-ChildItem -LiteralPath $dataset -Directory).Count
$noteCount = (Get-ChildItem -LiteralPath $dataset -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "meeting_note.txt") }).Count
if ($caseCount -ne 86 -or $noteCount -ne 86) {
    throw "Expected 86 reviewed cases and 86 meeting notes; found $caseCount cases and $noteCount notes."
}

$runId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
if ($Resume -and -not $OutputDirectory) {
    throw "-Resume requires -OutputDirectory pointing to the interrupted run."
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "evaluation\live-v1-$runId"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot $OutputDirectory
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$traceDirectory = Join-Path $OutputDirectory "traces"
$reportPath = Join-Path $OutputDirectory "v1-with-meeting-notes.json"
$usagePath = Join-Path $OutputDirectory "openai-usage.json"
$baselinePath = Join-Path $OutputDirectory "rule-only-baseline.json"
$serverLog = Join-Path $OutputDirectory "uvicorn.log"
$serverErrorLog = Join-Path $OutputDirectory "uvicorn-error.log"
$caseCsv = Join-Path $OutputDirectory "selected-cases.csv"
$candidateIdentityAuditPath = Join-Path $OutputDirectory "candidate-identity-audit.json"

$allCases = @(Get-ChildItem -LiteralPath $dataset -Directory)
$selectedCases = @()
if ($Scope -eq "smoke") {
    $smokeCaseIds = @(
        "W2-SHORT-C3-N0-PROD-CANC-027",
        "W3-MED-C4-N2-PROD-INT-007",
        "W4-LONG-C5-N1-SW-STATE-009",
        "W4-LONG-C5-N2-SW-STATE-001"
    )
    foreach ($caseId in $smokeCaseIds) {
        $case = $allCases | Where-Object { $_.Name -eq $caseId } | Select-Object -First 1
        if (-not $case) {
            throw "Smoke case was not found: $caseId"
        }
        $selectedCases += $case
    }
} elseif ($Scope -eq "gate-b") {
    $longCases = Get-ChildItem -LiteralPath $dataset -Directory |
        Where-Object { $_.Name -like "W4-*" -or $_.Name -like "W5-*" } |
        Sort-Object Name
    $positiveControls = Get-ChildItem -LiteralPath $dataset -Directory |
        Where-Object { $_.Name -like "W1-*" -or $_.Name -like "W2-*" } |
        Where-Object {
            $expected = Get-Content -LiteralPath (Join-Path $_.FullName "expected_output.json") -Raw | ConvertFrom-Json
            @($expected.tasks).Count -gt 0
        } |
        Sort-Object Name |
        Select-Object -First 8
    $selectedCases = @($longCases) + @($positiveControls)
} else {
    $selectedCases = $allCases
}
if ($Scope -ne "full") {
    $selectedCases | ForEach-Object { [pscustomobject]@{ case_id = $_.Name } } |
        Export-Csv -LiteralPath $caseCsv -NoTypeInformation -Encoding utf8
}
$expectedRunCaseCount = $selectedCases.Count

$candidateAuditArgs = @(
    $candidateIdentityAuditor,
    $dataset,
    "--report", $candidateIdentityAuditPath
)
if ($Scope -ne "full") {
    $candidateAuditArgs += @("--case-csv", $caseCsv)
}
Write-Host "Running provider-free candidate identity preflight"
& $python @candidateAuditArgs
if ($LASTEXITCODE -ne 0) {
    throw "Candidate identity preflight failed. Review $candidateIdentityAuditPath"
}

$baselineArgs = @(
    $evaluator,
    $dataset,
    "--pipeline-version", "v1",
    "--context-mode", "assist",
    "--reviewed-only",
    "--report", $baselinePath
)
if ($Scope -ne "full") {
    $baselineArgs += @("--case-csv", $caseCsv)
}
$baselineMetadataEnvironment = @{
    "OPENAI_MODEL" = [Environment]::GetEnvironmentVariable(
        "OPENAI_MODEL", [EnvironmentVariableTarget]::Process
    )
    "OPENAI_REASONING_EFFORT" = [Environment]::GetEnvironmentVariable(
        "OPENAI_REASONING_EFFORT", [EnvironmentVariableTarget]::Process
    )
    "PROMPT_VERSION" = [Environment]::GetEnvironmentVariable(
        "PROMPT_VERSION", [EnvironmentVariableTarget]::Process
    )
}
try {
    [Environment]::SetEnvironmentVariable("OPENAI_MODEL", $OpenAIModel, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("OPENAI_REASONING_EFFORT", $OpenAIReasoningEffort, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("PROMPT_VERSION", $PromptVersion, [EnvironmentVariableTarget]::Process)

    Write-Host "Creating rule-only baseline for the same $Scope subset"
    & $python @baselineArgs
    if (-not (Test-Path -LiteralPath $baselinePath)) {
        throw "Rule-only evaluator did not write its report: $baselinePath"
    }
    $baseline = Get-Content -LiteralPath $baselinePath -Raw | ConvertFrom-Json
    if ([int]$baseline.completed_case_count -ne $expectedRunCaseCount) {
        throw "Rule-only baseline is incomplete: $($baseline.completed_case_count)/$expectedRunCaseCount cases."
    }
    if (
        $baseline.model -ne $OpenAIModel -or
        $baseline.reasoning_effort -ne $OpenAIReasoningEffort -or
        $baseline.prompt_version -ne $PromptVersion
    ) {
        throw "Rule-only baseline metadata does not match the pinned model/reasoning/prompt configuration."
    }
} finally {
    foreach ($name in $baselineMetadataEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $baselineMetadataEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
}

$environmentNames = @(
    "POWER_AUTOMATE_API_KEY",
    "PIPELINE_VERSION",
    "MEETING_CONTEXT_MODE",
    "PIPELINE_TRACE_ENABLED",
    "PIPELINE_TRACE_DIRECTORY",
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "PROMPT_VERSION"
)
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        [EnvironmentVariableTarget]::Process
    )
}

$server = $null
try {
    [Environment]::SetEnvironmentVariable("POWER_AUTOMATE_API_KEY", $ApiKey, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("PIPELINE_VERSION", "v1", [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("MEETING_CONTEXT_MODE", "assist", [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("PIPELINE_TRACE_ENABLED", "true", [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("PIPELINE_TRACE_DIRECTORY", $traceDirectory, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("OPENAI_MODEL", $OpenAIModel, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("OPENAI_REASONING_EFFORT", $OpenAIReasoningEffort, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable("PROMPT_VERSION", $PromptVersion, [EnvironmentVariableTarget]::Process)

    $server = Start-Process -FilePath $python -ArgumentList @(
        "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$Port"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $serverLog -RedirectStandardError $serverErrorLog -PassThru

    $baseUrl = "http://127.0.0.1:$Port"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
        } catch {
            $health = $null
        }
    } while ($null -eq $health -and (Get-Date) -lt $deadline)

    if ($null -eq $health -or $health.status -ne "ok") {
        throw "V1 API did not become healthy. Check $serverLog and $serverErrorLog"
    }

    $jobEndpoint = "$($baseUrl.TrimEnd('/'))/api/v1/meetings/jobs/process-file"
    Write-Host "Running V1 with meeting notes: $Scope ($expectedRunCaseCount cases)"
    Write-Host "Job endpoint: $jobEndpoint"
    Write-Host "Report: $reportPath"

    $evaluatorArgs = @(
        $evaluator,
        $dataset,
        "--job-endpoint", $jobEndpoint,
        "--api-key", $ApiKey,
        "--timeout", $TimeoutSeconds,
        "--full-timeout", $FullTimeoutSeconds,
        "--poll-interval", $PollIntervalSeconds,
        "--pipeline-version", "v1",
        "--context-mode", "assist",
        "--reviewed-only",
        "--report", $reportPath
    )
    if ($Scope -ne "full") {
        $evaluatorArgs += @("--case-csv", $caseCsv)
    }
    if ($Resume) {
        $evaluatorArgs += "--resume"
    }
    & $python @evaluatorArgs
    $validationExitCode = $LASTEXITCODE

    if (-not (Test-Path -LiteralPath $reportPath)) {
        throw "Evaluator did not write its report: $reportPath"
    }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if ([int]$report.completed_case_count -ne $expectedRunCaseCount) {
        throw "Expected the report to contain $expectedRunCaseCount completed cases; found $($report.completed_case_count)."
    }
    if ($report.meeting_notes -ne "sidecar_if_present") {
        throw "Meeting notes were not enabled in the V1 validation run."
    }
    if (
        $report.model -ne $OpenAIModel -or
        $report.reasoning_effort -ne $OpenAIReasoningEffort -or
        $report.prompt_version -ne $PromptVersion
    ) {
        throw "Live report metadata does not match the pinned model/reasoning/prompt configuration."
    }
    $providerCallCount = @($report.case_execution |
        Measure-Object -Property ai_provider_call_count -Sum).Sum
    if ([int]$providerCallCount -lt 1) {
        throw "No provider call was observed; OpenAI fallback was not exercised."
    }

    & $python $usageSummarizer --trace-dir $traceDirectory | Tee-Object -FilePath $usagePath
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to summarize OpenAI usage."
    }

    Write-Host "OpenAI fallback calls: $providerCallCount"
    Write-Host "Usage report: $usagePath"
    Write-Host "Quality result: $($report.metrics.passed_case_count)/$expectedRunCaseCount cases passed"

    $usage = Get-Content -LiteralPath $usagePath -Raw | ConvertFrom-Json
    $baselineUnexpected = @($baseline.cases | ForEach-Object {
        @($_.unexpected_tasks).Count
    } | Measure-Object -Sum).Sum
    $liveUnexpected = @($report.cases | ForEach-Object {
        @($_.unexpected_tasks).Count
    } | Measure-Object -Sum).Sum

    $effectiveMinimumPrecision = [double]$baseline.metrics.task_precision
    if ($MinimumPrecision -ge 0) {
        $effectiveMinimumPrecision = [Math]::Max(
            $effectiveMinimumPrecision,
            $MinimumPrecision
        )
    }
    $effectiveMinimumRecall = [double]$baseline.metrics.task_recall
    if ($MinimumRecall -ge 0) {
        $effectiveMinimumRecall = [Math]::Max(
            $effectiveMinimumRecall,
            $MinimumRecall
        )
    }
    if ($MaximumFieldAccuracyDrop -lt 0) {
        $MaximumFieldAccuracyDrop = switch ($Scope) {
            "smoke" { 0.02 }
            "gate-b" { 0.01 }
            default { 0.0 }
        }
    }
    $effectiveMinimumFieldAccuracy = [Math]::Max(
        0.0,
        [double]$baseline.metrics.field_accuracy - $MaximumFieldAccuracyDrop
    )
    if ($MinimumFieldAccuracy -ge 0) {
        $effectiveMinimumFieldAccuracy = [Math]::Max(
            $effectiveMinimumFieldAccuracy,
            $MinimumFieldAccuracy
        )
    }
    $effectiveMaximumUnexpected = [int]$baselineUnexpected
    if ($MaximumUnexpectedTasks -ge 0) {
        $effectiveMaximumUnexpected = [Math]::Min(
            $effectiveMaximumUnexpected,
            $MaximumUnexpectedTasks
        )
    }
    if ($MaximumProviderCalls -lt 0) {
        $MaximumProviderCalls = switch ($Scope) {
            "smoke" { 25 }
            "gate-b" { 250 }
            default { 1000 }
        }
    }
    if ($MaximumTotalTokens -lt 0) {
        $MaximumTotalTokens = switch ($Scope) {
            "smoke" { 500000 }
            "gate-b" { 5000000 }
            default { 20000000 }
        }
    }

    $contractErrors = (
        [int]$report.pipeline_diagnostics.ai_fallback_error_count +
        [int]$report.pipeline_diagnostics.ai_structural_contract_rejection_count +
        [int]$report.pipeline_diagnostics.ledger_unknown_task_id_rejection_count +
        @($report.execution_errors).Count
    )
    $semanticRejections = [int]$report.pipeline_diagnostics.ai_semantic_rejection_count
    $totalRejections = [int]$report.pipeline_diagnostics.ai_contract_rejection_count
    $classifiedRejections = (
        [int]$report.pipeline_diagnostics.ai_structural_contract_rejection_count +
        $semanticRejections
    )
    $reasonedRejections = (
        [int]$report.pipeline_diagnostics.ai_unknown_task_id_rejection_count +
        [int]$report.pipeline_diagnostics.ai_invalid_source_clause_rejection_count +
        [int]$report.pipeline_diagnostics.ai_invalid_anchor_clause_rejection_count +
        [int]$report.pipeline_diagnostics.ai_non_concrete_action_rejection_count +
        [int]$report.pipeline_diagnostics.ai_invalid_assignee_rejection_count
    )
    $rejectionBreakdown = (
        "unknown_task_id=$([int]$report.pipeline_diagnostics.ai_unknown_task_id_rejection_count), " +
        "invalid_source=$([int]$report.pipeline_diagnostics.ai_invalid_source_clause_rejection_count), " +
        "invalid_anchor=$([int]$report.pipeline_diagnostics.ai_invalid_anchor_clause_rejection_count), " +
        "non_concrete_action=$([int]$report.pipeline_diagnostics.ai_non_concrete_action_rejection_count), " +
        "invalid_assignee=$([int]$report.pipeline_diagnostics.ai_invalid_assignee_rejection_count)"
    )
    $invalidAiEvents = @()
    foreach ($tracePath in Get-ChildItem -LiteralPath $traceDirectory -Filter "*.json" -File) {
        $trace = Get-Content -LiteralPath $tracePath.FullName -Raw | ConvertFrom-Json
        foreach ($event in @($trace.events_after_deduplication)) {
            if ($event.extraction_source -ne "AI") {
                continue
            }
            if (
                $event.event_type -in @("TASK_CREATE", "TASK_COMMITMENT") -or
                [string]::IsNullOrWhiteSpace([string]$event.related_task_id)
            ) {
                $invalidAiEvents += "$($trace.meeting_id):$($event.event_id)"
            }
        }
    }
    $contractErrors += $invalidAiEvents.Count

    $baselinePassingCaseIds = @(
        $baseline.cases |
            Where-Object { $_.passed } |
            ForEach-Object { $_.case_id }
    )
    $livePassingCaseIds = @(
        $report.cases |
            Where-Object { $_.passed } |
            ForEach-Object { $_.case_id }
    )
    $regressedPassingCases = @(
        $baselinePassingCaseIds |
            Where-Object { $_ -notin $livePassingCaseIds }
    )
    $baselineUnresolvedMutations = [int]$baseline.pipeline_diagnostics.unresolved_mutation_count
    $liveUnresolvedMutations = [int]$report.pipeline_diagnostics.unresolved_mutation_count
    $publicQualityImproved = (
        [double]$report.metrics.task_precision -gt ([double]$baseline.metrics.task_precision + 1e-12) -or
        [double]$report.metrics.task_recall -gt ([double]$baseline.metrics.task_recall + 1e-12) -or
        [double]$report.metrics.field_accuracy -gt ([double]$baseline.metrics.field_accuracy + 1e-12) -or
        [int]$liveUnexpected -lt [int]$baselineUnexpected
    )

    $qualityFailures = @()
    if ($totalRejections -ne $classifiedRejections) {
        $qualityFailures += (
            "rejection diagnostics mismatch: total $totalRejections != " +
            "classified $classifiedRejections"
        )
    }
    if ($totalRejections -ne $reasonedRejections) {
        $qualityFailures += (
            "rejection diagnostics mismatch: total $totalRejections != " +
            "reasoned $reasonedRejections"
        )
    }
    if ([double]$report.metrics.task_precision -lt $effectiveMinimumPrecision) {
        $qualityFailures += "precision $($report.metrics.task_precision) < $effectiveMinimumPrecision"
    }
    if ([double]$report.metrics.task_recall -lt $effectiveMinimumRecall) {
        $qualityFailures += "recall $($report.metrics.task_recall) < $effectiveMinimumRecall"
    }
    if ([double]$report.metrics.field_accuracy -lt $effectiveMinimumFieldAccuracy) {
        $qualityFailures += (
            "field accuracy $($report.metrics.field_accuracy) < " +
            "$effectiveMinimumFieldAccuracy"
        )
    }
    if ([int]$liveUnexpected -gt $effectiveMaximumUnexpected) {
        $qualityFailures += "unexpected tasks $liveUnexpected > $effectiveMaximumUnexpected"
    }
    if ([int]$providerCallCount -gt $MaximumProviderCalls) {
        $qualityFailures += "provider calls $providerCallCount > $MaximumProviderCalls"
    }
    if ([int64]$usage.total_tokens -gt $MaximumTotalTokens) {
        $qualityFailures += "total tokens $($usage.total_tokens) > $MaximumTotalTokens"
    }
    if ($contractErrors -gt $MaximumContractErrors) {
        $qualityFailures += "contract/schema/provider errors $contractErrors > $MaximumContractErrors"
    }
    if ($regressedPassingCases.Count -gt 0) {
        $qualityFailures += "rule-only passing cases regressed: $($regressedPassingCases -join ', ')"
    }
    if ($Scope -eq "gate-b" -and -not $publicQualityImproved) {
        $qualityFailures += (
            "Gate B did not improve any public quality metric over its " +
            "matched baseline"
        )
    }
    if (
        $baselineUnresolvedMutations -gt 0 -and
        $liveUnresolvedMutations -ge $baselineUnresolvedMutations
    ) {
        $qualityFailures += (
            "unresolved mutations did not decrease: " +
            "$liveUnresolvedMutations >= $baselineUnresolvedMutations"
        )
    }
    if ($baselineUnresolvedMutations -eq 0 -and $liveUnresolvedMutations -ne 0) {
        $qualityFailures += "live run introduced $liveUnresolvedMutations unresolved mutations"
    }

    Write-Host (
        "Baseline comparison: precision $($baseline.metrics.task_precision) -> " +
        "$($report.metrics.task_precision), recall $($baseline.metrics.task_recall) -> " +
        "$($report.metrics.task_recall), field accuracy " +
        "$($baseline.metrics.field_accuracy) -> $($report.metrics.field_accuracy), " +
        "unexpected $baselineUnexpected -> $liveUnexpected, " +
        "unresolved mutations $baselineUnresolvedMutations -> $liveUnresolvedMutations"
    )
    Write-Host (
        "Contract errors: $contractErrors | Semantic safety rejections: " +
        "$semanticRejections | Rejection reasons: $rejectionBreakdown | " +
        "Total tokens: $($usage.total_tokens)"
    )

    if ($qualityFailures.Count -gt 0) {
        throw "V1 $Scope gate failed: $($qualityFailures -join '; '). Review $reportPath"
    }

    if ($RequireAllCasesPass -and ($validationExitCode -ne 0 -or [int]$report.metrics.passed_case_count -ne $expectedRunCaseCount)) {
        throw "V1 quality gate failed: $($report.metrics.passed_case_count)/$expectedRunCaseCount cases passed. Review $reportPath"
    }
    Write-Host "V1 $Scope quantitative gate passed."
} finally {
    if ($server) {
        Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedEnvironment[$name],
            [EnvironmentVariableTarget]::Process
        )
    }
}
