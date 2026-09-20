"""Static safety contract for the manually invoked pilot CI workflow."""

from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "manual-pilot-ci.yml"


def test_manual_pilot_workflow_is_dispatch_only_and_safe() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)

    automatic_triggers = r"^\s{2}(?:push|pull_request|schedule|repository_dispatch|workflow_call):\s*$"
    assert not re.search(automatic_triggers, text, flags=re.MULTILINE)

    lowered = text.lower()
    assert not re.search(r"\bsecrets(?:\.|\[)", lowered)
    assert not re.search(r"\b(?:uvicorn|docker|tunnel|start-process)\b", lowered)
    assert not re.search(r"\b(?:provider|trainer|train(?:ing)?)\b", lowered)
    assert not re.search(r"\b(?:schedule|cron|workflow_call|repository_dispatch)\b", lowered)
    assert "upload-artifact" not in lowered
    assert "transcript" not in lowered
    assert "evaluation/runtime" not in lowered


def test_manual_pilot_workflow_contains_required_gates() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required_fragments = (
        "python -m compileall",
        "Parser]::ParseFile",
        "git diff --check HEAD^ HEAD",
        "backend/tests/unit/test_core_registry.py",
        "backend/tests/end_to_end/test_core_api.py",
        "backend/tests/unit/test_company_shell_packaging.py",
        "tests/test_unified_core_smoke.py",
        "tests/test_pilot_runtime_protection.py",
        "python -m pytest backend/tests tests",
    )
    for fragment in required_fragments:
        assert fragment in text
