-- Create 'test' column on rooms if it does not exist
alter table if exists public.rooms
  add column if not exists test text;
