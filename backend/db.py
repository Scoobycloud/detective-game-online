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


# ==============================
# Case framework persistence
# ==============================

def upsert_case(room_code: str, status: str, seed: str, summary: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("cases")
            .upsert({
                "room_code": room_code,
                "status": status,
                "seed": seed,
                "summary": summary,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB upsert_case warning:", e)
        return False, str(e)


def upsert_case_character(room_code: str, name: str, role: str, personality: Optional[Dict[str, Any]] = None, knowledge_scope: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("case_characters")
            .upsert({
                "room_code": room_code,
                "name": name,
                "role": role,
                "personality": personality,
                "knowledge_scope": knowledge_scope,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB upsert_case_character warning:", e)
        return False, str(e)


def insert_evidence(room_code: str, title: str, ev_type: str, location: Optional[str], notes: Optional[str] = None, is_discovered: bool = False) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("evidence")
            .insert({
                "room_code": room_code,
                "title": title,
                "type": ev_type,
                "location": location,
                "is_discovered": is_discovered,
                "notes": notes,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_evidence warning:", e)
        return False, str(e)


def insert_timeline_event(room_code: str, tstamp: str, phase: str, label: str, details: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("timeline_events")
            .insert({
                "room_code": room_code,
                "tstamp": tstamp,
                "phase": phase,
                "label": label,
                "details": details,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_timeline_event warning:", e)
        return False, str(e)


def insert_relationship(room_code: str, a: str, b: str, kind: str, notes: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("relationships")
            .insert({
                "room_code": room_code,
                "a": a,
                "b": b,
                "kind": kind,
                "notes": notes,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_relationship warning:", e)
        return False, str(e)


def insert_alibi(room_code: str, character: str, timeframe: str, account: str, credibility_score: Optional[float] = None) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("alibis")
            .insert({
                "room_code": room_code,
                "character": character,
                "timeframe": timeframe,
                "account": account,
                "credibility_score": credibility_score,
            })
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_alibi warning:", e)
        return False, str(e)


def get_case_framework(room_code: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not supabase:
        return False, None
    try:
        case_res = supabase.table("cases").select("room_code, status, seed, summary").eq("room_code", room_code).limit(1).execute()
        case_rows = getattr(case_res, "data", []) or []
        if not case_rows:
            return True, None
        framework: Dict[str, Any] = {"case": case_rows[0]}
        chars_res = supabase.table("case_characters").select("name, role, personality").eq("room_code", room_code).execute()
        framework["characters"] = getattr(chars_res, "data", []) or []
        return True, framework
    except Exception as e:
        print("DB get_case_framework warning:", e)
        return False, None


def get_case_character(room_code: str, name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not supabase:
        return False, None
    try:
        res = (
            supabase.table("case_characters")
            .select("name, role, personality, knowledge_scope")
            .eq("room_code", room_code)
            .eq("name", name)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        return True, rows[0] if rows else None
    except Exception as e:
        print("DB get_case_character warning:", e)
        return False, None


def get_evidence_for_room(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("evidence")
            .select("id,title,type,location,is_discovered,discovered_at,notes,created_at")
            .eq("room_code", room_code)
            .order("created_at", desc=False)
            .execute()
        )
        return True, getattr(res, "data", []) or []
    except Exception as e:
        print("DB get_evidence_for_room warning:", e)
        return False, []


def get_timeline_for_room(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("timeline_events")
            .select("id,tstamp,phase,label,details,created_at")
            .eq("room_code", room_code)
            .order("created_at", desc=False)
            .execute()
        )
        return True, getattr(res, "data", []) or []
    except Exception as e:
        print("DB get_timeline_for_room warning:", e)
        return False, []


def find_undiscovered_evidence_by_location(room_code: str, location_query: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not supabase:
        return False, None
    try:
        res = (
            supabase.table("evidence")
            .select("id,title,type,location,is_discovered,notes")
            .eq("room_code", room_code)
            .eq("is_discovered", False)
            .ilike("location", f"%{location_query}%")
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        return True, rows[0] if rows else None
    except Exception as e:
        print("DB find_undiscovered_evidence_by_location warning:", e)
        return False, None


def mark_evidence_discovered(room_code: str, evidence_id: str) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("evidence")
            .update({"is_discovered": True, "discovered_at": "now()"})
            .eq("room_code", room_code)
            .eq("id", evidence_id)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB mark_evidence_discovered warning:", e)
        return False, str(e)


def get_credibility_counts(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """Return a list of {character, contradictions} by scanning contradiction evidence notes."""
    if not supabase:
        return False, []


def get_alibis_for_room(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("alibis")
            .select("id,character,timeframe,account,credibility_score,created_at")
            .eq("room_code", room_code)
            .order("created_at", desc=False)
            .execute()
        )
        return True, getattr(res, "data", []) or []
    except Exception as e:
        print("DB get_alibis_for_room warning:", e)
        return False, []
    try:
        res = (
            supabase.table("evidence")
            .select("notes")
            .eq("room_code", room_code)
            .eq("type", "contradiction")
            .execute()
        )
        rows = getattr(res, "data", []) or []
        counts: Dict[str, int] = {}
        for r in rows:
            notes = (r or {}).get("notes") or ""
            name = None
            # crude parse: look for 'character=Name' in notes
            for part in str(notes).split(";"):
                part = part.strip()
                if part.lower().startswith("character="):
                    name = part.split("=", 1)[1].strip()
                    break
            if name:
                counts[name] = counts.get(name, 0) + 1
        return True, [{"character": k, "contradictions": v} for k, v in counts.items()]
    except Exception as e:
        print("DB get_credibility_counts warning:", e)
        return False, []

