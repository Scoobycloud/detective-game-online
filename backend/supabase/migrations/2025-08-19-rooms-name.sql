-- Add 'name' column to rooms if it does not exist
alter table if exists public.rooms
  add column if not exists name text;

-- Case-insensitive unique index on name
create unique index if not exists rooms_name_ci_unique
  on public.rooms (lower(name));

-- Helpful index for listing
create index if not exists rooms_created_at_idx
  on public.rooms (created_at);
