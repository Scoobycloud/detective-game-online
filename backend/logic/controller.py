from typing import Any, Dict, Optional

try:
    # local import pattern
    from ..db import get_case_framework as db_get_case_framework, get_case_character as db_get_case_character
except Exception:
    from db import get_case_framework as db_get_case_framework, get_case_character as db_get_case_character  # type: ignore

from .qa import ask_character, extract_clues_from_reply


def _build_case_context_text(room_code: str) -> str:
    ok, data = db_get_case_framework(room_code)
    if not ok or not data:
        return ""
    case = (data or {}).get("case", {})
    summary = case.get("summary", {})
    victim = summary.get("victim")
    motive = summary.get("motive")
    weapon = summary.get("weapon")
    location = summary.get("location")
    time = summary.get("time")
    parts = []
    if victim:
        parts.append(f"Victim: {victim}")
    if motive:
        parts.append(f"Motive: {motive}")
    if weapon:
        parts.append(f"Weapon: {weapon}")
    if location:
        parts.append(f"Location: {location}")
    if time:
        parts.append(f"Time: {time}")
    return " | ".join(parts)


async def answer_in_character(
    room_code: str,
    character_agent: Any,
    character_name: str,
    question: str,
    memory: Any,
) -> str:
    """
    Generate a character-consistent answer using the case framework as guardrails.
    For now, we enrich the question with a brief case context string.
    """
    context = _build_case_context_text(room_code)
    enriched_q = question
    if context:
        enriched_q = (
            f"[Case context: {context}. Keep responses consistent with these facts and your character's knowledge.]\n"
            + question
        )
    # Enforce simple knowledge bounds: if character marked as limited on topic, prepend reminder
    try:
        ok, ch = db_get_case_character(room_code, character_name)
    except Exception:
        ok, ch = (False, None)
    if ok and ch and isinstance(ch.get("knowledge_scope"), dict):
        scope = ch.get("knowledge_scope", {})
        if isinstance(scope, dict):
            cannot = scope.get("cannot", []) or []
            if isinstance(cannot, list) and any(isinstance(x, str) and x.lower() in question.lower() for x in cannot):
                enriched_q = "[You do not know about that topic; answer honestly within your limits.]\n" + enriched_q
            allowed = scope.get("allowed", []) or []
            # no-op for allowed; could bias later
    answer = await ask_character(character_agent, enriched_q, memory)
    return answer


async def postprocess_human_answer(
    room_code: str,
    character_name: str,
    answer: str,
    memory: Any,
) -> None:
    """
    Best-effort post-processing for human answers: extract clues and (future) validate consistency.
    """
    try:
        await extract_clues_from_reply(character_name, answer, memory)
    except Exception:
        pass


