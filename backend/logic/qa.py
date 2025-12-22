import openai
import json
import re
from pathlib import Path


KNOWLEDGE_FILE = Path(__file__).resolve().parent.parent / "state" / "knowledge.json"

def load_knowledge():
    try:
        if KNOWLEDGE_FILE.exists():
            return json.loads(KNOWLEDGE_FILE.read_text())
    except Exception as e:
        print("Failed to load knowledge.json:", e)
    return {}

def build_knowledge_text(character_name: str, knowledge: dict) -> str:
    data = knowledge.get(character_name, {})
    background = data.get("background", [])
    location_hints = data.get("location_hints", [])
    about = data.get("about", {})

    lines = []
    if background:
        lines.append("Background knowledge:")
        for item in background:
            lines.append(f"- {item}")
    if location_hints:
        lines.append("Things you've noticed about places (share naturally if the detective asks about locations or evidence):")
        for hint in location_hints:
            lines.append(f"- {hint}")
    if about:
        lines.append("Knowledge about other witnesses:")
        for other, facts in about.items():
            if facts:
                lines.append(f"- About {other}:")
                for fact in facts:
                    lines.append(f"  • {fact}")
    return "\n".join(lines) if lines else "No specific knowledge provided."

async def ask_character(agent, question: str, memory):
    # === Build system prompt with character identity ===
    knowledge = load_knowledge()
    knowledge_text = build_knowledge_text(agent.name, knowledge)
    
    # Build list of valid character names from knowledge (to prevent AI hallucinating names)
    valid_characters = list(knowledge.keys()) if knowledge else []
    valid_names_str = ", ".join(valid_characters) if valid_characters else "Unknown"
    
    # Detect vague/reaction inputs that tend to confuse the AI
    vague_inputs = ['hmm', 'hmmm', 'hmmmm', 'hmmmmm', 'interesting', 'i see', 'ok', 'okay', 'right', 'uh huh', 'go on']
    is_vague = question.strip().lower().rstrip('.,!?') in vague_inputs or question.strip().lower().startswith('hmm')
    
    vague_examples = ""
    if is_vague:
        vague_examples = (
            "\n\nEXAMPLES of how to respond to vague statements like 'hmmm':\n"
            "- \"Is there something else you'd like to know?\"\n"
            "- \"I'm not sure what you mean by that.\"\n"
            "- \"Do you have more questions for me, Detective?\"\n"
            "- \"I've told you everything I know.\"\n"
            "- \"Why are you looking at me like that?\"\n"
        )
    
    system_msg = (
        f"{agent.system_prompt}\n\n"
        f"CRITICAL RULES:\n"
        f"- You ARE {agent.name}. Speak ONLY in first-person (I, me, my).\n"
        f"- NEVER write \"{agent.name}\" in your response.\n"
        f"- NEVER analyze yourself or your alibi in third-person.\n"
        f"- NEVER say things like \"{agent.name}'s claim\" or \"{agent.name}'s alibi\" or \"{agent.name}'s routine\".\n"
        f"- WRONG: \"{agent.name}'s alibi seems plausible...\"\n"
        f"- RIGHT: \"I was baking a pie, as I told you.\"\n"
        f"- The ONLY people in this case are: {valid_names_str}. Do NOT mention any other names.\n"
        f"- Do NOT invent victims, witnesses, or suspects. If referring to a victim, say 'the victim'.\n"
        f"- If the detective says something vague, respond naturally in character.\n"
        f"- Be concise and stay in character.{vague_examples}\n\n"
        f"Your background (speak about this as YOUR OWN experience using 'I'):\n{knowledge_text}"
    )
    
    # === Build message history from memory ===
    messages = [{"role": "system", "content": system_msg}]
    prior_dialogue = memory.get_dialogue_for(agent.name)
    for entry in prior_dialogue:
        if entry['speaker'] == 'Detective':
            messages.append({"role": "user", "content": entry['content']})
        else:
            messages.append({"role": "assistant", "content": entry['content']})
    
    # Add the current question
    messages.append({"role": "user", "content": question})

    # === Get character's response (with retry if third-person detected) ===
    max_attempts = 3
    answer = ""
    print(f"[QA] Asking {agent.name}: '{question}'")
    for attempt in range(max_attempts):
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # Better at role-play than 3.5-turbo
            messages=messages,
            temperature=0.6 + (attempt * 0.15),
        )
        answer = response.choices[0].message.content.strip()
        print(f"[QA] Attempt {attempt+1} response: {answer[:100]}...")
        
        # Check for third-person slip (character referring to themselves by name)
        if agent.name.lower() not in answer.lower():
            print(f"[QA] Good response - no third-person detected")
            break  # Good response, no third-person
        elif attempt < max_attempts - 1:
            # Add stronger correction and retry
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": f"STOP. You just referred to yourself as '{agent.name}'. You ARE {agent.name}. Say 'I' instead. Try again."})
            print(f"[QA] Retry {attempt+1}: Third-person detected ('{agent.name}' in response)")
    
    # === Aggressive post-processing: Replace third-person references ===
    # If after retries we still have third-person, try to salvage it
    if agent.name.lower() in answer.lower():
        print(f"[QA] WARNING: Third-person STILL present after {max_attempts} retries!")
        print(f"[QA] Original answer: {answer}")
        # Replace "Ms. Banana's" with "My", "Ms. Banana is" with "I am", etc.
        # Don't use word boundaries - they don't work well with periods in names
        answer = re.sub(rf"{re.escape(agent.name)}'s", "My", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} is", "I am", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} was", "I was", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} has", "I have", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} had", "I had", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} seems", "I seem", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)} appears", "I appear", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"{re.escape(agent.name)}", "I", answer, flags=re.IGNORECASE)
        # Also fix third-person pronouns when clearly referring to the character
        answer = re.sub(r"\bher background\b", "my background", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bher statement\b", "my statement", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bher alibi\b", "my alibi", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bher routine\b", "my routine", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bShe seems\b", "I seem", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bShe has\b", "I have", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bShe was\b", "I was", answer, flags=re.IGNORECASE)
        answer = re.sub(r"\bShe is\b", "I am", answer, flags=re.IGNORECASE)
        print(f"[QA] Fixed answer: {answer}")
    
    # Final cleanup: remove character name prefix if present (e.g., "Ms. Banana: ...")
    pattern = rf"^{re.escape(agent.name)}:\s*"
    answer = re.sub(pattern, "", answer, flags=re.IGNORECASE)

    # === Save to memory ===
    memory.add("Detective", question)
    memory.add(agent.name, answer)

    # === Ask GPT to extract structured clues (case-relevant ONLY) ===
    clue_prompt = f"""Extract only case-relevant investigative facts from the reply below. 
Return items ONLY if they meaningfully narrow suspects, establish or contradict alibis, reveal opportunity/motive/means, or are specific physical evidence. 
Exclude greetings, pleasantries, generic empathy, and small talk.
Label each as one of: "IMPORTANT" or "CONTRADICTION" (use CONTRADICTION if it conflicts with prior statements or common facts).
Reply in STRICT JSON array form, e.g.:
[
  {{"text": "She does not have a sister", "type": "IMPORTANT"}},
  {{"text": "He was with me at 9pm", "type": "IMPORTANT"}},
  {{"text": "Claims she was alone at 9pm but earlier said she met Holloway", "type": "CONTRADICTION"}}
]

Reply: {answer}
"""

    try:
        clue_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": clue_prompt}],
            temperature=0.4,
        )

        parsed = json.loads(clue_response.choices[0].message.content.strip())

        allowed = {"IMPORTANT", "CONTRADICTION"}
        for clue in parsed:
            text = clue.get("text", "").strip()
            clue_type = str(clue.get("type", "")).upper().strip()
            if text and clue_type in allowed:
                memory.add_clue(text, clue_type=clue_type, source=agent.name)

    except Exception as e:
        print("Failed to extract or parse clues:", e)

    return answer


async def extract_clues_from_reply(agent_name: str, reply: str, memory):
    """
    Parse a character's reply to extract structured clues and add them to memory.
    Mirrors the extraction logic used in ask_character.
    """
    import json
    import openai

    clue_prompt = f"""Extract only case-relevant investigative facts from the reply below. 
Return items ONLY if they meaningfully narrow suspects, establish or contradict alibis, reveal opportunity/motive/means, or are specific physical evidence. 
Exclude greetings, pleasantries, generic empathy, and small talk.
Label each as one of: "IMPORTANT" or "CONTRADICTION".
Reply in STRICT JSON array form.

Reply: {reply}
"""

    try:
        clue_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": clue_prompt}],
            temperature=0.4,
        )
        parsed = json.loads(clue_response.choices[0].message.content.strip())
        allowed = {"IMPORTANT", "CONTRADICTION"}
        for clue in parsed:
            text = clue.get("text", "").strip()
            clue_type = str(clue.get("type", "")).upper().strip()
            if text and clue_type in allowed:
                memory.add_clue(text, clue_type=clue_type, source=agent_name)
    except Exception as e:  # pragma: no cover
        print("Failed to extract or parse clues (standalone):", e)
