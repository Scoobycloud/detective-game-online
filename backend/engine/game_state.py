import json
from pathlib import Path

MEMORY_FILE = Path("backend/state/memory.json")
CLUES_FILE = Path("backend/state/clues.json")

def load_memory():
    return json.loads(MEMORY_FILE.read_text())

def update_memory(character, entry):
    memory = load_memory()
    memory.setdefault(character, []).append(entry)
    MEMORY_FILE.write_text(json.dumps(memory, indent=2))

def get_clues():
    return json.loads(CLUES_FILE.read_text())

def add_clue(clue):
    clues = get_clues()
    if clue not in clues:
        clues.append(clue)
        CLUES_FILE.write_text(json.dumps(clues, indent=2))
