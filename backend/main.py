# backend/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from agents.profiles import create_bellamy, create_holloway, create_tommy, create_perpetrator
from logic.memory import Memory
from logic.qa import ask_character
import os
from dotenv import load_dotenv
import logging
import openai

# === NEW: sockets bits ===
import socketio
import asyncio
import uuid
import random
import string
from typing import Dict, Any, Optional
import inspect
# === Firebase Admin (optional) ===
try:
    import firebase_admin
    from firebase_admin import auth as fb_auth, credentials as fb_credentials
    _fb_app = None
    if not firebase_admin._apps:
        fb_creds_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if fb_creds_json:
            _fb_app = firebase_admin.initialize_app(fb_credentials.Certificate(json.loads(fb_creds_json)))
except Exception:
    firebase_admin = None  # type: ignore
    fb_auth = None  # type: ignore
    _fb_app = None

try:
    # Support both package and local run
    from .db import create_room as db_create_room, add_room_member as db_add_room_member
except Exception:
    from db import create_room as db_create_room, add_room_member as db_add_room_member
    try:
        from db import room_exists as db_room_exists
    except Exception:
        db_room_exists = None  # type: ignore
    try:
        from db import debug_status as db_debug_status
    except Exception:
        db_debug_status = None  # type: ignore

# === Load environment and API Key ===
load_dotenv()
log = logging.getLogger("uvicorn.error")
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

"""
Room model (in-memory, persisted optionally to DB later):
ROOMS = {
  "ABCD12": {
      "detective_sid": str | None,
      "murderer_sid": str | None,
      "human_character": str | None,
      "memory": Memory(),
  },
}
"""

memory = Memory()  # legacy single-player memory
ROOMS: Dict[str, Dict[str, Any]] = {}

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

@app.get("/rooms/{code}/clues")
async def get_room_clues(code: str):
    room = ROOMS.get(code)
    if not room:
        return {"error": "Room not found"}
    return room["memory"].get_clues()

@app.get("/debug/supabase")
async def debug_supabase():
    try:
        if 'db_debug_status' in globals() and db_debug_status:
            return db_debug_status()
        return {"configured": False}
    except Exception as e:
        return {"error": str(e)}

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
#   CI test change: no-op comment to trigger deploy
# ============================

# Socket server mounted *around* FastAPI so both HTTP + WS work
sio_debug = os.getenv("SIO_DEBUG", "1") == "1"
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=sio_debug,
    engineio_logger=sio_debug,
)
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

async def maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value

def generate_room_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))

# correlation_id -> asyncio.Future for murderer replies
PENDING: Dict[str, asyncio.Future] = {}
HUMAN_REPLY_TIMEOUT_SECONDS = int(os.getenv("HUMAN_REPLY_TIMEOUT_SECONDS", "120"))

# very simple matchmaking queues
WAITING: Dict[str, set[str]] = {
    "detective": set(),
    "murderer": set(),
}

def find_character(name: str):
    return next((c for c in characters if c.name == name), None)

def normalize_name(name: str | None) -> str:
    return (name or "").strip().lower()

@sio.event
async def connect(sid, environ):
    log.info(f"Socket connected: {sid}")
    log.info(f"Connection from: {environ.get('HTTP_USER_AGENT', 'Unknown')}")

@sio.event
async def disconnect(sid):
    log.info(f"Socket disconnected: {sid}")
    session = await maybe_await(sio.get_session(sid)) if hasattr(sio, "get_session") else {}
    room_code = (session or {}).get("room")
    role = (session or {}).get("role")
    if room_code and room_code in ROOMS:
        room = ROOMS[room_code]
        if role == "detective" and room.get("detective_sid") == sid:
            room["detective_sid"] = None
        if role == "murderer" and room.get("murderer_sid") == sid:
            room["murderer_sid"] = None
    # remove from matchmaking queues
    for r in ("detective", "murderer"):
        if sid in WAITING[r]:
            WAITING[r].discard(sid)

@sio.event
async def create_room(sid, data):
    """
    Create a new room and return the code.
    data: {"preferred_code"?: str}
    """
    preferred = (data or {}).get("preferred_code")
    code = preferred or generate_room_code()
    while code in ROOMS:
        code = generate_room_code()
    ROOMS[code] = {
        "detective_sid": None,
        "murderer_sid": None,
        "human_character": None,
        "memory": Memory(),
    }
    # Persist room creation (best-effort)
    try:
        ok, info = db_create_room(code)
        log.info(f"DB create_room ok={ok} info={info}")
    except Exception as e:
        log.info(f"DB create_room failed: {e}")
    log.info(f"ROOM CREATED {code}")
    await sio.emit("room_created", {"room": code}, room=sid)

