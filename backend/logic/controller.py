from typing import Any, Dict, Optional

try:
    # local import pattern
    from ..db import get_case_framework as db_get_case_framework
except Exception:
    from db import get_case_framework as db_get_case_framework  # type: ignore

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


