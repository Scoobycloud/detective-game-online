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
    
    system_msg = (
        f"{agent.system_prompt}\n\n"
        f"RULES:\n"
        f"- You ARE {agent.name}. Speak ONLY in first-person (I, me, my).\n"
        f"- NEVER refer to yourself as \"{agent.name}\" or in third-person.\n"
        f"- NEVER analyze or describe yourself - just BE the character.\n"
        f"- If the detective says something vague (like 'hmmm'), respond naturally: be curious, confused, or defensive.\n"
        f"- Stay consistent with prior statements. Don't invent new facts.\n"
        f"- Be concise and natural.\n\n"
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
    max_attempts = 2
    answer = ""
    for attempt in range(max_attempts):
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.4 + (attempt * 0.2),  # Increase temp on retry
        )
        answer = response.choices[0].message.content.strip()
        
        # Check for third-person slip (character referring to themselves by name)
        if agent.name.lower() not in answer.lower():
            break  # Good response, no third-person
        elif attempt < max_attempts - 1:
            # Add correction and retry
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": "Please respond in first-person (using 'I'), not third-person."})
    
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
