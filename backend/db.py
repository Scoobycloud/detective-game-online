import os
from typing import Optional

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
        print("Supabase client initialized")
    except Exception as e:  # pragma: no cover
        print("Failed to init Supabase:", e)
        supabase = None
else:
    print("Supabase not configured (set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)")


def create_room(code: str) -> None:
    if not supabase:
        return
    try:
        supabase.table("rooms").insert({
            "code": code,
            "status": "open",
        }).execute()
    except Exception as e:
        # Likely conflict on unique code; ignore
        print("DB create_room warning:", e)


def update_room_status(code: str, status: str) -> None:
    if not supabase:
        return
    try:
        supabase.table("rooms").update({"status": status}).eq("code", code).execute()
    except Exception as e:
        print("DB update_room_status warning:", e)


def add_room_member(code: str, role: str, user_id: Optional[str] = None) -> None:
    if not supabase:
        return
    try:
        supabase.table("room_members").insert({
            "room_code": code,
            "role": role,
            "user_id": user_id,
        }).execute()
    except Exception as e:
        print("DB add_room_member warning:", e)


