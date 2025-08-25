# backend/main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from agents.profiles import (
    create_bellamy,
    create_holloway,
    create_tommy,
    create_perpetrator,
)
from logic.memory import Memory
from logic.qa import ask_character, extract_clues_from_reply
from logic.controller import answer_in_character, postprocess_human_answer
import os
from dotenv import load_dotenv
import logging
import openai
import json

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
            _fb_app = firebase_admin.initialize_app(
                fb_credentials.Certificate(json.loads(fb_creds_json))
            )
except Exception:
    firebase_admin = None  # type: ignore
    fb_auth = None  # type: ignore
    _fb_app = None

try:
    # Support both package and local run
    from .db import (
        create_room as db_create_room,
        add_room_member as db_add_room_member,
        add_transcript_entry as db_add_transcript_entry,
        add_clue as db_add_clue,
        get_clues_for_room as db_get_clues_for_room,
        get_character_profile as db_get_character_profile,
        upsert_case as db_upsert_case,
        upsert_case_character as db_upsert_case_character,
        insert_evidence as db_insert_evidence,
        insert_timeline_event as db_insert_timeline_event,
        insert_relationship as db_insert_relationship,
        insert_alibi as db_insert_alibi,
        get_case_framework as db_get_case_framework,
    )
except Exception:
    from db import (
        create_room as db_create_room,
        add_room_member as db_add_room_member,
        add_transcript_entry as db_add_transcript_entry,
        add_clue as db_add_clue,
        get_clues_for_room as db_get_clues_for_room,
        get_character_profile as db_get_character_profile,
        upsert_case as db_upsert_case,
        upsert_case_character as db_upsert_case_character,
        insert_evidence as db_insert_evidence,
        insert_timeline_event as db_insert_timeline_event,
        insert_relationship as db_insert_relationship,
        insert_alibi as db_insert_alibi,
        get_case_framework as db_get_case_framework,
    )

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


@app.get("/rooms/{code}/framework")
async def get_case_framework_http(code: str):
    try:
        if "db_get_case_framework" in globals() and db_get_case_framework:
            ok, data = db_get_case_framework(code)
            if not ok:
                return {"error": "db_unavailable"}
            return data or {"error": "not_found"}
    except Exception as e:
        return {"error": str(e)}
    return {"error": "not_configured"}


@app.get("/rooms/{code}/credibility")
async def get_credibility_http(code: str):
    try:
        from db import (
            get_credibility_counts as _get_cred,
            get_case_characters_min as _get_chars,
        )  # type: ignore
    except Exception:
        _get_cred = None  # type: ignore
        _get_chars = None  # type: ignore
    try:
        out: Dict[str, Any] = {"counts": [], "personality": []}
        if _get_cred:
            ok, items = _get_cred(code)
            if ok:
                out["counts"] = items
        if _get_chars:
            ok2, chars = _get_chars(code)
            if ok2:
                out["personality"] = chars
        return out
    except Exception as e:
        return {"error": str(e)}


@app.get("/rooms/{code}/evidence")
async def get_room_evidence_http(code: str):
    try:
        from db import get_evidence_for_room as _get_evidence_for_room  # type: ignore
    except Exception:
        _get_evidence_for_room = None  # type: ignore
    try:
        if _get_evidence_for_room:
            ok, items = _get_evidence_for_room(code)
            if not ok:
                return {"error": "db_unavailable"}
            return items
    except Exception as e:
        return {"error": str(e)}
    return []


@app.get("/rooms/{code}/timeline")
async def get_room_timeline_http(code: str):
    try:
        from db import get_timeline_for_room as _get_timeline_for_room  # type: ignore
    except Exception:
        _get_timeline_for_room = None  # type: ignore
    try:
        if _get_timeline_for_room:
            ok, items = _get_timeline_for_room(code)
            if not ok:
                return {"error": "db_unavailable"}
            return items
    except Exception as e:
        return {"error": str(e)}
    return []


