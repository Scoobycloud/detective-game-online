-- DB Foundations: indexes, uniqueness, and optional linking columns
-- Safe to run multiple times (IF NOT EXISTS guards)

-- 1) Room-scoped indexes for performance
CREATE INDEX IF NOT EXISTS idx_cases_room_code ON public.cases(room_code);
CREATE INDEX IF NOT EXISTS idx_case_characters_room_code ON public.case_characters(room_code);
CREATE INDEX IF NOT EXISTS idx_evidence_room_code ON public.evidence(room_code);
CREATE INDEX IF NOT EXISTS idx_clues_room_code ON public.clues(room_code);
CREATE INDEX IF NOT EXISTS idx_timeline_events_room_code ON public.timeline_events(room_code);
CREATE INDEX IF NOT EXISTS idx_alibis_room_code ON public.alibis(room_code);
CREATE INDEX IF NOT EXISTS idx_relationships_room_code ON public.relationships(room_code);
CREATE INDEX IF NOT EXISTS idx_room_members_room_code ON public.room_members(room_code);

-- 2) Ensure one character per name per room
CREATE UNIQUE INDEX IF NOT EXISTS uq_case_characters_room_name
  ON public.case_characters(room_code, name);

-- 3) Optional link columns for associating items to characters
ALTER TABLE public.evidence
  ADD COLUMN IF NOT EXISTS character_name text NULL;

ALTER TABLE public.clues
  ADD COLUMN IF NOT EXISTS character_name text NULL;

-- Optionally, lightweight FKs with NOT VALID to avoid locking large tables
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'evidence_character_fk'
  ) THEN
    ALTER TABLE public.evidence
      ADD CONSTRAINT evidence_character_fk
      FOREIGN KEY (room_code, character_name)
      REFERENCES public.case_characters(room_code, name)
      DEFERRABLE INITIALLY DEFERRED NOT VALID;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'clues_character_fk'
  ) THEN
    ALTER TABLE public.clues
      ADD CONSTRAINT clues_character_fk
      FOREIGN KEY (room_code, character_name)
      REFERENCES public.case_characters(room_code, name)
      DEFERRABLE INITIALLY DEFERRED NOT VALID;
  END IF;
END $$;

-- To validate later during a maintenance window:
-- ALTER TABLE public.evidence VALIDATE CONSTRAINT evidence_character_fk;
-- ALTER TABLE public.clues VALIDATE CONSTRAINT clues_character_fk;


