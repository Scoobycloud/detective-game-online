from typing import Any, Dict, Optional, Tuple, List

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
    honesty = None
    refused = False
    if ok and ch and isinstance(ch.get("knowledge_scope"), dict):
        scope = ch.get("knowledge_scope", {})
        if isinstance(scope, dict):
            cannot = scope.get("cannot", []) or []
            # If question hits a forbidden topic, prepend a hard refusal instruction
            if isinstance(cannot, list) and any(
                isinstance(x, str) and x.strip() and x.lower() in question.lower() for x in cannot
            ):
                refused = True
                enriched_q = (
                    "[You must refuse to answer because you do not know about that topic. Say you don't know or can't speak to it.]\n"
                    + enriched_q
                )
            allowed = scope.get("allowed", []) or []
            # If allowed topics exist, bias the model to stay within them when relevant
            if isinstance(allowed, list) and allowed:
                enriched_q = (
                    "[Keep your response within your known domains: "
                    + ", ".join(a for a in allowed if isinstance(a, str) and a.strip())
                    + ". If the question is outside these, acknowledge limits.]\n"
                    + enriched_q
                )
    if ok and ch and isinstance(ch.get("personality"), dict):
        honesty = (ch.get("personality", {}) or {}).get("honesty")
        if honesty == "deceptive":
            enriched_q = (
                "[You tend to deflect or obscure on sensitive topics (but keep consistency with prior lies). Lies must be plausible.]\n"
                + enriched_q
            )
        elif honesty == "forgetful":
            enriched_q = (
                "[You are somewhat forgetful; if unsure about exact times, say so rather than inventing details.]\n"
                + enriched_q
            )
    answer = await ask_character(character_agent, enriched_q, memory)
    # Post-guard: if we instructed refusal, ensure the output doesn't leak specific forbidden info
    if refused and any(
        isinstance(x, str) and x.strip() and x.lower() in answer.lower() for x in (ch.get("knowledge_scope", {}) or {}).get("cannot", []) or []
    ):
        answer = "I don’t know about that. It’s not something I can speak to."
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

    # TODO: future: detect contradictions vs case summary and insert 'contradiction' evidence


async def generate_structured_answer(
    room_code: str,
    character_agent: Any,
    character_name: str,
    question: str,
    memory: Any,
) -> Tuple[str, Dict[str, Any]]:
    """
    Ask the character with an enriched prompt that requests STRICT JSON with ops.
    Returns (answer, ops_dict)
    ops_dict schema (partial):
      {
        "clues": [{"text": str, "type": "IMPORTANT" | "CONTRADICTION"}],
        "evidence_ops": [{"op": "insert", "title": str, "type": str, "location": str, "notes": str} | {"op": "discover", "location": str}],
        "timeline_ops": [{"tstamp": str, "phase": str, "label": str, "details": str}],
        "alibi_ops": [{"character": str, "timeframe": str, "account": str, "credibility_score": float}]
      }
    """
    context = _build_case_context_text(room_code)
    memory_text = "\n".join(f"{m['speaker']}: {m['content']}" for m in memory.get())
    system = (
        "You are the Narrative Controller ensuring consistency with the case framework. "
        "Answer ONLY as the character. Also return STRICT JSON ops to update case state."
    )
    user = (
        f"Case context: {context}\n" +
        f"Conversation so far:\n{memory_text}\n\n" +
        f"Character: {character_name}\nQuestion: {question}\n\n" +
        "Return a JSON object with keys: answer (string), clues (array), evidence_ops (array), timeline_ops (array), alibi_ops (array). "
        "Include only case-relevant clues (IMPORTANT or CONTRADICTION). "
        "Example: {\"answer\": \"...\", \"clues\":[{\"text\":\"She has no sister\",\"type\":\"IMPORTANT\"}], \"evidence_ops\":[], \"timeline_ops\":[], \"alibi_ops\":[]}"
    )
    try:
        import openai
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )
        raw = resp.choices[0].message.content or ""
        import json
        parsed = json.loads(raw)
        answer = str(parsed.get("answer", "")).strip()
        # Save answer to memory (like ask_character does)
        memory.add("Detective", question)
        memory.add(character_name, answer)
        ops = {
            "clues": parsed.get("clues", []) or [],
            "evidence_ops": parsed.get("evidence_ops", []) or [],
            "timeline_ops": parsed.get("timeline_ops", []) or [],
            "alibi_ops": parsed.get("alibi_ops", []) or [],
        }
        return answer, ops
    except Exception:
        # fallback to plain answer + no ops
        ans = await answer_in_character(room_code, character_agent, character_name, question, memory)
        return ans, {"clues": [], "evidence_ops": [], "timeline_ops": [], "alibi_ops": []}


