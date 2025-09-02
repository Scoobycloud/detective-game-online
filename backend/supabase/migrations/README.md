# Supabase Migrations

Place idempotent SQL files here. Example naming: 2025-08-19-rooms-name.sql.

Run order is filename-sorted. Each migration should be safe to re-run.

-- Planned: 2025-09-02-001_users.sql
-- Creates public.users(user_id text pk, email text, is_admin boolean default false, created_at timestamptz default now())
-- Adds index on lower(email)