@app.get("/rooms/{code}/alibis")
async def get_room_alibis_http(code: str):
    try:
        from db import get_alibis_for_room as _get_alibis_for_room  # type: ignore
    except Exception:
        _get_alibis_for_room = None  # type: ignore
    try:
        if _get_alibis_for_room:
            ok, items = _get_alibis_for_room(code)
            if not ok:
                return {"error": "db_unavailable"}
            return items
    except Exception as e:
        return {"error": str(e)}
    return []


@app.post("/rooms/{code}/search")
async def search_location_http(code: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    location = (data or {}).get("location") or ""
    if not location:
        return {"error": "missing_location"}
    try:
        from db import (
            find_undiscovered_evidence_by_location as _find,
            find_any_evidence_by_location as _find_any,
            mark_evidence_discovered as _mark,
        )
    except Exception:
        _find = None  # type: ignore
        _mark = None  # type: ignore
    try:
        if not _find or not _mark:
            return {"error": "db_unavailable"}
        ok, item = _find(code, location)
        if not ok:
            return {"error": "db_unavailable"}
        if not item:
            # fallback: if any match exists (already discovered), return it so UI can open modal
            any_item = None
            try:
                if "_find_any" in locals() and _find_any:
                    ok2, any_item = _find_any(code, location)
            except Exception:
                any_item = None
            if any_item:
                return {"found": True, "evidence": any_item}
            return {"found": False}
        # mark discovered
        _mark(code, item.get("id"))
        # notify sockets
        await sio.emit("evidence_updated", {}, room=code)
        return {"found": True, "evidence": item}
    except Exception as e:
        return {"error": str(e)}


@app.get("/characters/{name}/profile")
async def get_character_profile_http(name: str):
    try:
        if "db_get_character_profile" in globals() and db_get_character_profile:
            ok, profile = db_get_character_profile(name)
            if not ok:
                return {"error": "db_unavailable"}
            if not profile:
                return {"error": "not_found"}
            return profile
    except Exception as e:
        return {"error": str(e)}
    return {"error": "not_configured"}


@app.get("/clues")
async def get_clues():
    return memory.get_clues()


@app.get("/rooms/{code}/clues")
async def get_room_clues(code: str):
    # Prefer DB if available, fall back to in-memory
    try:
        if "db_get_clues_for_room" in globals() and db_get_clues_for_room:
            ok, items = db_get_clues_for_room(code)
            if ok and items:
                return items
    except Exception as e:
        print("/rooms/{code}/clues DB read failed:", e)
    room = ROOMS.get(code)
    if not room:
        return {"error": "Room not found"}
    return room["memory"].get_clues()


@app.get("/debug/supabase")
async def debug_supabase():
    try:
        if "db_debug_status" in globals() and db_debug_status:
            return db_debug_status()
        return {"configured": False}
    except Exception as e:
        return {"error": str(e)}


@app.get("/debug/rooms/{code}")
async def debug_get_room_http(code: str):
    return {"error": "disabled"}


@app.get("/debug/rooms_columns")
async def debug_rooms_columns():
    return {"error": "disabled"}


@app.post("/debug/fix_evidence_extensions")
async def debug_fix_evidence_extensions(code: Optional[str] = None):
    try:
        from db import fix_evidence_extensions as _fix  # type: ignore
    except Exception:
        _fix = None  # type: ignore
    try:
        if not _fix:
            return {"error": "db_unavailable"}
        ok, n = _fix(code)
        if not ok:
            return {"error": "update_failed"}
        return {"ok": True, "updated": n}
    except Exception as e:
        return {"error": str(e)}


@app.get("/murderer")
async def get_murderer_page():
    """Serve the murderer console page"""
    return FileResponse("gui/electron/murderer.html")


@app.get("/rooms")
async def list_rooms_http():
    try:
        from db import list_rooms as _list_rooms  # type: ignore
    except Exception:
        _list_rooms = None  # type: ignore
    try:
        if _list_rooms:
            ok, items = _list_rooms()
            if not ok:
                return {"error": "db_unavailable"}
            return items
    except Exception as e:
        return {"error": str(e)}
    return []


@app.get("/rooms/name_exists")
async def room_name_exists_http(name: str):
    try:
        from db import room_name_exists as _room_name_exists  # type: ignore
    except Exception:
        _room_name_exists = None  # type: ignore
    try:
        if _room_name_exists:
            return {"exists": _room_name_exists(name)}
    except Exception as e:
        return {"error": str(e)}
    return {"exists": False}


@app.get("/rooms/new_code")
async def get_new_room_code_http():
    # Generate a code that does not exist in memory or DB
    tries = 0
    while True:
        code = generate_room_code()
        if code not in ROOMS:
            exists_in_db = False
            try:
                if (
                    "db_room_exists" in globals()
                    and db_room_exists
                    and db_room_exists(code)
                ):
                    exists_in_db = True
            except Exception:
                exists_in_db = False
            if not exists_in_db:
                return {"code": code}
        tries += 1
        if tries > 20:
            return {"error": "unable_to_generate"}


@app.post("/rooms/{code}/name")
async def set_room_name_http(code: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    name = (data or {}).get("name") or ""
    if not name or len(name) < 4 or not name.isalnum():
        return {"error": "invalid_name"}
    try:
        from db import room_name_exists as _exists, set_room_name as _set  # type: ignore
    except Exception:
        _exists = None  # type: ignore
        _set = None  # type: ignore
    try:
        if not _exists or not _set:
            return {"error": "db_unavailable"}
        if _exists(name):
            return {"error": "name_taken"}
        ok, err = _set(code, name)
        if not ok:
            return {"error": err or "update_failed"}
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


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
    session = (
        await maybe_await(sio.get_session(sid)) if hasattr(sio, "get_session") else {}
    )
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

    # Generate a lightweight case framework (deterministic by code)
    try:
        seed = code
        summary = {
            "victim": "Mr. Whitaker",
            "murderer": "TBD",
            "motive": "Debt and resentment",
            "weapon": "Heavy candlestick",
            "location": "Whitestone Manor - Study",
            "time": "~9:00 PM",
        }
        db_upsert_case(code, status="open", seed=seed, summary=summary)
        # seed notable characters (names here align with default roster; roles illustrative)
        db_upsert_case_character(
            code,
            name="Mrs. Bellamy",
            role="witness",
            personality={"traits": ["poised", "observant"], "honesty": "honest"},
            knowledge_scope={
                "cannot": ["technical boiler details"],
                "allowed": ["social observations"],
            },
        )
        db_upsert_case_character(
            code,
            name="Mr. Holloway",
            role="suspect",
            personality={
                "traits": ["irritable", "defensive"],
                "honesty": "deceptive",
                "lie_about": ["debts", "argument with victim"],
            },
            knowledge_scope={
                "cannot": ["exact time at boiler"],
                "allowed": ["maintenance issues"],
            },
        )
        db_upsert_case_character(
            code,
            name="Tommy the Janitor",
            role="bystander",
            personality={
                "traits": ["nervous", "eager-to-please"],
                "honesty": "forgetful",
            },
            knowledge_scope={
                "cannot": ["exact times"],
                "allowed": ["places he cleaned"],
            },
        )
        db_upsert_case_character(
            code,
            name="Dr. Adrian Blackwood",
            role="suspect",
            personality={"traits": ["calm", "clinical"], "honesty": "honest"},
            knowledge_scope={
                "cannot": ["house staff routines"],
                "allowed": ["medical observations"],
            },
        )
        # first timeline marker
        db_insert_timeline_event(
            code,
            tstamp="21:00",
            phase="during",
            label="Murder timeframe",
            details="Victim last seen alive near 8:45 PM; sound reported near 9:00 PM",
        )
        # seed relationship example
        db_insert_relationship(
            code,
            "Mr. Holloway",
            "Mr. Whitaker",
            "debts",
            notes="Unpaid fees and public argument last week",
        )
        # seed undiscovered evidence example
        # Seed sample evidence with placeholder media paths under /evidence
        db_insert_evidence(
            code,
            title="syringe",
            ev_type="item",
            location="Bathroom cabinet",
            notes="Trace residue on needle",
            is_discovered=True,  # Temporarily set to true for testing
            thumbnail_url="/evidence/syringe_thumb.png",
            media_url="/evidence/syringe.png",
        )
        db_insert_evidence(
            code,
            title="park footage",
            ev_type="video",
            location="North Park gate",
            notes="Figure entering gate at 21:03",
            is_discovered=True,  # Temporarily set to true for testing
            thumbnail_url="/evidence/park_footage_thumb.png",
            media_url="/evidence/park_footage.mp4",
        )
        db_insert_evidence(
            code,
            title="hanky",
            ev_type="item",
            location="Study",
            notes="Monogrammed initial; faint stain",
            is_discovered=True,  # Temporarily set to true for testing
            thumbnail_url="/evidence/hanky_thumb.png",
            media_url="/evidence/hanky.png",
        )
        # seed basic alibis
        db_insert_alibi(
            code,
            character="Mrs. Bellamy",
            timeframe="20:45–21:15",
            account="Preparing tea in the sunroom; saw no one enter the study",
            credibility_score=0.6,
        )
        db_insert_alibi(
            code,
            character="Mr. Holloway",
            timeframe="20:50–21:10",
            account="Checking boiler in the basement",
            credibility_score=0.4,
        )
        db_insert_alibi(
            code,
            character="Tommy the Janitor",
            timeframe="20:40–21:20",
            account="Cleaning corridor near the north wing",
            credibility_score=0.5,
        )
        db_insert_alibi(
            code,
            character="Dr. Adrian Blackwood",
            timeframe="20:55–21:05",
            account="On a phone call in the courtyard",
            credibility_score=0.7,
        )
    except Exception as e:
        log.info(f"Case framework generation failed: {e}")
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
            if (
                "db_room_exists" in globals()
                and db_room_exists
                and db_room_exists(room_code)
            ):
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
    await maybe_await(
        sio.save_session(sid, {"role": role, "room": room_code, "user_id": user_id})
    )
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
        return await sio.emit(
            "error", {"msg": "Invalid role for matchmaking."}, room=sid
        )
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
        return await sio.emit(
            "error", {"msg": "Only murderer can set character."}, room=sid
        )

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
        return await sio.emit(
            "error", {"msg": "Missing character or question."}, room=sid
        )

    log.info(f"Question for {character}: {question}")
    log.info(
        {
            "routing_check": {
                "room_human": normalize_name(room.get("human_character")),
                "incoming": normalize_name(character),
                "match": normalize_name(room.get("human_character"))
                == normalize_name(character),
                "has_murderer": bool(room.get("murderer_sid")),
            }
        }
    )

    # Record question in transcript (best-effort)
    try:
        if "db_add_transcript_entry" in globals() and db_add_transcript_entry:
            db_add_transcript_entry(
                room_code, "Detective", question, character=character
            )
    except Exception as e:
        log.info(f"DB add_transcript_entry(question) failed: {e}")

    # Track clues before answering to compute delta
    before_len = len(room["memory"].get_clues())

    # If human controls this character, forward to murderer and await reply
    if normalize_name(room.get("human_character")) == normalize_name(
        character
    ) and room.get("murderer_sid"):
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
        # Extract clues from human reply and add to memory
        if answer:
            await postprocess_human_answer(
                room_code, character, answer, room["memory"]
            )  # best-effort
    else:
        # AI handles it
        log.info(f"Using AI for {character}")
        agent = find_character(character)
        # Route through controller with structured ops; fallback handled inside
        from logic.controller import generate_structured_answer, apply_ops

        answer, ops = await generate_structured_answer(
            room_code, agent, character, question, room["memory"]
        )  # type: ignore
        changes = apply_ops(
            room_code, character, ops, room["memory"]
        )  # persist any state changes

    # Send answer back to detective
    if room.get("detective_sid"):
        await sio.emit(
            "answer",
            {"character": character, "answer": answer},
            room=room["detective_sid"],
        )

    # Record answer in transcript (best-effort)
    try:
        if "db_add_transcript_entry" in globals() and db_add_transcript_entry:
            db_add_transcript_entry(room_code, character, answer, character=character)
    except Exception as e:
        log.info(f"DB add_transcript_entry(answer) failed: {e}")

    # Persist any new clues to DB
    try:
        if "db_add_clue" in globals() and db_add_clue:
            after_clues = room["memory"].get_clues()
            new_items = after_clues[before_len:]
            for c in new_items:
                db_add_clue(
                    room_code,
                    text=c.get("text", ""),
                    clue_type=c.get("type", "FACT"),
                    source=c.get("source"),
                    timestamp=c.get("timestamp"),
                )
    except Exception as e:
        log.info(f"DB add_clue batch failed: {e}")

    # Tell clients to refresh clues (your GUI will still call GET /clues)
    await sio.emit("clues_updated", {}, room=room_code)
    try:
        if "changes" in locals():
            if changes.get("evidence"):
                await sio.emit("evidence_updated", {}, room=room_code)
            if changes.get("timeline"):
                await sio.emit("timeline_updated", {}, room=room_code)
            if changes.get("alibis"):
                await sio.emit("alibis_updated", {}, room=room_code)
    except Exception:
        pass


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


@app.get("/debug/openai-status")
async def check_openai_status():
    """Check if OpenAI API key is configured."""
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"status": "error", "message": "OpenAI API key not configured"}

        import openai
        client = openai.AsyncOpenAI(api_key=api_key)

        # Test the API with a simple request
        response = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Say 'OpenAI is working'"}],
            max_tokens=10
        )

        return {
            "status": "ok",
            "message": "OpenAI API is configured and working",
            "response": response.choices[0].message.content.strip()
        }

    except Exception as e:
        return {"status": "error", "message": f"OpenAI test failed: {str(e)}"}