@sio.event
async def join_role(sid, data):
    """
    data: {"role": "detective" | "murderer", "room": str}
    """
    role = (data or {}).get("role")
    room_code = (data or {}).get("room")
    id_token = (data or {}).get("idToken") or (data or {}).get("token")
    log.info(f"JOIN_ROLE: sid={sid} role={role} room={room_code}")
    if not role or not room_code:
        return await sio.emit("error", {"msg": "Missing role or room."}, room=sid)
    if room_code not in ROOMS:
        # Try to hydrate from DB (in case process restarted)
        hydrated = False
        try:
            if 'db_room_exists' in globals() and db_room_exists and db_room_exists(room_code):
                ROOMS[room_code] = {
                    "detective_sid": None,
                    "murderer_sid": None,
                    "human_character": None,
                    "memory": Memory(),
                }
                hydrated = True
                print(f"Hydrated room {room_code} from DB")
        except Exception as e:
            print("Room hydration failed:", e)
        if not hydrated:
            return await sio.emit("error", {"msg": "Room not found."}, room=sid)

    # Verify Firebase token if provided
    user_id: Optional[str] = None
    if id_token and fb_auth:
        try:
            decoded = fb_auth.verify_id_token(id_token)
            user_id = decoded.get("uid")
        except Exception as e:
            log.info(f"Firebase token verification failed: {e}")

    room = ROOMS[room_code]
    await maybe_await(sio.save_session(sid, {"role": role, "room": room_code, "user_id": user_id}))
    await maybe_await(sio.enter_room(sid, room_code))
    if role == "detective":
        room["detective_sid"] = sid
        log.info(f"Detective connected: {sid}")
        await sio.emit("system", {"msg": "Detective joined."}, room=sid)
        try:
            ok, info = db_add_room_member(room_code, "detective", user_id=user_id)
            log.info(f"DB add_room_member(det) ok={ok} info={info}")
        except Exception as e:
            log.info(f"DB add_room_member(det) failed: {e}")
    elif role == "murderer":
        room["murderer_sid"] = sid
        log.info(f"Murderer connected: {sid}")
        await sio.emit("system", {"msg": "Murderer joined."}, room=sid)
        try:
            ok, info = db_add_room_member(room_code, "murderer", user_id=user_id)
            log.info(f"DB add_room_member(mur) ok={ok} info={info}")
        except Exception as e:
            log.info(f"DB add_room_member(mur) failed: {e}")
    else:
        log.info(f"Unknown role: {role}")
        await sio.emit("error", {"msg": "Unknown role"}, room=sid)

@sio.event
async def queue_for_role(sid, data):
    """
    data: {"role": "detective" | "murderer"}
    On match, emit 'matched' {room}
    """
    role = (data or {}).get("role")
    if role not in ("detective", "murderer"):
        return await sio.emit("error", {"msg": "Invalid role for matchmaking."}, room=sid)
    counterpart = "murderer" if role == "detective" else "detective"
    # if someone is waiting on the other side, match immediately
    if WAITING[counterpart]:
        other_sid = next(iter(WAITING[counterpart]))
        WAITING[counterpart].discard(other_sid)
        code = generate_room_code()
        ROOMS[code] = {
            "detective_sid": None,
            "murderer_sid": None,
            "human_character": None,
            "memory": Memory(),
        }
        try:
            db_create_room(code)
        except Exception:
            pass
        await sio.emit("matched", {"room": code}, room=sid)
        await sio.emit("matched", {"room": code}, room=other_sid)
    else:
        WAITING[role].add(sid)
        await sio.emit("system", {"msg": f"Queued for {role} matchmaking."}, room=sid)

