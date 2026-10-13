# Unresolved lines persist with a null speaker

Since dialogue schema v3.0, a line whose speaker could not be determined is persisted with `characterId: null` instead of being defaulted to Narrator. The schema stays honest about what is known; the Narrator sentinel is reserved for lines explicitly attributed to it, and unknown lines render with the Unknown voice in audio.
