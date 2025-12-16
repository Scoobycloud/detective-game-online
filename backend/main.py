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
from datetime import datetime, timezone
from pathlib import Path

# === NEW: sockets bits ===
import socketio
import asyncio
import uuid
import random
import string
from typing import Dict, Any, Optional
import inspect
import time

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
        get_case_character as db_get_case_character,
        get_case_characters_min as db_get_case_characters_min,
        update_case_character_scope as db_update_case_character_scope,
        update_case_status as db_update_case_status,
        ensure_user as db_ensure_user,
        get_user_admin as db_get_user_admin,
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
        get_case_character as db_get_case_character,
        get_case_characters_min as db_get_case_characters_min,
        update_case_character_scope as db_update_case_character_scope,
        update_case_status as db_update_case_status,
        ensure_user as db_ensure_user,
        get_user_admin as db_get_user_admin,
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


# ===== Read APIs: Case and Characters =====
@app.get("/rooms/{code}/case")
async def get_room_case(code: str):
    try:
        ok, framework = db_get_case_framework(code)
        if not ok:
            raise HTTPException(status_code=500, detail="Database error")
        # framework may be None if no case yet
        meta = stage_meta(code)
        # compute remaining seconds best-effort
        remaining = None
        if meta.get("stage_end") and not meta.get("stage_paused"):
            remaining = max(0, int(meta["stage_end"] - time.time()))
        elif meta.get("stage_paused") and meta.get("stage_remaining") is not None:
            remaining = int(meta["stage_remaining"])
        return {
            "success": True,
            "case": framework.get("case") if framework else None,
            "stage": {
                "status": meta.get("stage"),
                "paused": meta.get("stage_paused"),
                "remaining_seconds": remaining,
                "durations": meta.get("durations"),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"GET /rooms/{code}/case failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch case")


@app.get("/rooms/{code}/characters")
async def get_room_characters(code: str):
    try:
        # Use minimal set with personality; falls back to empty list
        ok, rows = db_get_case_characters_min(code)
        if not ok:
            raise HTTPException(status_code=500, detail="Database error")
        return {"success": True, "characters": rows or []}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"GET /rooms/{code}/characters failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch characters")


@app.get("/auth/me")
async def auth_me(request: Request):
    # Expect optional Firebase ID token via Authorization: Bearer <token>
    try:
        auth_header = (
            request.headers.get("authorization")
            or request.headers.get("Authorization")
            or ""
        )
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        if not token or not fb_auth:
            return {"authenticated": False}
        decoded = fb_auth.verify_id_token(token)
        user_id = decoded.get("uid")
        email = decoded.get("email")
        is_admin = False
        try:
            ok, flag = db_get_user_admin(user_id)
            if ok and flag is not None:
                is_admin = bool(flag)
        except Exception:
            pass
        return {
            "authenticated": True,
            "user_id": user_id,
            "email": email,
            "is_admin": is_admin,
        }
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


