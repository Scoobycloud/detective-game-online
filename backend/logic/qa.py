import openai
import json
import re


async def ask_character(agent, question: str, memory):
    # === Build prompt with system prompt and memory ===
    system_prompt = agent.system_prompt
    memory_text = "\n".join(
        f"{entry['speaker']}: {entry['content']}" for entry in memory.get()
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
        "If the detective's input is unclear or not a question, ask for a brief clarification in character. "
        "If the input is off-topic (e.g., a joke request), politely deflect and steer back to the investigation. "
        "Do not repeat the same alibi or stock lines verbatim unless directly relevant. "
        "Speak in first-person as the character (no stage directions or third-person narration). "
        "Stay consistent with prior statements and the case context. Do not include any detective dialogue."
    )
    off_topic_preface = (
        "The detective's prompt appears off-topic or frivolous; provide a brief, polite deflection and steer back to relevant case details. "
        "If appropriate, ask a short clarifying question tied to the case."
        if is_off_topic
        else ""
    )
    prompt = (
        f"{system_prompt}\n\n{convo_guidelines}\n{off_topic_preface}\n\nPrevious conversation:\n{memory_text}\n\n"
        f'Now reply ONLY as {agent.name} to this question: "{question}"'
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
