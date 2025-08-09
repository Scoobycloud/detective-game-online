import openai
import json
import re

async def ask_character(agent, question: str, memory):
    # === Build prompt with system prompt and memory ===
    system_prompt = agent.system_prompt
    memory_text = "\n".join(
        f"{entry['speaker']}: {entry['content']}" for entry in memory.get()
    )
    # prompt = f"{system_prompt}\n\n{memory_text}\n\n{agent.name}, reply directly to the detective's question:\n\"{question}\""
    prompt = f"{system_prompt}\n\nPrevious conversation:\n{memory_text}\n\nNow reply ONLY as {agent.name} to this question: \"{question}\"\n\nDo not include any detective dialogue or questions in your response."

    # === Get character's response ===
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    answer = response.choices[0].message.content.strip()

    # === Save to memory ===
    memory.add("Detective", question)

    pattern = rf"^{agent.name}:\s*"
    answer = re.sub(pattern, "", answer, flags=re.IGNORECASE)
    memory.add(agent.name, answer)

    # === Ask GPT to extract structured clues ===
    clue_prompt = f"""Extract all potential clues from the following reply. 
Label each clue as either "important", "background", or "gossip" depending on how relevant and actionable it is to a murder investigation.
Reply in JSON format as a list of objects like this:
[
  {{"text": "She heard a loud thud around 9am", "type": "important"}},
  {{"text": "She was watering plants", "type": "background"}},
  {{"text": "She thinks the victim was grumpy", "type": "gossip"}}
]

Reply: {answer}
"""

    try:
        clue_response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": clue_prompt}],
            temperature=0.4,
        )

        parsed = json.loads(clue_response.choices[0].message.content.strip())

        for clue in parsed:
            text = clue.get("text", "").strip()
            clue_type = clue.get("type", "fact").upper()
            if text:
                memory.add_clue(text, clue_type=clue_type, source=agent.name)

    except Exception as e:
        print("Failed to extract or parse clues:", e)

    return answer
