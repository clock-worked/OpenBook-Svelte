class Character:
    """
    Represents a character in the story.
    """
    def __init__(self, name: str, voice_name: str = None, personality_notes: str = "", aliases: list = None, voice_accent: str = "en-GB", speed: float = 1.0):
        self.name = name
        self.voice_name = voice_name
        self.personality_notes = personality_notes
        self.aliases = aliases or []
        self.description = "" # Transient property for GUI use
        self.voice_accent = voice_accent
        self.speed = speed


