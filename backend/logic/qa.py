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
    # === Build prompt with system prompt and LIMITED memory (only Detective <-> this character) ===
    system_prompt = agent.system_prompt
    prior_dialogue = memory.get_dialogue_for(agent.name)
    memory_text = "\n".join(
        f"{entry['speaker']}: {entry['content']}" for entry in prior_dialogue
    )
    off_topic_triggers = [
        "joke",
        "funny",
        "make me laugh",
        "tell me a joke",
        "humour",
        "humor",
        "sing",
        "riddle",
    ]
    is_off_topic = any(t in question.lower() for t in off_topic_triggers)
    convo_guidelines = (
        "Guidelines: Be natural, concise, and context-aware. Answer only what was asked. "
        "If the detective's input is unclear, vague, or just a reaction (like 'hmmm', 'interesting', 'I see'), "
        "respond naturally in character - perhaps with curiosity, defensiveness, or a follow-up question. "
        "If the input is off-topic (e.g., a joke request), politely deflect and steer back to the investigation. "
        "Do not repeat the same alibi or stock lines verbatim unless directly relevant. "
        "CRITICAL: You ARE this character. ALWAYS speak in first-person ('I', 'me', 'my'). "
        "NEVER analyze yourself in third-person. NEVER describe what the character might do or think - just BE them. "
        "Stay consistent with prior statements and the case context. Do not include any detective dialogue."
    )
    off_topic_preface = (
        "The detective's prompt appears off-topic or frivolous; provide a brief, polite deflection and steer back to relevant case details. "
        "If appropriate, ask a short clarifying question tied to the case."
        if is_off_topic
        else ""
    )
    # === Load per-character knowledge and build constraints ===
    knowledge = load_knowledge()
    knowledge_text = build_knowledge_text(agent.name, knowledge)
    prompt = (
        f"{system_prompt}\n\n{convo_guidelines}\n{off_topic_preface}\n\n"
        f"You must answer ONLY using:\n"
        f"1) Your own background/perspective, and\n"
        f"2) The knowledge provided below about other witnesses.\n"
        f"Do NOT assume knowledge of what other witnesses told the detective unless it is explicitly in your knowledge. "
        f"Do NOT invent facts.\n\n"
        f"Your provided knowledge:\n{knowledge_text}\n\n"
        f"Previous conversation with the Detective (for continuity only):\n{memory_text}\n\n"
        f"The detective says: \"{question}\"\n\n"
        f"Respond in first-person as {agent.name}. Use 'I' and 'my', never '{agent.name}' or third-person. "
        f"If the detective's statement is vague, react naturally (curiosity, confusion, defensiveness) but stay in character."
    )

    # === Get character's response ===
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.35,
    )
    answer = response.choices[0].message.content.strip()

    # === Save to memory ===
    memory.add("Detective", question)

    pattern = rf"^{agent.name}:\s*"
    answer = re.sub(pattern, "", answer, flags=re.IGNORECASE)
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
