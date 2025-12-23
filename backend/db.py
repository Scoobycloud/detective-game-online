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
        res = (
            supabase.table("rooms")
            .insert(
                {
                    "code": code,
                    "status": "open",
                }
            )
            .execute()
        )
        data = getattr(res, "data", None)
        return True, f"inserted:{len(data) if data is not None else 'unknown'}"
    except Exception as e:
        print("DB create_room warning:", e)
        return False, str(e)


def delete_room(code: str) -> Tuple[bool, Optional[str]]:
    """Delete a room and all associated data from all tables."""
    if not supabase:
        return False, "supabase_not_configured"
    try:
        # Delete from all related tables (order matters for foreign keys)
        tables = [
            "transcript",
            "clues",
            "evidence",
            "timeline_events",
            "relationships",
            "alibis",
            "case_characters",
            "cases",
            "room_members",
            "rooms",
        ]
        deleted_counts = {}
        for table in tables:
            try:
                res = supabase.table(table).delete().eq("room_code", code).execute()
                data = getattr(res, "data", None)
                deleted_counts[table] = len(data) if data else 0
            except Exception as e:
                # Some tables might use 'code' instead of 'room_code'
                if table == "rooms":
                    try:
                        res = supabase.table(table).delete().eq("code", code).execute()
                        data = getattr(res, "data", None)
                        deleted_counts[table] = len(data) if data else 0
                    except Exception as e2:
                        deleted_counts[table] = f"error: {e2}"
                else:
                    deleted_counts[table] = f"error: {e}"
        return True, str(deleted_counts)
    except Exception as e:
        print("DB delete_room error:", e)
        return False, str(e)


def update_room_status(code: str, status: str) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("rooms")
            .update({"status": status})
            .eq("code", code)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB update_room_status warning:", e)
        return False, str(e)


def add_room_member(
    code: str, role: str, user_id: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("room_members")
            .insert(
                {
                    "room_code": code,
                    "role": role,
                    "user_id": user_id,
                }
            )
            .execute()
        )
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