@app.post("/rooms/{code}/status")
async def set_case_status_http(code: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    status = (body or {}).get("status")
    pause = (body or {}).get("pause")
    if pause is not None:
        pause_stage(code, bool(pause))
        return {"ok": True, "paused": bool(pause)}
    if not isinstance(status, str) or not status.strip():
        raise HTTPException(status_code=400, detail="Missing status")
    status = status.strip().lower()
    allowed = {"investigation", "interrogation", "accusation", "closed"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")
    try:
        ok, err = db_update_case_status(code, status)
        if not ok:
            raise HTTPException(status_code=500, detail=err or "Update failed")
        # restart timers from this stage
        start_stage_timers(code, status)
        return {"ok": True, "status": status}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"POST /rooms/{code}/status failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update status")


@app.post("/rooms/{code}/stage_config")
async def set_stage_config_http(code: str, request: Request):
    """Update stage durations (seconds) per room; restarts timer from current stage."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    def parse_duration(key: str) -> Optional[int]:
        val = body.get(key)
        if val is None:
            return None
        try:
            num = int(float(val))
            if num <= 0:
                raise ValueError()
            return num
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid duration for {key}")

    updates: Dict[str, int] = {}
    for k in ("investigation", "interrogation", "accusation"):
        v = parse_duration(k)
        if v is not None:
            updates[k] = v

    if not updates:
        raise HTTPException(status_code=400, detail="No durations provided")

    room = ROOMS.get(code)
    if not room:
        hydrated = False
        try:
            if (
                "db_room_exists" in globals()
                and db_room_exists
                and db_room_exists(code)
            ):
                ROOMS[code] = {
                    "detective_sid": None,
                    "murderer_sid": None,
                    "human_character": None,
                    "memory": Memory(),
                    "stage_durations": dict(STAGE_DURATIONS),
                }
                hydrated = True
                try:
                    ok_fw, framework = db_get_case_framework(code)
                    status = "investigation"
                    if ok_fw and framework and isinstance(framework.get("case"), dict):
                        status = framework["case"].get("status", "investigation")
                    start_stage_timers(code, status)
                except Exception as e:  # pragma: no cover - defensive
                    print(f"Failed to restart timers for hydrated room {code}: {e}")
        except Exception as e:  # pragma: no cover - defensive
            print("Room hydration failed:", e)
        if not hydrated:
            raise HTTPException(status_code=404, detail="Room not found")
        room = ROOMS.get(code)

    room["stage_durations"] = {**get_stage_durations(code), **updates}

    current_stage = room.get("stage", "investigation")
    durations = get_stage_durations(code)
    cur_dur = durations.get(current_stage) or 0

    if room.get("stage_paused"):
        remaining = room.get("stage_remaining") or cur_dur
        remaining = min(max(0, int(remaining)), cur_dur or int(remaining))
    else:
        start_ts = room.get("stage_started") or time.time()
        elapsed = max(0, time.time() - start_ts)
        remaining = max(0, int((cur_dur or 0) - elapsed))

    start_stage_timers(code, current_stage, remaining=remaining or None)
    asyncio.create_task(emit_stage_update(code))

    return {
        "ok": True,
        "durations": durations,
        "stage": current_stage,
        "remaining_seconds": remaining,
    }


@app.post("/rooms/{code}/summary")
async def set_case_summary_http(code: str, request: Request):
    """Update selected keys in cases.summary, preserving others."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    # Load existing summary
    ok, framework = db_get_case_framework(code)
    if not ok:
        raise HTTPException(status_code=500, detail="Database error")
    existing_summary = {}
    if framework and isinstance(framework.get("case"), dict):
        cur_case = framework.get("case") or {}
        cur_summary = cur_case.get("summary")
        if isinstance(cur_summary, dict):
            existing_summary = dict(cur_summary)
    # Allowed fields to update
    fields = ["victim", "motive", "weapon", "location", "time", "narrative"]
    merged = dict(existing_summary)
    for f in fields:
        if f in body and isinstance(body.get(f), (str, type(None))):
            val = body.get(f)
            if val is None:
                merged.pop(f, None)
            else:
                merged[f] = str(val)
    # Persist via upsert_case
    try:
        ok2, err = db_upsert_case(
            code,
            status=(framework.get("case", {}).get("status") or "open"),
            seed=code,
            summary=merged,
        )
        if not ok2:
            raise HTTPException(status_code=500, detail=err or "Update failed")
        return {"ok": True, "summary": merged}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"POST /rooms/{code}/summary failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update summary")


@app.post("/rooms/{code}/characters/{name}")
async def set_character_profile_http(code: str, name: str, request: Request):
    """Update character role/personality minimally via upsert."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    role = body.get("role")
    personality = body.get("personality")
    if role is not None and not isinstance(role, str):
        raise HTTPException(status_code=400, detail="role must be string")
    if personality is not None and not isinstance(personality, dict):
        raise HTTPException(status_code=400, detail="personality must be object")
    # Get existing to preserve
    ok, ch = db_get_case_character(code, name)
    if not ok:
        raise HTTPException(status_code=500, detail="Database error")
    current_role = ch.get("role") if ch else None
    current_personality = ch.get("personality") if ch else None
    try:
        ok2, err = db_upsert_case_character(
            room_code=code,
            name=name,
            role=str(role or current_role or "suspect"),
            personality=personality or current_personality or {},
            knowledge_scope=(ch.get("knowledge_scope") if ch else None),
        )
        if not ok2:
            raise HTTPException(status_code=500, detail=err or "Update failed")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"POST /rooms/{code}/characters/{name} failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update character")


@app.post("/rooms/{code}/accuse")
async def submit_accusation_http(code: str, request: Request):
    """Submit an accusation, record it in summary, and close the case."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    suspect = ((body or {}).get("suspect") or "").strip()
    rationale = ((body or {}).get("rationale") or "").strip()
    if not suspect:
        raise HTTPException(status_code=400, detail="Missing suspect")

    # Load framework for current summary and murderer (if any)
    ok, framework = db_get_case_framework(code)
    if not ok:
        raise HTTPException(status_code=500, detail="Database error")
    existing_summary = {}
    current_status = "open"
    murderer = None
    if framework and isinstance(framework.get("case"), dict):
        cur_case = framework.get("case") or {}
        current_status = cur_case.get("status") or current_status
        cur_summary = cur_case.get("summary")
        if isinstance(cur_summary, dict):
            existing_summary = dict(cur_summary)
            murderer = (cur_summary or {}).get("murderer")

    # Build accusation record
    now_iso = datetime.now(timezone.utc).isoformat()
    verdict = None
    if isinstance(murderer, str) and murderer.strip():
        verdict = (
            "correct" if murderer.strip().lower() == suspect.lower() else "incorrect"
        )
    accusation = {"suspect": suspect, "rationale": rationale, "at": now_iso}
    if verdict:
        accusation["verdict"] = verdict

    merged_summary = dict(existing_summary)
    merged_summary["accusation"] = accusation

    # Persist: set status to closed and update summary
    try:
        ok2, err = db_upsert_case(
            code, status="closed", seed=code, summary=merged_summary
        )
        if not ok2:
            raise HTTPException(status_code=500, detail=err or "Update failed")
        return {"ok": True, "status": "closed", "verdict": verdict}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"POST /rooms/{code}/accuse failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit accusation")


