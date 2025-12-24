-- Timed clue release support
-- Adds scheduling metadata and indexes for staged clue reveals.

ALTER TABLE public.clues
  ADD COLUMN IF NOT EXISTS release_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS released boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS auto_generated boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS stage text NULL;

-- Backfill existing rows to a safe released state
UPDATE public.clues
  SET released = true
  WHERE released IS NULL;

-- Helpful index for due-clue scans
CREATE INDEX IF NOT EXISTS idx_clues_room_release
  ON public.clues(room_code, released, release_at);

