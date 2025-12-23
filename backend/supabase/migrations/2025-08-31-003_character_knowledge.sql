-- Add knowledge JSONB column to case_characters for storing background, location_hints, about
-- This consolidates all character knowledge in one place

ALTER TABLE public.case_characters
  ADD COLUMN IF NOT EXISTS knowledge jsonb DEFAULT '{}'::jsonb;

-- Example structure for knowledge:
-- {
--   "background": ["I am a retired schoolteacher.", "I was baking a pie at 9:00 PM."],
--   "location_hints": ["Check the Study.", "The bathroom cabinet has been left open."],
--   "about": {
--     "Mr. Holloway": ["Keeps a strict gardening routine.", "Complains about noise."],
--     "Tommy the Janitor": ["Often seen mopping between 8:45 and 9:30."]
--   }
-- }
-- knowledge_scope remains a separate column for allowed/cannot topics