@app.get("/rooms/{code}/characters/{name}")
async def get_room_character_detail(code: str, name: str):
    try:
        ok, ch = db_get_case_character(code, name)
        if not ok:
            raise HTTPException(status_code=500, detail="Database error")
        if not ch:
            raise HTTPException(status_code=404, detail="Not found")
        return {"success": True, "character": ch}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"GET /rooms/{code}/characters/{name} failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch character")


@app.post("/rooms/{code}/characters/{name}/knowledge_scope")
async def set_room_character_scope(code: str, name: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    scope = body.get("knowledge_scope")
    if not isinstance(scope, dict):
        raise HTTPException(status_code=400, detail="knowledge_scope must be an object")

    # normalize lists to arrays of strings
    def _norm_list(val):
        if isinstance(val, list):
            out = []
            for x in val:
                try:
                    s = str(x).strip()
                except Exception:
                    s = ""
                if s:
                    out.append(s)
            return out
        return []

    normalized = {
        "allowed": _norm_list(scope.get("allowed", [])),
        "cannot": _norm_list(scope.get("cannot", [])),
    }
    try:
        ok, err = db_update_case_character_scope(code, name, normalized)
        if not ok:
            raise HTTPException(status_code=500, detail=err or "Update failed")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"POST /rooms/{code}/characters/{name}/knowledge_scope failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update knowledge_scope")


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


@app.get("/admin/knowledge")
async def admin_get_knowledge(request: Request):
    if not is_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return read_knowledge()


@app.put("/admin/knowledge")
async def admin_put_knowledge(request: Request):
    if not is_authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="invalid payload")
        ok = write_knowledge(data)
        if not ok:
            raise HTTPException(status_code=500, detail="write_failed")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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

# Auto stage flow and timers (seconds)
STAGE_FLOW = ["investigation", "interrogation", "accusation", "closed"]
STAGE_DURATIONS = {
    "investigation": 10 * 60,  # 10 mins
    "interrogation": 15 * 60,  # 15 mins
    "accusation": 5 * 60,  # 5 mins
}
STAGE_TASKS: Dict[str, asyncio.Task] = {}


def get_stage_durations(room_code: str) -> Dict[str, int]:
    room = ROOMS.get(room_code) or {}
    return dict(room.get("stage_durations") or STAGE_DURATIONS)


async def emit_stage_update(room_code: str):
    """Push current stage state to all clients in the room."""
    room = ROOMS.get(room_code)
    if not room:
        return
    paused = bool(room.get("stage_paused"))
    remaining = None
    if paused:
        remaining = int(room.get("stage_remaining") or 0)
    else:
        end = room.get("stage_end")
        if end:
            remaining = max(0, int(end - time.time()))
    durations = get_stage_durations(room_code)
    try:
        await sio.emit(
            "stage_update",
            {
                "stage": room.get("stage", "investigation"),
                "paused": paused,
                "remaining_seconds": remaining,
                "durations": durations,
            },
            room=room_code,
        )
    except Exception as e:
        log.info(f"stage_update emit failed for {room_code}: {e}")


