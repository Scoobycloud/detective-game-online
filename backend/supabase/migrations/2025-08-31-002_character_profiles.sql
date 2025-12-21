-- Character Profiles: Police records and background info for suspects
-- Safe to run multiple times (IF NOT EXISTS guards)

-- Create character_profiles table
CREATE TABLE IF NOT EXISTS public.character_profiles (
    name text PRIMARY KEY,
    dob text,
    address text,
    image_url text,
    record text,
    created_at timestamptz DEFAULT now()
);

-- Add index for faster lookups
CREATE INDEX IF NOT EXISTS idx_character_profiles_name 
    ON public.character_profiles(name);

-- Insert default character profiles
INSERT INTO public.character_profiles (name, dob, address, image_url, record)
VALUES 
    (
        'Ms. Banana',
        '14 March 1958',
        '42 Maple Lane, Apartment 3B',
        '/images/characters/ms_banana.png',
        'No prior convictions. One caution for noise complaint dispute in 2019. Known to local police as a frequent caller reporting minor neighborhood disturbances.'
    ),
    (
        'Mr. Holloway',
        '7 September 1952',
        '38 Maple Lane',
        '/images/characters/mr_holloway.png',
        'Clean record. Former civil servant with 35 years of service. Filed 47 complaints with the council in the past 5 years regarding noise, litter, and parking violations.'
    ),
    (
        'Tommy the Janitor',
        '22 November 1985',
        '15 Birch Street, Flat 1',
        '/images/characters/tommy_the_janitor.png',
        'Minor theft conviction (shoplifting) in 2008, served community service. No incidents since. Employed as building janitor for 8 years with no complaints.'
    ),
    (
        'Dr. Adrian Blackwood',
        '3 June 1970',
        '12 Harley Gardens, Penthouse',
        '/images/characters/dr_adrian_blackwood.png',
        'No criminal record. Licensed surgeon since 1998. One malpractice complaint filed in 2021, later withdrawn. Member of the Royal College of Surgeons.'
    )
ON CONFLICT (name) DO UPDATE SET
    dob = EXCLUDED.dob,
    address = EXCLUDED.address,
    image_url = EXCLUDED.image_url,
    record = EXCLUDED.record;

