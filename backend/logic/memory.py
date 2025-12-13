from datetime import datetime

class Memory:
    def __init__(self):
        self.entries = []
        self.clues = []

    def add(self, speaker, content):
        self.entries.append({"speaker": speaker, "content": content})

    def get(self):
        return self.entries

    def get_dialogue_for(self, participant_name: str):
        """
        Return only dialogue between the Detective and the specified participant.
        This prevents other witnesses' statements from leaking into prompts.
        """
        allowed = {"Detective", participant_name}
        return [e for e in self.entries if e.get("speaker") in allowed]

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