async def _run_stage_timer(
    room_code: str, start_stage: str = "investigation", initial_remaining: Optional[float] = None
):
    try:
        if start_stage not in STAGE_FLOW:
            start_stage = "investigation"
        start_idx = STAGE_FLOW.index(start_stage)
        first_stage = True
        for stage in STAGE_FLOW[start_idx:]:
            try:
                db_update_case_status(room_code, stage)
            except Exception as e:
                log.info(f"Auto-stage update failed for {room_code} -> {stage}: {e}")
            duration = get_stage_durations(room_code).get(stage)
            if first_stage and initial_remaining is not None:
                duration = initial_remaining
            first_stage = False
            if not duration:
                break  # closed or no timer
            # set/refresh stage meta at stage start
            room = ROOMS.get(room_code)
            if not room:
                break
            room["stage"] = stage
            room["stage_paused"] = False
            room["stage_remaining"] = None
            room["stage_started"] = time.time()
            room["stage_end"] = room["stage_started"] + duration

            await emit_stage_update(room_code)

            end_ts = room["stage_end"]
            while True:
                room = ROOMS.get(room_code)
                if not room:
                    break
                if room.get("stage_paused"):
                    await asyncio.sleep(1)
                    end_ts = room.get("stage_end", end_ts)
                    continue
                now = time.time()
                if now >= end_ts:
                    break
                await asyncio.sleep(1)
            # advance to next stage
        # mark closed at the end of flow
        room = ROOMS.get(room_code)
        if room:
            room["stage"] = "closed"
            room["stage_paused"] = False
            room["stage_remaining"] = 0
            room["stage_started"] = None
            room["stage_end"] = None
            try:
                db_update_case_status(room_code, "closed")
            except Exception:
                pass
            await emit_stage_update(room_code)
    except asyncio.CancelledError:
        return


def start_stage_timers(
    room_code: str,
    initial_stage: str = "investigation",
    remaining: Optional[float] = None,
):
    prev = STAGE_TASKS.get(room_code)
    if prev and not prev.done():
        prev.cancel()
    room = ROOMS.get(room_code)
    if room:
        dur = get_stage_durations(room_code).get(initial_stage)
        if remaining is not None:
            dur = remaining
        if dur:
            room["stage"] = initial_stage
            room["stage_started"] = time.time()
            room["stage_end"] = room["stage_started"] + dur
            room["stage_paused"] = False
            room["stage_remaining"] = None
    task = asyncio.create_task(
        _run_stage_timer(room_code, initial_stage, initial_remaining=remaining)
    )
    STAGE_TASKS[room_code] = task
    asyncio.create_task(emit_stage_update(room_code))


def pause_stage(room_code: str, pause: bool):
    room = ROOMS.get(room_code)
    if not room:
        return
    if pause and not room.get("stage_paused"):
        remaining = max(0, (room.get("stage_end") or time.time()) - time.time())
        room["stage_paused"] = True
        room["stage_remaining"] = remaining
        asyncio.create_task(emit_stage_update(room_code))
    if not pause and room.get("stage_paused"):
        remaining = room.get("stage_remaining") or 0
        room["stage_paused"] = False
        room["stage_started"] = time.time()
        room["stage_end"] = room["stage_started"] + remaining
        room["stage_remaining"] = None
        start_stage_timers(
            room_code, room.get("stage", "investigation"), remaining=remaining
        )
        asyncio.create_task(emit_stage_update(room_code))


def stage_meta(room_code: str) -> Dict[str, Any]:
    room = ROOMS.get(room_code)
    if not room:
        return {}
    durations = get_stage_durations(room_code)
    return {
        "stage": room.get("stage", "investigation"),
        "stage_paused": bool(room.get("stage_paused")),
        "stage_end": room.get("stage_end"),
        "stage_started": room.get("stage_started"),
        "stage_remaining": room.get("stage_remaining"),
        "durations": durations,
    }


def find_character(name: str):
    return next((c for c in characters if c.name == name), None)


BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = BASE_DIR / "state" / "knowledge.json"


def read_knowledge() -> dict:
    try:
        if KNOWLEDGE_PATH.exists():
            return json.loads(KNOWLEDGE_PATH.read_text())
    except Exception as e:
        log.info(f"Failed reading knowledge: {e}")
    return {}


def write_knowledge(data: dict) -> bool:
    try:
        KNOWLEDGE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        log.info(f"Failed writing knowledge: {e}")
        return False


def is_authorized(request: Request) -> bool:
    """Authorize only Firebase admin users (Bearer token)."""
    hdr = request.headers.get("authorization", "") or request.headers.get(
        "Authorization", ""
    )
    bearer = ""
    if hdr.lower().startswith("bearer "):
        bearer = hdr.split(" ", 1)[1].strip()
    try:
        if fb_auth and bearer:
            decoded = fb_auth.verify_id_token(bearer)
            uid = decoded.get("uid")
            if uid and "db_get_user_admin" in globals() and db_get_user_admin:
                ok, flag = db_get_user_admin(uid)
                if ok and flag:
                    return True
    except Exception:
        pass
    return False


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
        "stage_durations": dict(STAGE_DURATIONS),
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
        db_upsert_case(code, status="investigation", seed=seed, summary=summary)
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
    start_stage_timers(code, "investigation")
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
                    "stage_durations": dict(STAGE_DURATIONS),
                }
                hydrated = True
                print(f"Hydrated room {room_code} from DB")
                # Restart stage timers based on stored case status (default investigation)
                try:
                    ok_fw, framework = db_get_case_framework(room_code)
                    status = "investigation"
                    if ok_fw and framework and isinstance(framework.get("case"), dict):
                        status = framework["case"].get("status", "investigation")
                    start_stage_timers(room_code, status)
                except Exception as e:
                    print(
                        f"Failed to restart timers for hydrated room {room_code}: {e}"
                    )
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
            # Ensure user exists in DB (best-effort)
            try:
                db_ensure_user(user_id, decoded.get("email"))
            except Exception:
                pass
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

        openai.api_key = api_key

        # Test the API with a simple request
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Say 'OpenAI is working'"}],
            max_tokens=10,
        )

        return {
            "status": "ok",
            "message": "OpenAI API is configured and working",
            "response": response.choices[0].message.content.strip(),
        }

    except Exception as e:
        return {"status": "error", "message": f"OpenAI test failed: {str(e)}"}


