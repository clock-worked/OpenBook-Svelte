# Character identity: per-character files with short GUIDs

Character data v3.0 replaced the centralized `characters.json` with one file per alias cluster in the book's `characters/` folder, each identified by an 8-character Crockford base32 short GUID. Identity is decoupled from all names: a title rename touches exactly one file, and no reference in dialogue, rosters, voices, or manifests ever changes.

**Considered options**:

- Keep the centralized `characters.json` — the council's original "no storage change" consensus; the owner overrode it to buy rename-without-fan-out.
- Add an `_index.json` mapping GUID → file — rejected because "an index is a second source of truth"; resolution is a per-session folder scan, and all lookups go by GUID from file contents, never by parsing filenames.

**Consequences**:

- A title rename is the largest single write operation in the project (new file + old file deleted).
- NTFS case-insensitive collisions (e.g. `Bob.json`/`bob.json`) fall back to a GUID-named file.
- A book in mid-migration state (both the folder and `characters.json` present) aborts rather than silently picking a side; recovery is an idempotent re-open.
- An old app opening a v3 book may read dialogue but is hard-gated from mutating characters, preventing silent `characters.json` resurrection.
- Legacy `characters.json` is a read-only fallback; once `characters/` holds valid v3.0 files it must not be rewritten (invariant IN-2).
- A rename's old title becomes an alias (invariant IN-7: no name is ever lost).