@app.post("/rooms/{code}/generate-game")
async def generate_game(code: str, request: Request):
    """Generate a complete murder mystery game from a narrative."""
    try:
        data = await request.json()
        narrative = data.get("narrative", "").strip()

        if not narrative:
            raise HTTPException(status_code=400, detail="Narrative text is required")

        if len(narrative) < 50:
            raise HTTPException(
                status_code=400, detail="Narrative must be at least 50 characters long"
            )

        # Check if room exists in database instead of just in-memory storage
        try:
            from db import get_evidence_for_room as _get_evidence_for_room
            if _get_evidence_for_room:
                ok, _ = _get_evidence_for_room(code)
                if not ok:
                    raise HTTPException(status_code=404, detail="Room not found")
        except Exception:
            # If database check fails, fall back to in-memory check
            if code not in ROOMS:
                raise HTTPException(status_code=404, detail="Room not found")

        # Import OpenAI for narrative processing
        try:
            import openai

            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                raise HTTPException(
                    status_code=500, detail="OpenAI API key not configured"
                )

            openai.api_key = openai_api_key

            # Create the AI prompt for narrative processing
            prompt = f"""You are an expert murder mystery game designer. Analyze this narrative and generate comprehensive game content for a detective game.

NARRATIVE: {narrative}

Please respond with a JSON object containing exactly these keys:
- characters: Array of character objects with name, role (victim/suspect/witness/housekeeper), and detailed backstory
- evidence: Array of evidence objects with title, type (item/document/video/witness_statement), location, detailed notes, and is_discovered (set to false for discovery gameplay)
- timeline_events: Array of timeline objects with tstamp (use format like "8:45 PM" or "2:00 PM"), phase (pre_crime/during_crime/post_discovery), label, and details
- clues: Array of clue objects with text, type (physical/forensic/witness/testimonial), and source (who found it or who provided the info)
- alibis: Array of alibi objects with character, timeframe, account (detailed description), and credibility_score (0-100, lower for suspicious alibis)

CRITICAL INSTRUCTIONS:
- Set ALL evidence is_discovered to false for proper gameplay
- Include the housekeeper as a witness character
- Make alibis detailed and some suspiciously weak
- Create clues that can be discovered through location searches
- Ensure timeline creates clear interrogation opportunities
- Add red herrings and false leads for engaging gameplay
- Generate 3-5 evidence items, 4-6 clues, 5-8 timeline events, and alibis for each suspect"""

            # Call OpenAI API using new client syntax
            client = openai.AsyncOpenAI(api_key=openai_api_key)

            response = await client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=2000,
            )

            ai_response = response.choices[0].message.content.strip()

            # Parse the JSON response
            try:
                game_data = json.loads(ai_response)
            except json.JSONDecodeError as e:
                log.error(f"Failed to parse AI response: {e}")
                raise HTTPException(
                    status_code=500, detail="Failed to parse AI response"
                )

            # Validate the response structure
            required_keys = [
                "characters",
                "evidence",
                "timeline_events",
                "clues",
                "alibis",
            ]
            if not all(key in game_data for key in required_keys):
                log.error(
                    f"AI response missing required keys: {list(game_data.keys())}"
                )
                raise HTTPException(
                    status_code=500, detail="AI response missing required game elements"
                )

            evidence_count = 0
            clues_count = 0

            # Insert characters (this would require a characters table)
            # For now, we'll focus on evidence, clues, timeline, and alibis

            # Insert evidence
            if game_data.get("evidence"):
                for item in game_data["evidence"]:
                    try:
                        from db import insert_evidence as _insert_evidence

                        if _insert_evidence:
                            ok, _ = _insert_evidence(
                                code,
                                title=item.get("title", "Unknown Evidence"),
                                ev_type=item.get("type", "item"),
                                location=item.get("location", "Unknown"),
                                notes=item.get("notes", ""),
                                is_discovered=item.get("is_discovered", False),
                            )
                            if ok:
                                evidence_count += 1
                    except Exception as e:
                        log.error(f"Failed to insert evidence: {e}")

            # Insert clues
            if game_data.get("clues"):
                for item in game_data["clues"]:
                    try:
                        from db import add_clue as _add_clue

                        if _add_clue:
                            ok, _ = _add_clue(
                                code,
                                item.get("text", ""),
                                item.get("type", "general"),
                                item.get("source", "unknown"),
                            )
                            if ok:
                                clues_count += 1
                    except Exception as e:
                        log.error(f"Failed to insert clue: {e}")

            # Insert timeline events
            if game_data.get("timeline_events"):
                for item in game_data["timeline_events"]:
                    try:
                        from db import insert_timeline_event as _insert_timeline

                        if _insert_timeline:
                            ok, _ = _insert_timeline(
                                code,
                                tstamp=item.get("tstamp", "Unknown time"),
                                phase=item.get("phase", "investigation"),
                                label=item.get("label", "Event"),
                                details=item.get("details", ""),
                            )
                    except Exception as e:
                        log.error(f"Failed to insert timeline event: {e}")

            # Insert alibis
            if game_data.get("alibis"):
                for item in game_data["alibis"]:
                    try:
                        from db import insert_alibi as _insert_alibi

                        if _insert_alibi:
                            ok, _ = _insert_alibi(
                                code,
                                character=item.get("character", "Unknown"),
                                timeframe=item.get("timeframe", "Unknown"),
                                account=item.get("account", ""),
                                credibility_score=75,  # Default credibility
                            )
                    except Exception as e:
                        log.error(f"Failed to insert alibi: {e}")

            # Emit socket events to notify clients
            await sio.emit("evidence_updated", {}, room=code)
            await sio.emit("timeline_updated", {}, room=code)
            await sio.emit("alibis_updated", {}, room=code)

            return {
                "success": True,
                "evidence_count": evidence_count,
                "clues_count": clues_count,
                "message": f"Game generated successfully with {evidence_count} evidence items and {clues_count} clues",
            }

        except ImportError:
            raise HTTPException(status_code=500, detail="OpenAI library not available")
        except openai.OpenAIError as e:
            log.error(f"OpenAI API error: {e}")
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")
        except json.JSONDecodeError as e:
            log.error(f"AI response parsing error: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to parse AI response: {str(e)}")
        except Exception as e:
            log.error(f"AI processing error: {e}")
            raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Unexpected error in generate_game: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ci: trigger render deploy