@sio.event
async def set_human_character(sid, data):
    """
    Murderer picks which character they 'possess'
    data: {"character": "Mr. Holloway"}
    """
    session = await maybe_await(sio.get_session(sid))
    room_code = session.get("room")
    if not room_code or room_code not in ROOMS:
        return await sio.emit("error", {"msg": "No room for session."}, room=sid)
    room = ROOMS[room_code]
    if sid != room.get("murderer_sid"):
        return await sio.emit("error", {"msg": "Only murderer can set character."}, room=sid)

    name = (data or {}).get("character")
    if not find_character(name):
        return await sio.emit("error", {"msg": f"No character named {name}."}, room=sid)

    log.info(f"SET_HUMAN_CHARACTER: room={room_code} sid={sid} name={name}")
    room["human_character"] = name
    # Confirm to murderer only
    await sio.emit("character_locked", {"character": name}, room=sid)
    # Optional broadcast (filtered client-side)
    await sio.emit("system", {"msg": f"Human now controls: {name}."}, room=room_code)

@sio.event
async def ask(sid, data):
    """
    Detective asks a question (multiplayer path).
    data: {"character": "Mrs. Bellamy", "question": "Where were you?"}
    """
    session = await maybe_await(sio.get_session(sid))
    room_code = session.get("room")
    if not room_code or room_code not in ROOMS:
        return await sio.emit("error", {"msg": "No room for session."}, room=sid)
    room = ROOMS[room_code]
    log.info(f"ASK event from {sid} in room {room_code}")
    log.info(f"Detective SID: {room.get('detective_sid')}")
    log.info(f"Murderer SID: {room.get('murderer_sid')}")
    log.info(f"Human character: {room.get('human_character')}")
    
    if sid != room.get("detective_sid"):
        print(f"ERROR: {sid} is not detective")
        return await sio.emit("error", {"msg": "Only detective can ask."}, room=sid)

    character = (data or {}).get("character")
    question = ((data or {}).get("question") or "").strip()
    if not character or not question:
        return await sio.emit("error", {"msg": "Missing character or question."}, room=sid)

    log.info(f"Question for {character}: {question}")
    log.info(
        {
            "routing_check": {
                "room_human": normalize_name(room.get("human_character")),
                "incoming": normalize_name(character),
                "match": normalize_name(room.get("human_character")) == normalize_name(character),
                "has_murderer": bool(room.get("murderer_sid")),
            }
        }
    )

    # If human controls this character, forward to murderer and await reply
    if normalize_name(room.get("human_character")) == normalize_name(character) and room.get("murderer_sid"):
        log.info(f"Forwarding to human murderer for {character}")
        corr_id = uuid.uuid4().hex
        fut = asyncio.get_event_loop().create_future()
        PENDING[corr_id] = fut
        await sio.emit(
            "question_for_murderer",
            {"correlation_id": corr_id, "character": character, "question": question},
            room=room["murderer_sid"],
        )
        try:
            answer = await asyncio.wait_for(fut, timeout=HUMAN_REPLY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            # fallback to AI if murderer is silent
            log.info("Timeout, falling back to AI")
            agent = find_character(character)
            answer = await ask_character(agent, question, room["memory"])
        finally:
            PENDING.pop(corr_id, None)
    else:
        # AI handles it
        log.info(f"Using AI for {character}")
        agent = find_character(character)
        answer = await ask_character(agent, question, room["memory"])

    # Send answer back to detective
    if room.get("detective_sid"):
        await sio.emit("answer", {"character": character, "answer": answer}, room=room["detective_sid"])

    # Tell clients to refresh clues (your GUI will still call GET /clues)
    await sio.emit("clues_updated", {}, room=room_code)

@sio.event
async def murderer_answer(sid, data):
    """
    Murderer replies to a pending question.
    data: {"correlation_id": "...", "answer": "text"}
    """
    session = await maybe_await(sio.get_session(sid))
    room_code = session.get("room")
    if not room_code or room_code not in ROOMS:
        return await sio.emit("error", {"msg": "No room for session."}, room=sid)
    room = ROOMS[room_code]
    if sid != room.get("murderer_sid"):
        return await sio.emit("error", {"msg": "Only murderer can answer."}, room=sid)
    corr_id = (data or {}).get("correlation_id")
    ans = ((data or {}).get("answer") or "").strip()
    fut = PENDING.get(corr_id)
    if fut and not fut.done():
        fut.set_result(ans)

@sio.event
async def murderer_ack(sid, data):
    """
    Optional debug ACK from murderer client when a question is received.
    data: {"correlation_id": "..."}
    """
    corr_id = (data or {}).get("correlation_id")
    log.info(f"MURDERER_ACK sid={sid} corr_id={corr_id}")

# ci: trigger render deploy
