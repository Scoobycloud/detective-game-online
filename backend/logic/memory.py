from datetime import datetime

class Memory:
    def __init__(self):
        self.entries = []
        self.clues = []

    def add(self, speaker, content):
        self.entries.append({"speaker": speaker, "content": content})

    def get(self):
        return self.entries

    def add_clue(self, text, clue_type="FACT", source="Unknown", timestamp=None):
        if not timestamp:
            timestamp = datetime.now().isoformat()
        self.clues.append({
            "text": text,
            "type": clue_type,
            "source": source,
            "timestamp": timestamp
        })

    def get_clues(self):
        return self.clues
