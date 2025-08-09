from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from backend.agents.profiles import create_bellamy, create_holloway, create_tommy, create_perpetrator
from backend.logic.memory import Memory
from backend.logic.qa import ask_character
import os
from dotenv import load_dotenv
import openai

# === Load environment and API Key ===
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
print("Loaded API Key:", openai.api_key[:5] + "..." if openai.api_key else "None")

# === FastAPI App ===
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === In-Memory Store ===
memory = Memory()

# === Characters ===
characters = []

@app.on_event("startup")
async def startup_event():
    global characters
    print("Initializing characters...")
    characters = [
        create_bellamy(),
        create_holloway(),
        create_tommy(),
        create_perpetrator()
    ]

@app.get("/characters")
async def get_characters():
    return [char.name for char in characters]

@app.get("/clues")
async def get_clues():
    return memory.get_clues()

@app.post("/ask")
async def ask(request: Request):
    data = await request.json()
    character_name = data.get("character")
    question = data.get("question")

    character = next((c for c in characters if c.name == character_name), None)
    if not character:
        return {"error": f"No character named {character_name}"}

    answer = await ask_character(character, question, memory)
    return {"response": answer}