def ensure_user(user_id: str, email: Optional[str]) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        # Upsert by user_id
        _ = (
            supabase.table("users")
            .upsert({"user_id": user_id, "email": email})
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB ensure_user warning:", e)
        return False, str(e)


def get_user_admin(user_id: str) -> Tuple[bool, Optional[bool]]:
    if not supabase:
        return False, None
    try:
        res = (
            supabase.table("users")
            .select("is_admin")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        if not rows:
            return True, None
        return True, bool(rows[0].get("is_admin"))
    except Exception as e:
        print("DB get_user_admin warning:", e)
        return False, None


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


# ==============================
# Rooms: names and listing
# ==============================


def set_room_name(code: str, name: str) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = supabase.table("rooms").update({"name": name}).eq("code", code).execute()
        return True, None
    except Exception as e:
        print("DB set_room_name warning:", e)
        return False, str(e)


def room_name_exists(name: str) -> bool:
    if not supabase:
        return False
    try:
        res = (
            supabase.table("rooms")
            .select("name")
            .ilike("name", name)
            .limit(1)
            .execute()
        )
        items = getattr(res, "data", []) or []
        return bool(items)
    except Exception as e:
        print("DB room_name_exists warning:", e)
        return False


def list_rooms() -> Tuple[bool, List[Dict[str, Any]]]:
    if not supabase:
        return False, []
    try:
        # Try selecting name if present, else fall back
        try:
            res = (
                supabase.table("rooms")
                .select("code,status,name,created_at")
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            return True, getattr(res, "data", []) or []
        except Exception:
            res2 = (
                supabase.table("rooms")
                .select("code,status,created_at")
                .order("created_at", desc=True)
                .limit(100)
                .execute()
            )
            rows = getattr(res2, "data", []) or []
            for r in rows:
                r.setdefault("name", None)
            return True, rows
    except Exception as e:
        print("DB list_rooms warning:", e)
        return False, []


def add_clue(
    room_code: str,
    text: str,
    clue_type: str,
    source: Optional[str] = None,
    timestamp: Optional[str] = None,
    character_name: Optional[str] = None,
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
        if character_name:
            payload["character_name"] = character_name
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
            .select("text,type,source,timestamp,created_at,character_name")
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


def upsert_case(
    room_code: str, status: str, seed: str, summary: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("cases")
            .upsert(
                {
                    "room_code": room_code,
                    "status": status,
                    "seed": seed,
                    "summary": summary,
                }
            )
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB upsert_case warning:", e)
        return False, str(e)


def update_case_status(room_code: str, status: str) -> Tuple[bool, Optional[str]]:
    """Update only the status field of a case for a room."""
    if not supabase:
        return False, "supabase_not_configured"
    try:
        _ = (
            supabase.table("cases")
            .update({"status": status})
            .eq("room_code", room_code)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB update_case_status warning:", e)
        return False, str(e)


def upsert_case_character(
    room_code: str,
    name: str,
    role: str,
    personality: Optional[Dict[str, Any]] = None,
    knowledge_scope: Optional[Dict[str, Any]] = None,
    knowledge: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        payload = {
            "room_code": room_code,
            "name": name,
            "role": role,
            "personality": personality,
            "knowledge_scope": knowledge_scope,
        }
        if knowledge is not None:
            payload["knowledge"] = knowledge
        res = (
            supabase.table("case_characters")
            .upsert(payload)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB upsert_case_character warning:", e)
        return False, str(e)


def insert_evidence(
    room_code: str,
    title: str,
    ev_type: str,
    location: Optional[str],
    notes: Optional[str] = None,
    is_discovered: bool = False,
    thumbnail_url: Optional[str] = None,
    media_url: Optional[str] = None,
    character_name: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        payload: Dict[str, Any] = {
            "room_code": room_code,
            "title": title,
            "type": ev_type,
            "location": location,
            "is_discovered": is_discovered,
            "notes": notes,
        }
        if thumbnail_url is not None:
            payload["thumbnail_url"] = thumbnail_url
        if media_url is not None:
            payload["media_url"] = media_url
        if character_name is not None:
            payload["character_name"] = character_name
        res = supabase.table("evidence").insert(payload).execute()
        return True, None
    except Exception as e:
        print("DB insert_evidence warning:", e)
        return False, str(e)


def insert_timeline_event(
    room_code: str, tstamp: str, phase: str, label: str, details: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("timeline_events")
            .insert(
                {
                    "room_code": room_code,
                    "tstamp": tstamp,
                    "phase": phase,
                    "label": label,
                    "details": details,
                }
            )
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_timeline_event warning:", e)
        return False, str(e)


def insert_relationship(
    room_code: str, a: str, b: str, kind: str, notes: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("relationships")
            .insert(
                {
                    "room_code": room_code,
                    "a": a,
                    "b": b,
                    "kind": kind,
                    "notes": notes,
                }
            )
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB insert_relationship warning:", e)
        return False, str(e)


def insert_alibi(
    room_code: str,
    character: str,
    timeframe: str,
    account: str,
    credibility_score: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    if not supabase:
        return False, "supabase_not_configured"
    try:
        res = (
            supabase.table("alibis")
            .insert(
                {
                    "room_code": room_code,
                    "character": character,
                    "timeframe": timeframe,
                    "account": account,
                    "credibility_score": credibility_score,
                }
            )
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
        case_res = (
            supabase.table("cases")
            .select("room_code, status, seed, summary")
            .eq("room_code", room_code)
            .limit(1)
            .execute()
        )
        case_rows = getattr(case_res, "data", []) or []
        if not case_rows:
            return True, None
        framework: Dict[str, Any] = {"case": case_rows[0]}
        chars_res = (
            supabase.table("case_characters")
            .select("name, role, personality")
            .eq("room_code", room_code)
            .execute()
        )
        framework["characters"] = getattr(chars_res, "data", []) or []
        return True, framework
    except Exception as e:
        print("DB get_case_framework warning:", e)
        return False, None


def get_case_character(
    room_code: str, name: str
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not supabase:
        return False, None
    try:
        res = (
            supabase.table("case_characters")
            .select("name, role, personality, knowledge_scope, knowledge")
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


def update_case_character_knowledge(
    room_code: str, name: str, knowledge: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Update the knowledge (background, location_hints, about) for a character."""
    if not supabase:
        return False, "supabase_not_configured"
    try:
        _ = (
            supabase.table("case_characters")
            .update({"knowledge": knowledge})
            .eq("room_code", room_code)
            .eq("name", name)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB update_case_character_knowledge warning:", e)
        return False, str(e)


def update_case_character_scope(
    room_code: str, name: str, knowledge_scope: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Update only the knowledge_scope for a character row."""
    if not supabase:
        return False, "supabase_not_configured"
    try:
        _ = (
            supabase.table("case_characters")
            .update({"knowledge_scope": knowledge_scope})
            .eq("room_code", room_code)
            .eq("name", name)
            .execute()
        )
        return True, None
    except Exception as e:
        print("DB update_case_character_scope warning:", e)
        return False, str(e)


def get_evidence_for_room(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("evidence")
            .select(
                "id,title,type,location,is_discovered,discovered_at,notes,created_at,thumbnail_url,media_url,character_name"
            )
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


def find_undiscovered_evidence_by_location(
    room_code: str, location_query: str
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Find undiscovered evidence matching any word in the search query."""
    if not supabase:
        return False, None
    try:
        # Get all undiscovered evidence for the room
        res = (
            supabase.table("evidence")
            .select(
                "id,title,type,location,is_discovered,notes,thumbnail_url,media_url"
            )
            .eq("room_code", room_code)
            .eq("is_discovered", False)
            .order("created_at", desc=False)
            .execute()
        )
        rows = getattr(res, "data", []) or []

        # Match if ANY word in the query matches the location
        query_words = [w.lower().strip() for w in location_query.split() if w.strip()]
        for row in rows:
            loc = (row.get("location") or "").lower()
            if any(word in loc for word in query_words):
                return True, row
        return True, None
    except Exception as e:
        print("DB find_undiscovered_evidence_by_location warning:", e)
        return False, None


def find_any_evidence_by_location(
    room_code: str, location_query: str
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Find any evidence (discovered or not) matching any word in the search query."""
    if not supabase:
        return False, None
    try:
        # Get all evidence for the room
        res = (
            supabase.table("evidence")
            .select(
                "id,title,type,location,is_discovered,notes,thumbnail_url,media_url"
            )
            .eq("room_code", room_code)
            .order("created_at", desc=False)
            .execute()
        )
        rows = getattr(res, "data", []) or []

        # Match if ANY word in the query matches the location
        query_words = [w.lower().strip() for w in location_query.split() if w.strip()]
        for row in rows:
            loc = (row.get("location") or "").lower()
            if any(word in loc for word in query_words):
                return True, row
        return True, None
    except Exception as e:
        print("DB find_any_evidence_by_location warning:", e)
        return False, None


def mark_evidence_discovered(
    room_code: str, evidence_id: str
) -> Tuple[bool, Optional[str]]:
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
    """Return list of credibility signals per character.

    Shape per item:
      {
        "character": str,
        "contradictions": int,              # number of CONTRADICTION clues linked to this character
        "avg_credibility": float | None     # average of alibis.credibility_score for this character
      }
    """
    if not supabase:
        return False, []
    try:
        # Gather alibi credibility averages per character
        alibi_res = (
            supabase.table("alibis")
            .select("character, credibility_score")
            .eq("room_code", room_code)
            .execute()
        )
        alibi_rows: List[Dict[str, Any]] = getattr(alibi_res, "data", []) or []
        cred_sum: Dict[str, float] = {}
        cred_count: Dict[str, int] = {}
        for r in alibi_rows:
            ch = (r.get("character") or "").strip()
            if not ch:
                continue
            cs = r.get("credibility_score")
            try:
                val = float(cs) if cs is not None else None
            except Exception:
                val = None
            if val is None:
                continue
            cred_sum[ch] = cred_sum.get(ch, 0.0) + val
            cred_count[ch] = cred_count.get(ch, 0) + 1

        # Gather contradictions per character via clues when linked
        clues_res = (
            supabase.table("clues")
            .select("character_name, type")
            .eq("room_code", room_code)
            .execute()
        )
        clues_rows: List[Dict[str, Any]] = getattr(clues_res, "data", []) or []
        contrad: Dict[str, int] = {}
        for r in clues_rows:
            if (r.get("type") or "").upper() != "CONTRADICTION":
                continue
            ch = (r.get("character_name") or "").strip()
            if not ch:
                continue
            contrad[ch] = contrad.get(ch, 0) + 1

        # Union of characters seen in either alibis or contradictions
        all_chars = set(cred_sum.keys()) | set(contrad.keys())
        items: List[Dict[str, Any]] = []
        for ch in sorted(all_chars):
            avg: Optional[float] = None
            if cred_count.get(ch):
                avg = cred_sum[ch] / float(cred_count[ch])
            items.append(
                {
                    "character": ch,
                    "contradictions": int(contrad.get(ch, 0)),
                    "avg_credibility": avg,
                }
            )
        return True, items
    except Exception as e:
        print("DB get_credibility_counts warning:", e)
        return False, []


def fix_evidence_extensions(room_code: Optional[str] = None) -> Tuple[bool, int]:
    """Replace .jpg extensions with .png in thumbnail/media URLs for existing rows.
    If room_code is provided, restrict to that room. Returns (ok, updated_count).
    """
    if not supabase:
        return False, 0
    try:
        sel = supabase.table("evidence").select("id,room_code,thumbnail_url,media_url")
        if room_code:
            sel = sel.eq("room_code", room_code)
        res = sel.execute()
        rows: List[Dict[str, Any]] = getattr(res, "data", []) or []
        updated = 0
        for r in rows:
            eid = r.get("id")
            t = r.get("thumbnail_url") or ""
            m = r.get("media_url") or ""
            new_t = t.replace(".jpg", ".png") if t.endswith(".jpg") else t
            new_m = m.replace(".jpg", ".png") if m.endswith(".jpg") else m
            if new_t != t or new_m != m:
                supabase.table("evidence").update(
                    {"thumbnail_url": new_t or None, "media_url": new_m or None}
                ).eq("id", eid).execute()
                updated += 1
        return True, updated
    except Exception as e:
        print("DB fix_evidence_extensions warning:", e)
        return False, 0


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


def get_case_characters_min(room_code: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """Return characters with personality for credibility hints."""
    if not supabase:
        return False, []
    try:
        res = (
            supabase.table("case_characters")
            .select("name, role, personality")
            .eq("room_code", room_code)
            .execute()
        )
        return True, getattr(res, "data", []) or []
    except Exception as e:
        print("DB get_case_characters_min warning:", e)
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
