"""Two fixed-order teacher prompts sharing one strict output contract."""

from __future__ import annotations

import json

from .contracts import TeacherPayload
from .hashing import sha256_bytes


COMMON = """Return JSON only under the supplied strict schema. Select only proposal, clause,
date mention, action span, and existing task references present in the payload.
Never invent a task name, resolved calendar date, clause/date/task ID, or free-form
rationale. CREATE requires a grounded action span plus transcript authority or a
valid later acceptance. Notes never create authority. UPDATE/REFERENCE never mint
task identity. Preserve sibling actions and fail closed as UNRESOLVED."""


PROMPT_INSTRUCTIONS = {
    "teacher-a-v1": COMMON
    + "\nEvaluate in this order: authority, acceptance, action, owner/deadline, negative and lifecycle evidence.",
    "teacher-b-v1": COMMON
    + "\nEvaluate in this order: negative/lifecycle evidence, mutation/reference state, authority/acceptance, action, owner/deadline.",
}


def prompt_hash(version: str) -> str:
    if version not in PROMPT_INSTRUCTIONS:
        raise ValueError("unknown teacher prompt version")
    return sha256_bytes(PROMPT_INSTRUCTIONS[version].encode("utf-8"))


def render_prompt(payload: TeacherPayload, version: str) -> str:
    instruction = PROMPT_INSTRUCTIONS.get(version)
    if instruction is None:
        raise ValueError("unknown teacher prompt version")
    payload_json = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return instruction + "\nPAYLOAD_JSON:\n" + payload_json
