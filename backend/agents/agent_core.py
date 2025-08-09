import openai
import os
from dotenv import load_dotenv

# Load .env file from project root
from pathlib import Path
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

# Set API key
openai.api_key = os.getenv("OPENAI_API_KEY")
print("Loaded API Key:", openai.api_key[:5] + "..." if openai.api_key else "None")

class SimpleAgent:
    def __init__(self, name, role, tools=None):
        self.name = name
        self.role = role
        self.tools = tools or []
        self.messages = [{"role": "system", "content": role}]

    async def run(self, input_text: str):
        self.messages.append({"role": "user", "content": input_text})

        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=self.messages,
            tools=self.tools,
            tool_choice="auto" if self.tools else None
        )

        reply = response.choices[0].message
        self.messages.append(reply)

        # 🔍 Debug full reply object
        print(f"\n🔁 FULL REPLY OBJECT FROM {self.name}:\n{reply}\n")

        # Extract content safely (works for both dicts and OpenAI objects)
        content = getattr(reply, "content", None) if hasattr(reply, "content") else reply.get("content")

        if content:
            return content.strip()

        # Handle tool_call fallback (if needed later)
        if hasattr(reply, "tool_calls"):
            return f"[Tool call used: {reply.tool_calls[0].function.name}]"
        if "tool_calls" in reply:
            return f"[Tool call used: {reply['tool_calls'][0]['function']['name']}]"

        return "[No reply from agent]"
