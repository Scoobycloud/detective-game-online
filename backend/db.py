import os
from typing import Optional, Tuple, Dict, Any, List

try:
    from supabase import create_client, Client
except Exception:  # pragma: no cover
    create_client = None
    Client = None  # type: ignore


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

supabase: Optional["Client"] = None

if SUPABASE_URL and SUPABASE_KEY and create_client is not None:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)  # type: ignore
        print("Supabase client initialized", SUPABASE_URL)
    except Exception as e:  # pragma: no cover
        print("Failed to init Supabase:", e)
        supabase = None
else:
    print("Supabase not configured (set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)")


def create_room(code: str) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = supabase.table("rooms").insert({
            "code": code,
            "status": "open",
        }).execute()
        data = getattr(res, "data", None)
        return True, f"inserted:{len(data) if data is not None else 'unknown'}"
    except Exception as e:
        print("DB create_room warning:", e)
        return False, str(e)


def update_room_status(code: str, status: str) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = supabase.table("rooms").update({"status": status}).eq("code", code).execute()
        return True, None
    except Exception as e:
        print("DB update_room_status warning:", e)
        return False, str(e)


def add_room_member(code: str, role: str, user_id: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = supabase.table("room_members").insert({
            "room_code": code,
            "role": role,
            "user_id": user_id,
        }).execute()
        data = getattr(res, "data", None)
        return True, f"inserted:{len(data) if data is not None else 'unknown'}"
    except Exception as e:
        print("DB add_room_member warning:", e)
        return False, str(e)


def room_exists(code: str) -> bool:
    if not supabase:
        return False
    try:
        res = supabase.table("rooms").select("code").eq("code", code).limit(1).execute()
        items = getattr(res, "data", None) or getattr(res, "json", {}).get("data") or []
        return bool(items)
    except Exception as e:
        print("DB room_exists warning:", e)
        return False


def debug_status() -> Dict[str, Any]:
    conf = bool(SUPABASE_URL and SUPABASE_KEY and supabase is not None)
    can_read = False
    try:
        if supabase:
            res = supabase.table("rooms").select("code").limit(1).execute()
            items = getattr(res, "data", [])
            can_read = True if items is not None else False
    except Exception as e:
        print("DB debug_status warning:", e)
        can_read = False
    return {
        "configured": conf,
        "url": SUPABASE_URL,
        "can_read": can_read,
    }



# ==============================
# Optional: transcripts and clues
# ==============================

def add_transcript_entry(
    room_code: str,
    speaker: str,
    content: str,
    character: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Insert a transcript row if the table exists.
    Expected schema: transcript(id uuid pk, room_code text, speaker text, character text null, content text, correlation_id text null, created_at timestamp default now())
    """
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("transcript")
            .insert(
                {
                    "room_code": room_code,
                    "speaker": speaker,
                    "character": character,
                    "content": content,
                    "correlation_id": correlation_id,
                }
            )
            .execute()
        )
        data = getattr(res, "data", None)
        return True, f"inserted:{len(data) if data is not None else 'unknown'}"
    except Exception as e:
        print("DB add_transcript_entry warning:", e)
        return False, str(e)


def add_clue(
    room_code: str,
    text: str,
    clue_type: str,
    source: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Insert a clue row if the table exists.
    Expected schema: clues(id uuid pk, room_code text, text text, type text, source text, timestamp text, created_at timestamp default now())
    """
    if not supabase:
        return False, "supabase_not_configured"
    try:
        payload: Dict[str, Any] = {
            "room_code": room_code,
            "text": text,
            "type": clue_type,
            "source": source,
        }
        if timestamp:
            payload["timestamp"] = timestamp
        res = supabase.table("clues").insert(payload).execute()
        data = getattr(res, "data", None)
        return True, f"inserted:{len(data) if data is not None else 'unknown'}"
    except Exception as e:
        print("DB add_clue warning:", e)
        return False, str(e)


def get_clues_for_room(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """Fetch clues for a room; returns (ok, list)."""
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("clues")
            .select("text,type,source,timestamp,created_at")
            .eq("room_code", room_code)
            .order("created_at", desc=False)
            .execute()
        )
        data = getattr(res, "data", []) or []
        return True, data  # type: ignore
    except Exception as e:
        print("DB get_clues_for_room warning:", e)
        return False, []


def get_character_profile(name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Fetch a character's police profile from Supabase.
    Expected table: character_profiles(name text pk, dob text, address text, image_url text, record text)
    """
    if not supabase:
        return False, None
    try:
        res = (
            supabase.table("character_profiles")
            .select("name,dob,address,image_url,record")
            .ilike("name", name)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", []) or []
        if not data:
            return True, None
        return True, data[0]
    except Exception as e:
        print("DB get_character_profile warning:", e)
        return False, None