@app.post("/debug/test-narrative")
async def test_narrative_processing(request: Request):
    """Test narrative processing and return raw AI response."""
    try:
        data = await request.json()
        narrative = data.get("narrative", "").strip()

        if not narrative:
            raise HTTPException(status_code=400, detail="Narrative text is required")

        if len(narrative) < 10:
            raise HTTPException(
                status_code=400, detail="Narrative must be at least 10 characters long"
            )

        # Import OpenAI
        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        openai.api_key = api_key

        # Use the same prompt as the main endpoint
        prompt = f"""You are an expert murder mystery game designer. Analyze this narrative and generate comprehensive game content for a detective game.

NARRATIVE: {narrative}

Please respond with a JSON object containing exactly these keys:
- characters: Array of character objects with name, role (victim/suspect/witness/housekeeper), and detailed backstory
- evidence: Array of evidence objects with title, type (item/document/video/witness_statement), location, detailed notes, and is_discovered (set to false for discovery gameplay)
- timeline_events: Array of timeline objects with tstamp (use format like "8:45 PM" or "2:00 PM"), phase (pre_crime/during_crime/post_discovery), label, and details
- clues: Array of clue objects with text, type (IMPORTANT/CONTRADICTION), and source (who found it or who provided the info)
- alibis: Array of alibi objects with character, timeframe, account (detailed description), and credibility_score (0-100, lower for suspicious alibis)

CRITICAL INSTRUCTIONS:
- For clues: Use IMPORTANT for key facts, CONTRADICTION for conflicting statements
- Set ALL evidence is_discovered to false for proper gameplay
- Include the housekeeper as a witness character
- Make alibis detailed and some suspiciously weak
- Create clues that can be discovered through location searches
- Ensure timeline creates clear interrogation opportunities
- Add red herrings and false leads for engaging gameplay
- Generate 3-5 evidence items, 4-6 clues, 5-8 timeline events, and alibis for each suspect"""

        # Call OpenAI API
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
        )

        ai_response = response.choices[0].message.content.strip()

        return {
            "narrative": narrative,
            "ai_response": ai_response,
            "response_length": len(ai_response),
            "response_preview": ai_response[:500],
        }

    except Exception as e:
        return {"error": str(e), "error_type": type(e).__name__}


@app.post("/debug/test-database")
async def test_database_insertion():
    """Test database insertion functions directly."""
    try:
        # Test room creation
        from db import create_room as _create_room

        room_ok, room_result = _create_room("test_room_123")
        log.info(f"Room creation result: ok={room_ok}, result={room_result}")

        # Test evidence insertion
        from db import insert_evidence as _insert_evidence

        evidence_ok, evidence_result = _insert_evidence(
            "test_room_123",
            title="Test Knife",
            ev_type="item",
            location="Kitchen",
            notes="A bloody knife found at the scene",
            is_discovered=False,
        )
        log.info(
            f"Evidence insertion result: ok={evidence_ok}, result={evidence_result}"
        )

        # Test clue insertion
        from db import add_clue as _add_clue

        clue_ok, clue_result = _add_clue(
            "test_room_123",
            "The knife has fingerprints matching the butler",
            "IMPORTANT",
            "Crime Lab",
        )
        log.info(f"Clue insertion result: ok={clue_ok}, result={clue_result}")

        # Check if data was inserted
        from db import get_evidence_for_room as _get_evidence

        get_ok, evidence_list = _get_evidence("test_room_123")
        log.info(f"Evidence retrieval result: ok={get_ok}, count={len(evidence_list)}")

        return {
            "room_creation": {"ok": room_ok, "result": room_result},
            "evidence_insertion": {"ok": evidence_ok, "result": evidence_result},
            "clue_insertion": {"ok": clue_ok, "result": clue_result},
            "evidence_retrieval": {"ok": get_ok, "count": len(evidence_list)},
            "status": "Database functions tested",
        }

    except Exception as e:
        log.error(f"Database test error: {e}")
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "status": "Database test failed",
        }