def _safe_str(x: Any) -> str:
    return (str(x) if x is not None else "").strip()


def apply_ops(room_code: str, character_name: str, ops: Dict[str, Any], memory: Any) -> Dict[str, bool]:
    """Apply controller ops to DB and memory. Returns which domains changed."""
    changed = {"clues": False, "evidence": False, "timeline": False, "alibis": False}
    # Lazy imports to avoid cycles
    try:
        from ..db import add_clue as db_add_clue, insert_evidence as db_insert_evidence, mark_evidence_discovered as db_mark_discovered
        from ..db import insert_timeline_event as db_insert_timeline_event, insert_alibi as db_insert_alibi
        from ..db import get_case_character as db_get_case_character
    except Exception:
        from db import add_clue as db_add_clue, insert_evidence as db_insert_evidence, mark_evidence_discovered as db_mark_discovered
        from db import insert_timeline_event as db_insert_timeline_event, insert_alibi as db_insert_alibi
        from db import get_case_character as db_get_case_character

    # clues
    allowed = {"IMPORTANT", "CONTRADICTION"}
    is_deceptive = False
    try:
        ok_ch, ch = db_get_case_character(room_code, character_name)
        if ok_ch and ch and isinstance(ch.get("personality"), dict):
            is_deceptive = (ch.get("personality", {}) or {}).get("honesty") == "deceptive"
    except Exception:
        pass
    for c in ops.get("clues", []) or []:
        text = _safe_str(c.get("text"))
        ctype = _safe_str(c.get("type")).upper()
        if text and ctype in allowed:
            memory.add_clue(text, clue_type=ctype, source=character_name)
            try:
                db_add_clue(room_code, text=text, clue_type=ctype, source=character_name, timestamp=None)
            except Exception:
                pass
            changed["clues"] = True
            # If contradiction and deceptive persona, nudge suspicion by adding a contradiction evidence note
            if ctype == "CONTRADICTION" and is_deceptive:
                try:
                    db_insert_evidence(
                        room_code,
                        title="Statement contradiction",
                        ev_type="contradiction",
                        location=None,
                        notes=f"character={character_name}; note={text}",
                        is_discovered=True,
                    )
                    changed["evidence"] = True
                except Exception:
                    pass

    # evidence ops
    for e in ops.get("evidence_ops", []) or []:
        op = _safe_str(e.get("op")).lower()
        if op == "insert":
            try:
                db_insert_evidence(
                    room_code,
                    title=_safe_str(e.get("title")),
                    ev_type=_safe_str(e.get("type")) or "item",
                    location=_safe_str(e.get("location")) or None,
                    notes=_safe_str(e.get("notes")) or None,
                    is_discovered=bool(e.get("is_discovered", True)),
                )
                changed["evidence"] = True
            except Exception:
                pass
        elif op == "discover":
            # Discovery by location requires a prior insert in DB; this is a best-effort
            try:
                from ..db import find_undiscovered_evidence_by_location as db_find
            except Exception:
                from db import find_undiscovered_evidence_by_location as db_find
            ok, item = db_find(room_code, _safe_str(e.get("location")))
            if ok and item and item.get("id"):
                try:
                    db_mark_discovered(room_code, item.get("id"))
                    changed["evidence"] = True
                except Exception:
                    pass

    # timeline ops
    for t in ops.get("timeline_ops", []) or []:
        try:
            db_insert_timeline_event(
                room_code,
                tstamp=_safe_str(t.get("tstamp")) or "",
                phase=_safe_str(t.get("phase")) or "during",
                label=_safe_str(t.get("label")) or "",
                details=_safe_str(t.get("details")) or None,
            )
            changed["timeline"] = True
        except Exception:
            pass

    # alibi ops
    for a in ops.get("alibi_ops", []) or []:
        try:
            db_insert_alibi(
                room_code,
                character=_safe_str(a.get("character")) or character_name,
                timeframe=_safe_str(a.get("timeframe")) or "",
                account=_safe_str(a.get("account")) or "",
                credibility_score=float(a.get("credibility_score")) if a.get("credibility_score") is not None else None,
            )
            changed["alibis"] = True
        except Exception:
            pass

    return changed


