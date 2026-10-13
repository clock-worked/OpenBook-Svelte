# VibeVoice stays an integration boundary

VibeVoice is an external TTS runtime and is deliberately quarantined behind its own integration router (adapter), not folded into core app logic. This keeps the parser and audio core free to evolve independently of it, and contains the blast radius of any VibeVoice change to one boundary module.