@app.post("/rooms/{code}/create-structured-game")
async def create_structured_game(code: str, request: Request):
    """Create a complete murder mystery game from structured input data."""
    try:
        data = await request.json()
        narrative = data.get("narrative", "").strip()
        clues_text = data.get("clues", "").strip()
        evidence_text = data.get("evidence", "").strip()
        timeline_text = data.get("timeline", "").strip()
        alibis_text = data.get("alibis", "").strip()

        if not narrative:
            raise HTTPException(status_code=400, detail="Narrative story is required")

        if len(narrative) < 50:
            raise HTTPException(
                status_code=400, detail="Narrative must be at least 50 characters long"
            )

        # Ensure room exists in database (create if it doesn't)
        try:
            from db import create_room as _create_room
            from db import get_evidence_for_room as _get_evidence_for_room

            log.info(f"Game generation: Checking/creating room {code}")

            if _create_room and _get_evidence_for_room:
                # First try to get existing room data
                ok, items = _get_evidence_for_room(code)
                log.info(
                    f"Room {code} check result: ok={ok}, items_count={len(items) if items else 0}"
                )

                if not ok:
                    # Room doesn't exist, create it
                    log.info(f"Creating room {code}")
                    create_ok, create_info = _create_room(code)
                    log.info(
                        f"Room creation result: ok={create_ok}, info={create_info}"
                    )

                    if not create_ok:
                        raise HTTPException(
                            status_code=500, detail="Failed to create room"
                        )
                else:
                    log.info(f"Room {code} already exists")
            else:
                log.error("Database functions not available")
                raise HTTPException(status_code=500, detail="Database not available")

        except ImportError as e:
            log.error(f"Database import error: {e}")
            raise HTTPException(status_code=500, detail="Database module not found")
        except Exception as e:
            log.error(f"Room validation/creation error: {e}")
            raise HTTPException(status_code=500, detail="Database error")

        # Process structured input data directly (no AI needed)
        log.info(f"Processing structured game data for room {code}")

        # Persist the narrative/story into cases.summary for this room (preserve existing fields)
        try:
            # Load current case to merge summaries
            cur_ok, framework = db_get_case_framework(code)
            existing_summary = {}
            if cur_ok and framework and isinstance(framework.get("case"), dict):
                cur_case = framework.get("case") or {}
                cur_summary = cur_case.get("summary")
                if isinstance(cur_summary, dict):
                    existing_summary = dict(cur_summary)

            merged_summary = dict(existing_summary)
            merged_summary["narrative"] = narrative

            ok_case, err = db_upsert_case(
                code, status="open", seed=code, summary=merged_summary
            )
            if not ok_case:
                log.error(f"Failed to upsert case narrative for {code}: {err}")
            else:
                log.info(
                    f"Narrative stored/merged in cases.summary for room {code} (preserved keys: {list(existing_summary.keys())})"
                )
        except Exception as e:
            log.error(f"Error storing narrative for room {code}: {e}")

        # --- Smart merge preload: gather existing items to avoid duplicates ---
        evidence_duplicates = 0
        clues_duplicates = 0
        timeline_duplicates = 0
        alibis_duplicates = 0

        existing_evidence_keys = set()
        existing_clue_keys = set()
        existing_timeline_keys = set()
        existing_alibi_keys = set()

        def _norm(value):
            return (value or "").strip().lower()

        try:
            from db import (
                get_evidence_for_room as _get_evidence_all,
                get_clues_for_room as _get_clues_all,
                get_timeline_for_room as _get_timeline_all,
                get_alibis_for_room as _get_alibis_all,
            )

            # Existing evidence
            try:
                ok_ev, ev_items = _get_evidence_all(code)
                if ok_ev and ev_items:
                    for r in ev_items:
                        existing_evidence_keys.add(
                            (
                                _norm(r.get("title")),
                                _norm(r.get("type")),
                                _norm(r.get("location")),
                            )
                        )
            except Exception as e:
                log.error(
                    f"Smart merge: failed to load existing evidence for {code}: {e}"
                )

            # Existing clues
            try:
                ok_cl, cl_items = _get_clues_all(code)
                if ok_cl and cl_items:
                    for r in cl_items:
                        existing_clue_keys.add(
                            (
                                _norm(r.get("text")),
                                _norm(r.get("type")),
                                _norm(r.get("source")),
                            )
                        )
            except Exception as e:
                log.error(f"Smart merge: failed to load existing clues for {code}: {e}")

            # Existing timeline
            try:
                ok_tl, tl_items = _get_timeline_all(code)
                if ok_tl and tl_items:
                    for r in tl_items:
                        existing_timeline_keys.add(
                            (
                                _norm(r.get("tstamp")),
                                _norm(r.get("phase")),
                                _norm(r.get("label")),
                                _norm(r.get("details")),
                            )
                        )
            except Exception as e:
                log.error(
                    f"Smart merge: failed to load existing timeline for {code}: {e}"
                )

            # Existing alibis
            try:
                ok_al, al_items = _get_alibis_all(code)
                if ok_al and al_items:
                    for r in al_items:
                        existing_alibi_keys.add(
                            (
                                _norm(r.get("character")),
                                _norm(r.get("timeframe")),
                                _norm(r.get("account")),
                            )
                        )
            except Exception as e:
                log.error(
                    f"Smart merge: failed to load existing alibis for {code}: {e}"
                )

        except Exception as e:
            log.error(f"Smart merge preload failed: {e}")

        evidence_count = 0
        clues_count = 0
        timeline_count = 0
        alibis_count = 0

        # Process Evidence Items (pipe-delimited format)
        if evidence_text:
            log.info(f"Processing evidence text: {len(evidence_text)} chars")
            evidence_lines = [
                line.strip() for line in evidence_text.split("\n") if line.strip()
            ]

            for i, line in enumerate(evidence_lines):
                try:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        title, ev_type, location, notes = (
                            parts[0],
                            parts[1],
                            parts[2],
                            parts[3],
                        )
                        # Optional 5th part: character_name link
                        character_name = (
                            parts[4] if len(parts) >= 5 and parts[4] else None
                        )

                        # Smart merge: skip duplicates
                        ev_key = (_norm(title), _norm(ev_type), _norm(location))
                        if ev_key in existing_evidence_keys:
                            evidence_duplicates += 1
                            log.info(f"Skipped duplicate evidence: {title}")
                            continue

                        from db import insert_evidence as _insert_evidence

                        if _insert_evidence:
                            ok, result = _insert_evidence(
                                code,
                                title=title,
                                ev_type=ev_type,
                                location=location,
                                notes=notes,
                                is_discovered=False,
                                character_name=character_name,
                            )
                            if ok:
                                evidence_count += 1
                                log.info(f"Inserted evidence: {title}")
                                existing_evidence_keys.add(ev_key)
                    else:
                        log.warning(
                            f"Evidence line {i + 1} has insufficient parts ({len(parts)}): {line}"
                        )
                except Exception as e:
                    log.error(f"Failed to parse evidence line {i + 1}: {line} - {e}")
        else:
            log.info("No evidence text provided - skipping evidence creation")

        # Process Clues (pipe-delimited format)
        if clues_text:
            log.info(f"Processing clues text: {len(clues_text)} chars")
            clue_lines = [
                line.strip() for line in clues_text.split("\n") if line.strip()
            ]

            for i, line in enumerate(clue_lines):
                try:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        text, clue_type, source = parts[0], parts[1], parts[2]
                        # Optional 4th part: character_name link
                        character_name = (
                            parts[3] if len(parts) >= 4 and parts[3] else None
                        )

                        # Validate clue type
                        if clue_type not in ["IMPORTANT", "CONTRADICTION"]:
                            clue_type = "IMPORTANT"  # Default to IMPORTANT

                        # Smart merge: skip duplicates
                        clue_key = (_norm(text), _norm(clue_type), _norm(source))
                        if clue_key in existing_clue_keys:
                            clues_duplicates += 1
                            log.info(f"Skipped duplicate clue: {text[:50]}...")
                            continue

                        from db import add_clue as _add_clue

                        if _add_clue:
                            ok, result = _add_clue(
                                code, text, clue_type, source, None, character_name
                            )
                            if ok:
                                clues_count += 1
                                log.info(f"Inserted clue: {text[:50]}...")
                                existing_clue_keys.add(clue_key)
                    else:
                        log.warning(
                            f"Clue line {i + 1} has insufficient parts ({len(parts)}): {line}"
                        )
                except Exception as e:
                    log.error(f"Failed to parse clue line {i + 1}: {line} - {e}")
        else:
            log.info("No clues text provided - skipping clues creation")

        # Process Timeline Events (pipe-delimited format)
        if timeline_text:
            log.info(f"Processing timeline text: {len(timeline_text)} chars")
            timeline_lines = [
                line.strip() for line in timeline_text.split("\n") if line.strip()
            ]

            for i, line in enumerate(timeline_lines):
                try:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        tstamp, phase, label, details = (
                            parts[0],
                            parts[1],
                            parts[2],
                            parts[3],
                        )

                        # Smart merge: skip duplicates
                        tl_key = (
                            _norm(tstamp),
                            _norm(phase),
                            _norm(label),
                            _norm(details),
                        )
                        if tl_key in existing_timeline_keys:
                            timeline_duplicates += 1
                            log.info(f"Skipped duplicate timeline: {label}")
                            continue

                        from db import insert_timeline_event as _insert_timeline

                        if _insert_timeline:
                            ok, result = _insert_timeline(
                                code, tstamp, phase, label, details
                            )
                            if ok:
                                timeline_count += 1
                                log.info(f"Inserted timeline: {label}")
                                existing_timeline_keys.add(tl_key)
                    else:
                        log.warning(
                            f"Timeline line {i + 1} has insufficient parts ({len(parts)}): {line}"
                        )
                except Exception as e:
                    log.error(f"Failed to parse timeline line {i + 1}: {line} - {e}")
        else:
            log.info("No timeline text provided - skipping timeline creation")

        # Process Alibis (pipe-delimited format)
        if alibis_text:
            log.info(f"Processing alibis text: {len(alibis_text)} chars")
            alibi_lines = [
                line.strip() for line in alibis_text.split("\n") if line.strip()
            ]

            for i, line in enumerate(alibi_lines):
                try:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        character, timeframe, account, credibility = (
                            parts[0],
                            parts[1],
                            parts[2],
                            parts[3],
                        )

                        # Convert credibility to int
                        try:
                            credibility_score = int(credibility)
                        except ValueError:
                            credibility_score = 75  # Default credibility

                        # Smart merge: skip duplicates
                        al_key = (
                            _norm(character),
                            _norm(timeframe),
                            _norm(account),
                        )
                        if al_key in existing_alibi_keys:
                            alibis_duplicates += 1
                            log.info(f"Skipped duplicate alibi for: {character}")
                            continue

                        from db import insert_alibi as _insert_alibi

                        if _insert_alibi:
                            ok, result = _insert_alibi(
                                code, character, timeframe, account, credibility_score
                            )
                            if ok:
                                alibis_count += 1
                                log.info(f"Inserted alibi for: {character}")
                                existing_alibi_keys.add(al_key)
                    else:
                        log.warning(
                            f"Alibi line {i + 1} has insufficient parts ({len(parts)}): {line}"
                        )
                except Exception as e:
                    log.error(f"Failed to parse alibi line {i + 1}: {line} - {e}")
        else:
            log.info("No alibis text provided - skipping alibis creation")

        # Emit socket events to notify clients
        await sio.emit("evidence_updated", {}, room=code)
        await sio.emit("timeline_updated", {}, room=code)
        await sio.emit("alibis_updated", {}, room=code)

        # Create summary of what was processed
        sections_processed = []
        if evidence_text:
            sections_processed.append(f"{evidence_count} evidence items")
        if clues_text:
            sections_processed.append(f"{clues_count} clues")
        if timeline_text:
            sections_processed.append(f"{timeline_count} timeline events")
        if alibis_text:
            sections_processed.append(f"{alibis_count} alibis")

        sections_skipped = []
        if not evidence_text:
            sections_skipped.append("evidence")
        if not clues_text:
            sections_skipped.append("clues")
        if not timeline_text:
            sections_skipped.append("timeline")
        if not alibis_text:
            sections_skipped.append("alibis")

        log.info(
            f"Game creation completed. Final counts - Evidence: {evidence_count}, Clues: {clues_count}, Timeline: {timeline_count}, Alibis: {alibis_count}"
        )

        if sections_skipped:
            log.info(f"Sections skipped (empty): {', '.join(sections_skipped)}")

        summary_message = f"Game created with {', '.join(sections_processed) if sections_processed else 'narrative only'}"
        if sections_skipped:
            summary_message += f". Skipped: {', '.join(sections_skipped)}"

        return {
            "success": True,
            "evidence_count": evidence_count,
            "clues_count": clues_count,
            "timeline_count": timeline_count,
            "alibis_count": alibis_count,
            "sections_processed": sections_processed,
            "duplicates_skipped": {
                "evidence": evidence_duplicates,
                "clues": clues_duplicates,
                "timeline": timeline_duplicates,
                "alibis": alibis_duplicates,
            },
            "sections_skipped": sections_skipped,
            "message": summary_message,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Unexpected error in create_structured_game: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Keep the old AI-based endpoint for backward compatibility
@app.post("/rooms/{code}/generate-game")
async def generate_game_ai(code: str, request: Request):
    """Generate a complete murder mystery game from a narrative using AI."""
    return {
        "error": "AI generation temporarily disabled. Use structured input instead."
    }


# ci: trigger render deploy - force redeployment
