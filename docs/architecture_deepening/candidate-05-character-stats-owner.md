# Candidate 5 — One answer for character stats

**Status:** designed (decision record below) — not started
**Part of:** architecture review 2026-11-06, candidates 1–8
**Companion issue:** see `docs/architecture_deepening/README.md`
**Extends:** ADR-0012 (incremental character stats accumulation — the backend half is the best-shaped recent work; this candidate fixes the coupling *around* it)

## Problem

"How many lines does this character have?" has no single owner: **4 FE derivations** with subtly different semantics, **2 BE paths** with a duplicated join rule, a legacy full rebuild still alive beside the new incremental path, and a hidden side effect on root switching:

- FE: `stores/stats.ts` (counts the **deprecated** script format, keyed by `chosenSpeaker`, 56–62); `characterChapterStats.ts` `computeChapterLineCounts`; `bookCharacterRepository.ts` `collectDerivedStats` (whole-book from dialogue, 125–293); `routes/book/+page.svelte` fuses the sources ad hoc (`stats.totalLines ?? count`, 328–330) while some badges read a third field (`character.count`, 253, 762).
- BE: `update_character_stats.py` joins raw dialogue `characterId` keys to character records with a 4-branch case-insensitive id/name cascade, **duplicated near-verbatim** in `_update_folder_stats` (127–138) and `_update_legacy_stats` (199–219).
- `fs.ts` `triggerStatsUpdate` (1090–1124) still performs the pre-ADR-0012 **full rebuild** with a root re-sync, beside the incremental delta.
- `root_config_router.py` `set_book_root` (41–62) silently triggers `stats_accumulator.build` and **swallows its failure** — stats can be silently stale after a root switch, with no error surface on either side of the wire.

## Design

**The accumulated stats are the one answer.** ADR-0012's incremental accumulation (written to the character files by the backend) is canonical; FE re-derivation from raw dialogue/script is deprecated.

- **FE:** one module `characterStats.ts` — the single answer, sourced from the character records' accumulated stats, with one per-chapter count function from dialogue where a per-chapter view is needed. The four current sites (`stats.ts`, `characterChapterStats`, `collectDerivedStats`, `+page.svelte`) all call it.
- **BE:** the 4-branch join cascade extracted to **one function** in `update_character_stats.py`; both `_update_folder_stats` and `_update_legacy_stats` call it.
- **BE:** `set_book_root`'s stats-build effect becomes **visible** — the response carries the build outcome (`stats_rebuilt` status) and the failure is logged, not printed-and-swallowed. A build failure does **not** fail the root switch (kept), but it is no longer silent.
- **Deletion:** `fs.triggerStatsUpdate`'s legacy full rebuild (superseded by the explicit incremental delta — candidate 2's step 4 lands the explicit delta first; this candidate removes the old regime).

## Decisions

1. Canonical source = accumulated character stats (ADR-0012), not re-derivation. The deprecated-script-based derivation in `stats.ts` goes with the consolidation (script format is deprecated).
2. `+page.svelte`'s ad-hoc fusion is deleted — badges read the one module.
3. `set_book_root`: surface + log, never fail.
4. The full-rebuild path is deleted, not kept as an option.

## Fences (out of scope)

- The save-path stats *step* (when a delta fires) → candidate 2 (lands first; this candidate consumes its explicit step).
- `api_models` stats shapes → candidate 11.

## Tests

- BE: cascade extraction is behavior-preserving — existing `test_character_stats_accumulator.py` green; the new route test from candidate 2 covers the route level.
- FE: one module, one test — a seeded-book test asserting the module's answer (post-consolidation, parity is by construction; the pre-consolidation "do the 4 derivations agree" question no longer exists).

## Landing order (one commit each)

1. BE: extract the join cascade to one function (both callers switch).
2. BE: `set_book_root` — visible build outcome + logging.
3. FE: `characterStats.ts` module (canonical source + per-chapter count).
4. FE: rewire the four call sites.
5. Delete `fs.triggerStatsUpdate`'s full rebuild.
