-- Ensure 'test' column exists on public.rooms
begin;
  alter table if exists public.rooms
    add column if not exists test text;
commit;
