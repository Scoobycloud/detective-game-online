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
    about = data.get("about", {})

    lines = []
    if background:
        lines.append("Background knowledge:")
        for item in background:
            lines.append(f"- {item}")
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
        f"- WRONG: \"{agent.name}'s alibi seems plausible...\"\n"
        f"- RIGHT: \"I was baking a pie, as I told you.\"\n"
        f"- If the detective says something vague, respond naturally in character.\n"
        f"- Be concise and stay in character.{vague_examples}\n\n"
        f"Your knowledge:\n{knowledge_text}"
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
    for attempt in range(max_attempts):
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.5 + (attempt * 0.2),  # Increase temp on retry
        )
        answer = response.choices[0].message.content.strip()
        
        # Check for third-person slip (character referring to themselves by name)
        if agent.name.lower() not in answer.lower():
            break  # Good response, no third-person
        elif attempt < max_attempts - 1:
            # Add stronger correction and retry
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": f"That response mentioned '{agent.name}' in third-person. Respond as 'I', staying in character. Do not analyze yourself."})
            print(f"[QA] Retry {attempt+1}: Third-person detected, retrying...")
    
    # === Aggressive post-processing: Replace third-person references ===
    # If after retries we still have third-person, try to salvage it
    if agent.name.lower() in answer.lower():
        print(f"[QA] Warning: Third-person still present after retries, applying fix...")
        # Replace "Ms. Banana's" with "My", "Ms. Banana is" with "I am", etc.
        answer = re.sub(rf"\b{re.escape(agent.name)}'s\b", "My", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"\b{re.escape(agent.name)} is\b", "I am", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"\b{re.escape(agent.name)} was\b", "I was", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"\b{re.escape(agent.name)} has\b", "I have", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"\b{re.escape(agent.name)} had\b", "I had", answer, flags=re.IGNORECASE)
        answer = re.sub(rf"\b{re.escape(agent.name)}\b", "I", answer, flags=re.IGNORECASE)
    
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
