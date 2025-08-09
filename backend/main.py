# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.agents.profiles import create_bellamy, create_holloway, create_tommy, create_perpetrator
from backend.logic.memory import Memory
from backend.logic.qa import ask_character
import os
from dotenv import load_dotenv
import openai

# === NEW: sockets bits ===
import socketio
import asyncio
import uuid

# === Load environment and API Key ===
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
print("Loaded API Key:", openai.api_key[:5] + "..." if openai.api_key else "None")

# === FastAPI App (unchanged) ===
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === In-Memory Store (unchanged) ===
memory = Memory()

# === Characters (unchanged) ===
characters = []

@app.on_event("startup")
async def startup_event():
    global characters
    print("Initializing characters...")
    characters = [
        create_bellamy(),
        create_holloway(),
        create_tommy(),
        create_perpetrator(),
    ]

@app.get("/characters")
async def get_characters():
    return [char.name for char in characters]

@app.get("/clues")
async def get_clues():
    return memory.get_clues()

@app.get("/murderer")
async def get_murderer_page():
    """Serve the murderer console page"""
    return FileResponse("gui/electron/murderer.html")

@app.post("/ask")
async def ask(request: Request):
    """
    HTTP path still works (single-player / Electron).
    In multiplayer, detective will use the socket 'ask' event instead.
    """
    data = await request.json()
    character_name = data.get("character")
    question = data.get("question")

    character = next((c for c in characters if c.name == character_name), None)
    if not character:
        return {"error": f"No character named {character_name}"}

    answer = await ask_character(character, question, memory)
    return {"response": answer}


# ============================
#   Socket.IO (Multiplayer)
# ============================

# Socket server mounted *around* FastAPI so both HTTP + WS work
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

# Minimal game state (single room v1)
GAME = {
    "room": "room1",
    "detective_sid": None,
    "murderer_sid": None,
    "human_character": None,  # e.g., "Mr. Holloway" (the one the human controls)
}
# correlation_id -> asyncio.Future for murderer replies
PENDING: dict[str, asyncio.Future] = {}

def find_character(name: str):
    return next((c for c in characters if c.name == name), None)

@sio.event
async def connect(sid, environ):
    print("Socket connected:", sid)
    print(f"Connection from: {environ.get('HTTP_USER_AGENT', 'Unknown')}")

@sio.event
async def disconnect(sid):
    print("Socket disconnected:", sid)
    if GAME.get("detective_sid") == sid:
        GAME["detective_sid"] = None
    if GAME.get("murderer_sid") == sid:
        GAME["murderer_sid"] = None

@sio.event
async def join_role(sid, data):
    """
    data: {"role": "detective" | "murderer"}
    """
    role = (data or {}).get("role")
    print(f"JOIN_ROLE: {sid} joining as {role}")
    await sio.save_session(sid, {"role": role})
    await sio.enter_room(sid, GAME["room"])
    if role == "detective":
        GAME["detective_sid"] = sid
        print(f"Detective connected: {sid}")
        await sio.emit("system", {"msg": "Detective joined."}, room=sid)
    elif role == "murderer":
        GAME["murderer_sid"] = sid
        print(f"Murderer connected: {sid}")
        await sio.emit("system", {"msg": "Murderer joined."}, room=sid)
    else:
        print(f"Unknown role: {role}")
        await sio.emit("error", {"msg": "Unknown role"}, room=sid)

@sio.event
async def set_human_character(sid, data):
    """
    Murderer picks which character they 'possess'
    data: {"character": "Mr. Holloway"}
    """
    if sid != GAME.get("murderer_sid"):
        return await sio.emit("error", {"msg": "Only murderer can set character."}, room=sid)

    name = (data or {}).get("character")
    if not find_character(name):
        return await sio.emit("error", {"msg": f"No character named {name}."}, room=sid)

    GAME["human_character"] = name
    # You might hide this broadcast in a real game; for now it's helpful
    await sio.emit("system", {"msg": f"Human now controls: {name}."}, room=GAME["room"])

@sio.event
async def ask(sid, data):
    """
    Detective asks a question (multiplayer path).
    data: {"character": "Mrs. Bellamy", "question": "Where were you?"}
    """
    print(f"ASK event from {sid}")
    print(f"Detective SID: {GAME.get('detective_sid')}")
    print(f"Murderer SID: {GAME.get('murderer_sid')}")
    print(f"Human character: {GAME.get('human_character')}")
    
    if sid != GAME.get("detective_sid"):
        print(f"ERROR: {sid} is not detective")
        return await sio.emit("error", {"msg": "Only detective can ask."}, room=sid)

    character = (data or {}).get("character")
    question = ((data or {}).get("question") or "").strip()
    if not character or not question:
        return await sio.emit("error", {"msg": "Missing character or question."}, room=sid)

    print(f"Question for {character}: {question}")

    # If human controls this character, forward to murderer and await reply
    if GAME.get("human_character") == character and GAME.get("murderer_sid"):
        print(f"Forwarding to human murderer for {character}")
        corr_id = uuid.uuid4().hex
        fut = asyncio.get_event_loop().create_future()
        PENDING[corr_id] = fut
        await sio.emit(
            "question_for_murderer",
            {"correlation_id": corr_id, "character": character, "question": question},
            room=GAME["murderer_sid"],
        )
        try:
            answer = await asyncio.wait_for(fut, timeout=40)
        except asyncio.TimeoutError:
            # fallback to AI if murderer is silent
            print("Timeout, falling back to AI")
            agent = find_character(character)
            answer = await ask_character(agent, question, memory)
        finally:
            PENDING.pop(corr_id, None)
    else:
        # AI handles it
        print(f"Using AI for {character}")
        agent = find_character(character)
        answer = await ask_character(agent, question, memory)

    # Send answer back to detective
    if GAME.get("detective_sid"):
        await sio.emit("answer", {"character": character, "answer": answer}, room=GAME["detective_sid"])

    # Tell clients to refresh clues (your GUI will still call GET /clues)
    await sio.emit("clues_updated", {}, room=GAME["room"])

@sio.event
async def murderer_answer(sid, data):
    """
    Murderer replies to a pending question.
    data: {"correlation_id": "...", "answer": "text"}
    """
    if sid != GAME.get("murderer_sid"):
        return await sio.emit("error", {"msg": "Only murderer can answer."}, room=sid)
    corr_id = (data or {}).get("correlation_id")
    ans = ((data or {}).get("answer") or "").strip()
    fut = PENDING.get(corr_id)
    if fut and not fut.done():
        fut.set_result(ans)
