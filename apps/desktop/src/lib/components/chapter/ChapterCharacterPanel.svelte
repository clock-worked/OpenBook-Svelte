<script lang="ts">
  import type { CharactersJson, Character, Gender, ScriptJson } from '$lib/types';
  import { writable, get } from 'svelte/store';
  import { currentChapter, bookRoot, bookRootAbsolutePath, currentScript, chapters, requestChapterReload } from '$lib/stores/bookState';
  import { selection, conflictCursor } from '$lib/stores/selection';
  import { characters, colorForCharacter, rgbaToOpaqueHex, opaqueHexToRgba, refreshChapterCharactersFromFile } from '$lib/stores/characters';
  import { getScriptPath, readFileAsJson, writeScript, getCharactersPath } from '$lib/services/fs';
  import { loadChapterCharactersData, persistChapterCharactersData, type PersistChapterCharactersOptions } from '$lib/services/chapterCharacterRepository';
  // removed generateChapterAudio per UI change
  import { defaultColors } from '$lib/theme/colors';
  import { Plus, ArrowUpDown, Menu } from 'lucide-svelte';
  import ChapterAiAssistPanel from './ChapterAiAssistPanel.svelte';
  import ChapterLocalAiPanel from './ChapterLocalAiPanel.svelte';
  import ChapterJevPanel from './ChapterJevPanel.svelte';
  import ChapterCharacterList from './ChapterCharacterList.svelte';
  import ChapterCharacterBookList from './ChapterCharacterBookList.svelte';
  import CharacterDetails from './CharacterDetails.svelte';
  import ClosedWorldCharacterReview from './ClosedWorldCharacterReview.svelte';
  import Dropdown from '$lib/components/common/Dropdown.svelte';
  import { addBookCharacter, addBookCharacterAlias, upsertBookCharacter, renameBookCharacter, removeBookCharacter, bookCharacters, detachBookCharacterAlias, setBookCharacterDescriptors, forceRefreshBookCharacters, mergeBookCharacters } from '$lib/stores/bookCharacters';
  import { historyAction } from '$lib/services/history';
  import { apiFetch, API_ENDPOINTS } from '$lib/services/apiClient';
  import { buildClosedWorldReviewItems, planClosedWorldDescriptorAssignments, resolveClosedWorldCandidateAsAlias, type ClosedWorldReviewItem } from '$lib/services/closedWorldCharacterWorkflow';
  import { computeChapterLineCounts } from '$lib/services/characterChapterStats';
  import { normalizeCharacterKey } from '$lib/services/characterDomain';
  import {
    dialogueAiAssistState,
    dialogueAiAssistVisibleResults,
  } from '$lib/stores/dialogueAiAssist';
  import {
    localDialogueAiState,
    localDialogueAiVisibleResults,
  } from '$lib/stores/localDialogueAi';
  import {
    jevDialogueAiState,
    jevDialogueAiVisibleResults,
  } from '$lib/stores/jevDialogueAi';

  const DISPLAY_ALPHA = 1.0; // Use solid colors for visibility

  const selectionActive = writable<boolean>(false);
  // removed master tab; panel only shows chapter characters

  let openColorIndex: number | null = null;
  let newCharacterName: string = '';
  let newCharacterColor: string | null = null;
  
  // Track last scrolled line for each character to enable "next" functionality
  const lastScrolledLine = new Map<string, number>();
  
  type SortMode = 'name' | 'lines' | 'color';
  type ChapterPanelTab = 'characters' | 'ai' | 'local-ai' | 'jev';

  let sortMode: SortMode = 'name';
  let activeTab: ChapterPanelTab = 'characters';
  let compactTabs = false;
  $: panelTabs = [
    { value: 'characters' as const, label: 'Characters', hasAlert: false },
    { value: 'ai' as const, label: 'AI Assist', hasAlert: $dialogueAiAssistState.running || $dialogueAiAssistVisibleResults.length > 0 },
    { value: 'local-ai' as const, label: 'Local AI', hasAlert: $localDialogueAiState.running || $localDialogueAiVisibleResults.length > 0 },
    { value: 'jev' as const, label: 'JEV', hasAlert: $jevDialogueAiState.running || $jevDialogueAiVisibleResults.length > 0 },
  ];
  $: selectedTab = panelTabs.find((tab) => tab.value === activeTab)!;

  function observePanelWidth(node: HTMLDivElement) {
    const updateWidth = () => {
      compactTabs = node.getBoundingClientRect().width < 450;
    };
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(node, { box: 'border-box' });
    return { destroy: () => observer.disconnect() };
  }

  let bookListOpen = false;
  // Unified selection: fed by BOTH the book pill list and the chapter row
  // click; the details card derives everything from this single variable.
  let selectedCharacterName: string | null = null;
  let _selectionChapterKey: string | null = null;
  let lastAiRunning = false;
  let lastLocalAiRunning = false;
  let lastJevAiRunning = false;

  $: if ($dialogueAiAssistState.running && !lastAiRunning) {
    activeTab = 'ai';
  }

  $: lastAiRunning = $dialogueAiAssistState.running;

  $: if ($localDialogueAiState.running && !lastLocalAiRunning) {
    activeTab = 'local-ai';
  }

  $: lastLocalAiRunning = $localDialogueAiState.running;

  $: if ($jevDialogueAiState.running && !lastJevAiRunning) {
    activeTab = 'jev';
  }

  $: lastJevAiRunning = $jevDialogueAiState.running;

  $: closedWorldReviewItems = buildClosedWorldReviewItems(
    (($currentScript?.lines ?? []) as any),
    $bookCharacters,
  );

  const addReviewedCharacter = historyAction('Create and assign character', addReviewedCharacterImpl);
  const mergeReviewedAlias = historyAction('Confirm character alias', mergeReviewedAliasImpl);
  const mergeReviewedDescriptor = historyAction('Confirm character descriptor', mergeReviewedDescriptorImpl);
  const deleteBookCharacter = historyAction('Delete and reassign character', deleteBookCharacterImpl);
  const mergeCharacters = historyAction('Merge characters', mergeCharactersImpl);
  const addCharacter = historyAction('Create character', addCharacterImpl);
  const createAndApplyNewCharacter = historyAction('Create and assign character', createAndApplyNewCharacterImpl);

  async function addReviewedCharacterImpl(item: ClosedWorldReviewItem, name: string, gender: Gender): Promise<void> {
    const created = await addBookCharacter(name, gender);
    if (!created) return;
    await applyReviewedCharacter(item, created.id, created.name);
  }

  async function mergeReviewedAliasImpl(item: ClosedWorldReviewItem, targetCharacterId: string): Promise<void> {
    const target = $bookCharacters.characters.find((character) => character.id === targetCharacterId);
    if (!target) return;
    const root = get(bookRoot);
    if (!root) return;
    const resolved = resolveClosedWorldCandidateAsAlias($bookCharacters, item.candidateName, targetCharacterId);
    if (resolved.changed) {
      // v3 (IN-2/IN-3): persist ONLY the touched character's file via the
      // store's per-file write — never the root characters.json.
      const saved = await addBookCharacterAlias(target.name, item.candidateName);
      if (!saved) throw new Error(`Failed to add ${item.candidateName} as an alias of ${target.name}`);
    }
    await applyReviewedCharacter(item, target.id, target.name);
  }

  async function markReviewedAsNonSpeaker(item: ClosedWorldReviewItem): Promise<void> {
    await applyReviewedCharacter(item, null, null, true);
  }

  async function mergeReviewedDescriptorImpl(item: ClosedWorldReviewItem, lineTargets: Record<number, string>): Promise<void> {
    if (!get(bookRoot)) return;
    const itemLineIds = new Set(item.lineIds);
    const scopedTargets = Object.fromEntries(
      Object.entries(lineTargets).filter(([lineId]) => itemLineIds.has(Number(lineId))),
    );
    const groups = planClosedWorldDescriptorAssignments(get(bookCharacters), item.candidateName, scopedTargets);
    if (!groups.length) return;
    for (const group of groups) {
      if (!group.addDescriptor) continue;
      // Re-read each time: every save refreshes the store.
      const target = get(bookCharacters).characters.find((character) => character.id === group.characterId);
      if (!target) throw new Error(`Character ${group.characterName} was not found`);
      const descriptors = Array.isArray(target.descriptors) ? target.descriptors : [];
      const saved = await setBookCharacterDescriptors(target.name, [...descriptors, item.candidateName]);
      if (!saved) throw new Error(`Failed to add ${item.candidateName} as a descriptor of ${target.name}`);
    }
    const assignments = new Map<number, ReviewedLineAssignment>();
    for (const group of groups) {
      for (const lineId of group.lineIds) {
        assignments.set(lineId, { characterId: group.characterId, characterName: group.characterName });
      }
    }
    await applyReviewedLines(assignments);
  }

  function jumpToReviewLine(lineId: number): void {
    // Reset first so re-jumping to the same line still scrolls.
    conflictCursor.set(null);
    conflictCursor.set(lineId);
  }

  type ReviewedLineAssignment = { characterId: string | null; characterName: string | null; nonSpeaker?: boolean };

  async function applyReviewedCharacter(item: ClosedWorldReviewItem, characterId: string | null, characterName: string | null, nonSpeaker = false): Promise<void> {
    await applyReviewedLines(new Map(item.lineIds.map((lineId) => [lineId, { characterId, characterName, nonSpeaker }])));
  }

  async function applyReviewedLines(assignments: Map<number, ReviewedLineAssignment>): Promise<void> {
    const scr = get(currentScript);
    const ch: any = get(currentChapter);
    const root = get(bookRoot);
    if (!scr || !ch || !root) return;
    let changed = false;
    for (const line of scr.lines) {
      const assignment = assignments.get(line.id);
      if (!assignment) continue;
      const { characterId, characterName } = assignment;
      const nonSpeaker = assignment.nonSpeaker ?? false;
      (line as any).characterId = nonSpeaker ? null : characterId;
      line.chosenSpeaker = characterName;
      line.isConflict = false;
      (line as any).isNonSpeaker = nonSpeaker;
      if (line.attribution) {
        line.attribution.resolutionStatus = 'user_confirmed';
        line.attribution.candidates = nonSpeaker ? [] : [{
          characterId,
          name: characterName as string,
          confidence: 1,
          reasons: ['user_confirmed_review']
        }];
      }
      changed = true;
    }
    if (!changed) return;
    const scriptPath = ch.scriptPath ?? getScriptPath(ch.title);
    let saved = await writeScript(scriptPath, scr);
    if (!saved && get(bookRootAbsolutePath)) {
      const response = await apiFetch(API_ENDPOINTS.save, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: scriptPath, content: scr }),
      });
      saved = response.ok;
    }
    if (!saved) throw new Error(`Failed to save reviewed character lines (${[...assignments.keys()].join(', ')})`);
    currentScript.set({ ...scr, lines: [...scr.lines] });
    await forceRefreshBookCharacters();
  }

  $: {
    // Reset transient UI state on chapter change
    if ($currentChapter) {
      openColorIndex = null;
    }
  }

  $: {
    const sel = $selection;
    selectionActive.set(!!(sel?.startLineId != null && sel?.endLineId != null));
  }

  // Sorted characters excluding Narrator. Keep original indices for mutations.
  $: sortedCharacters = (() => {
    const list = $characters.characters.map((character, origIndex) => ({ character, origIndex }));
    const isNarrator = (name: string) => name?.toLowerCase() === 'narrator' || name?.toLowerCase() === 'narator';
    const others = list.filter(item => !isNarrator(item.character.name));
    
    if (sortMode === 'name') {
      others.sort((a, b) => a.character.name.localeCompare(b.character.name, undefined, { sensitivity: 'base' }));
    } else if (sortMode === 'lines') {
      others.sort((a, b) => {
        const countA = chapterLineCounts.get(a.character.name) || 0;
        const countB = chapterLineCounts.get(b.character.name) || 0;
        return countB - countA; // Descending order (most lines first)
      });
    } else if (sortMode === 'color') {
      others.sort((a, b) => {
        const colorA = rgbaToOpaqueHex(a.character.color ?? colorForCharacter(a.character.name), DISPLAY_ALPHA).toUpperCase();
        const colorB = rgbaToOpaqueHex(b.character.color ?? colorForCharacter(b.character.name), DISPLAY_ALPHA).toUpperCase();
        
        // Find index in defaultColors array
        const indexA = defaultColors.findIndex(c => c.toUpperCase() === colorA);
        const indexB = defaultColors.findIndex(c => c.toUpperCase() === colorB);
        
        // If both colors are in defaultColors, sort by their position
        if (indexA !== -1 && indexB !== -1) {
          return indexA - indexB;
        }
        // If only one is in defaultColors, put it first
        if (indexA !== -1) return -1;
        if (indexB !== -1) return 1;
        // If neither is in defaultColors, sort alphabetically by hex
        return colorA.localeCompare(colorB);
      });
    }
    
    return others;
  })();

  // Chapter-specific line counts for the list badges + details stats.
  // Extracted to a pure helper (GUID-keyed in v3, chosenSpeaker fallback for
  // v1-script chapters) so the derivation is never duplicated.
  $: chapterLineCounts = computeChapterLineCounts($currentScript?.lines, $bookCharacters.characters);

  $: sortedBookCharacters = (() => {
    const list = $bookCharacters.characters
      .map((character, index) => ({ character, index }));
    list.sort((a, b) => a.character.name.localeCompare(b.character.name, undefined, { sensitivity: 'base' }));
    return list;
  })();

  // The selected record, resolved from the folder-loaded store.
  $: selectedCharacter = selectedCharacterName
    ? $bookCharacters.characters.find(c => c.name === selectedCharacterName) ?? null
    : null;

  // ONE reactive cleanup rule: the selection is stale when the resolved record
  // no longer exists, and it resets when the chapter changes. This replaces the
  // old ad-hoc nulling sites scattered across delete/merge.
  $: if ($currentChapter && _selectionChapterKey !== $currentChapter.title) {
    _selectionChapterKey = $currentChapter.title;
    selectedCharacterName = null;
  }
  $: if (selectedCharacterName && !$bookCharacters.characters.some(c => c.name === selectedCharacterName)) {
    selectedCharacterName = null;
  }

  // Line count for the selected character in the current chapter (card stat).
  $: selectedChapterLineCount = selectedCharacterName
    ? (chapterLineCounts.get(selectedCharacterName) ?? 0)
    : 0;

  function resolveCharacterColor(name: string, colorOverride?: string | null): string {
    return rgbaToOpaqueHex(colorOverride ?? colorForCharacter(name), DISPLAY_ALPHA);
  }

  async function ensureBookCharacterExists(name: string): Promise<void> {
    // v3: a new cluster gets a minted GUID + one characters/<Title>.json via
    // the mutation service (no root characters.json write, invariant IN-2).
    const data = get(bookCharacters);
    if (data.characters.some(c => c.name === name)) return;
    await addBookCharacter(name, 'Unknown');
  }

  async function updateChapterCharactersFileForChapter(
    chapterTitle: string,
    updater: (existing: CharactersJson | null) => CharactersJson | null
  ): Promise<void> {
    const root = get(bookRoot);
    if (!root) return;
    const path = getCharactersPath(chapterTitle);
    const existing = await loadChapterCharactersData(root, path);
    const next = updater(existing);
    if (!next) return;
    await persistChapterCharactersData(root, path, next);
    if (get(currentChapter)?.title === chapterTitle) {
      await refreshChapterCharactersFromFile();
    }
  }

  async function reassignCharacterAcrossBook(oldName: string, newName: string): Promise<void> {
    const root = get(bookRoot);
    if (!root) return;
    const chapterList = get(chapters);
    const oldKey = normalizeCharacterKey(oldName);
    const newKey = normalizeCharacterKey(newName);
    for (const ch of chapterList) {
      const path = (ch as any).scriptPath ?? getScriptPath(ch.title);
      const scr = await readFileAsJson<ScriptJson>(path);
      if (!scr) continue;
      let changed = false;
      for (const line of scr.lines) {
        if (normalizeCharacterKey(line.chosenSpeaker || '') === oldKey) {
          line.chosenSpeaker = newName;
          line.isConflict = !line.chosenSpeaker || (line.candidates && line.candidates.length > 1 && !line.candidates.some(c => c.name === newName));
          changed = true;
        }
      }
      if (changed) {
        await writeScript(path, scr);
        if (get(currentChapter)?.title === ch.title) {
          currentScript.set(scr);
        }
      }

      await updateChapterCharactersFileForChapter(ch.title, (existing) => {
        if (!existing?.characters) return existing;
        const list = existing.characters.filter(c => normalizeCharacterKey(c.name) !== oldKey);
        if (changed && !list.some(c => normalizeCharacterKey(c.name) === newKey)) {
          list.push({ name: newName, color: null, voice: null } as Character);
        }
        return { formatVersion: existing.formatVersion || '2.0', characters: list };
      });
    }
  }

  async function countCharacterUsageAcrossBook(characterName: string): Promise<number> {
    const root = get(bookRoot);
    if (!root) return 0;

    const canonicalEntry = get(bookCharacters).characters.find(
      (character) => normalizeCharacterKey(character.name) === normalizeCharacterKey(characterName)
    );
    const namesToMatch = new Set<string>([
      normalizeCharacterKey(characterName),
      normalizeCharacterKey(canonicalEntry?.name || ''),
    ]);
    const aliases = Array.isArray(canonicalEntry?.aliases) ? canonicalEntry.aliases : [];
    for (const alias of aliases) namesToMatch.add(normalizeCharacterKey(alias));

    let total = 0;
    const chapterList = get(chapters);
    for (const chapter of chapterList) {
      const path = (chapter as any).scriptPath ?? getScriptPath(chapter.title);
      const scr = await readFileAsJson<ScriptJson>(path);
      if (!scr?.lines) continue;
      for (const line of scr.lines) {
        if (!line.chosenSpeaker) continue;
        if (namesToMatch.has(normalizeCharacterKey(line.chosenSpeaker))) {
          total += 1;
        }
      }
    }
    return total;
  }

  async function deleteBookCharacterImpl(name: string, count: number): Promise<void> {
    const usageCount = await countCharacterUsageAcrossBook(name);
    if (usageCount > 0) {
      const input = prompt(
        `"${name}" is used on ${usageCount} line${usageCount === 1 ? '' : 's'}.\n\n` +
        `Type a replacement character name to reassign lines,\n` +
        `type DELETE to delete anyway (lines -> Unknown),\n` +
        `or leave blank to cancel.`
      );

      if (input == null) return;
      const decision = input.trim();
      if (!decision) return;

      if (decision.toUpperCase() === 'DELETE') {
        await ensureBookCharacterExists('Unknown');
        await reassignCharacterAcrossBook(name, 'Unknown');
      } else {
        await ensureBookCharacterExists(decision);
        await reassignCharacterAcrossBook(name, decision);
      }
    } else if (count > 0) {
      await ensureBookCharacterExists('Unknown');
      await reassignCharacterAcrossBook(name, 'Unknown');
    }

    await removeBookCharacter(name);
    // Selection cleanup is handled by the single reactive rule above (the
    // resolved record no longer exists once removed).
  }

  async function mergeBookCharactersByName(sourceName: string, targetName: string): Promise<void> {
    if (!sourceName || !targetName || sourceName === targetName) return;
    const result = await mergeBookCharacters(sourceName, targetName);
    if (!result.ok && result.message) alert(result.message);
    if (result.ok) requestChapterReload();
    // Keep the selection pointed at the survivor (a deliberate re-point, not a
    // cleanup).
    if (selectedCharacterName === sourceName) {
      selectedCharacterName = targetName;
    }
  }

  // === Details-card intent wiring (one-liners over the store actions) ===

  async function commitCharacterTitle(newName: string): Promise<void> {
    if (!selectedCharacter) return;
    const result = await renameBookCharacter(selectedCharacter.name, newName);
    // A title collision is a VISIBLE rejection (R7/IN-8) — never silent; the
    // card stays on the old title (the store was not mutated).
    if (!result.ok && result.message) {
      alert(result.message);
    } else if (result.ok) {
      // Follow the survivor: keep the card pointed at the renamed character.
      selectedCharacterName = newName;
      if (result.warning) console.warn('[ChapterCharacterPanel] rename warning:', result.warning);
      requestChapterReload();
    }
  }

  function addCharacterDescriptor(descriptor: string): void {
    if (!selectedCharacter) return;
    const descriptors = Array.isArray(selectedCharacter.descriptors) ? selectedCharacter.descriptors : [];
    setBookCharacterDescriptors(selectedCharacter.name, [...descriptors, descriptor]);
  }

  function removeCharacterDescriptor(descriptor: string): void {
    if (!selectedCharacter) return;
    const descriptors = Array.isArray(selectedCharacter.descriptors) ? selectedCharacter.descriptors : [];
    setBookCharacterDescriptors(selectedCharacter.name, descriptors.filter((d) => d !== descriptor));
  }

  async function addBookCharacterToCurrentChapter(name: string): Promise<void> {
    console.log('[DnD-trace] addBookCharacterToCurrentChapter:', name);
    const trimmedName = String(name || '').trim();
    if (!trimmedName) return;

    const existingChapter = get(characters);
    if (existingChapter.characters.some((character) => normalizeCharacterKey(character.name) === normalizeCharacterKey(trimmedName))) {
      console.log('[DnD-trace] early return: already in chapter list');
      return;
    }

    const canonical = get(bookCharacters).characters.find(
      (character) => normalizeCharacterKey(character.name) === normalizeCharacterKey(trimmedName)
    );
    const chapterName = canonical?.name || trimmedName;

    // Explicit, user-initiated roster edit (drop a book character into the
    // chapter list): allowed to write the roster file in v3 books, the same
    // way the migration does. The v3 guard (issue #2) stays in force for
    // INCIDENTAL flows (assignment changes, rename/merge resyncs), which must
    // not resurrect roster files the user never asked for.
    await updateChapterCharactersFile((existing) => {
      const list = existing?.characters ?? [];
      if (list.some((character) => normalizeCharacterKey(character.name) === normalizeCharacterKey(chapterName))) {
        return existing ?? { formatVersion: '2.0', characters: list };
      }
      return {
        formatVersion: existing?.formatVersion || '2.0',
        characters: [
          ...list,
          {
            name: chapterName,
            color: canonical?.color ?? null,
            voice: canonical?.voice ?? null,
          } as Character,
        ],
      };
    }, { bypassV3Guard: true });
  }

  async function updateChapterCharactersFile(
    updater: (existing: CharactersJson | null) => CharactersJson | null,
    options: PersistChapterCharactersOptions = {}
  ): Promise<void> {
    const ch: any = get(currentChapter);
    const root = get(bookRoot);
    if (!ch || !root) return;
    const path = getCharactersPath(ch.title);
    const existing = await loadChapterCharactersData(root, path);
    const next = updater(existing);
    if (!next) return;
    await persistChapterCharactersData(root, path, next, options);
    await refreshChapterCharactersFromFile();
  }

  function ensureCharacterInList(name: string, color: string | null = null) {
    if (!name) return;
    const chars = get(characters);
    if (chars.characters.some(c => c.name === name)) return;
    const bookChars = get(bookCharacters);
    const bookChar = bookChars.characters.find(c => c.name === name);
    const next: CharactersJson = {
      formatVersion: chars.formatVersion || '2.0',
      characters: [
        ...chars.characters,
        { name, color: color ?? bookChar?.color ?? null, voice: null } as Character
      ]
    };
    characters.set(next);
  }

  function jumpToNextCharacter(characterName: string) {
    const scr = get(currentScript);
    if (!scr) return;

    const aliasToCanonical = new Map<string, string>();
    const canonicalToAliases = new Map<string, Set<string>>();
    for (const character of get(bookCharacters).characters) {
      const aliases = Array.isArray(character.aliases) ? character.aliases : [];
      for (const alias of aliases) {
        const key = normalizeCharacterKey(alias);
        if (!key) continue;
        aliasToCanonical.set(key, character.name);
        if (!canonicalToAliases.has(character.name)) {
          canonicalToAliases.set(character.name, new Set());
        }
        canonicalToAliases.get(character.name)?.add(alias);
      }
    }

    const canonical = aliasToCanonical.get(normalizeCharacterKey(characterName)) || characterName;
    const namesToMatch = new Set<string>([canonical, characterName]);
    const aliasSet = canonicalToAliases.get(canonical);
    if (aliasSet) {
      for (const alias of aliasSet) namesToMatch.add(alias);
    }

    const normalizedNamesToMatch = new Set<string>();
    for (const name of namesToMatch) {
      normalizedNamesToMatch.add(normalizeCharacterKey(name));
    }

    const lastLine = lastScrolledLine.get(canonical) ?? -1;
    const lines = scr.lines.filter(l => {
      if (!l.chosenSpeaker) return false;
      return normalizedNamesToMatch.has(normalizeCharacterKey(l.chosenSpeaker));
    });
    
    if (lines.length === 0) return;
    
    // Find the next line after the last scrolled position
    let nextLine = lines.find(l => l.id > lastLine);
    
    // If no next line found, wrap around to the first line
    if (!nextLine) {
      nextLine = lines[0];
    }
    
    lastScrolledLine.set(canonical, nextLine.id);
    conflictCursor.set(nextLine.id);
  }

  async function mergeCharactersImpl(sourceIndex: number, targetIndex: number) {
    if (sourceIndex === targetIndex) return;
    const ch: any = get(currentChapter);
    const root = get(bookRoot);
    if (!ch || !root) return;
    const chars = get(characters);
    const source = chars.characters[sourceIndex];
    const target = chars.characters[targetIndex];
    if (!source || !target) return;
    const oldName = source.name;
    const newName = target.name;
    const oldKey = normalizeCharacterKey(oldName);
    const scr = get(currentScript);
    if (scr) {
      for (const line of scr.lines) {
        if (normalizeCharacterKey(line.chosenSpeaker || '') === oldKey) {
          line.chosenSpeaker = newName;
          line.isConflict = !line.chosenSpeaker || (line.candidates && line.candidates.length > 1 && !line.candidates.some(c => c.name === newName));
        }
      }
      await writeScript(ch.scriptPath ?? getScriptPath(ch.title), scr);
      currentScript.set(scr);
    }
    // Update book-level characters using merge semantics to preserve aliases on conflict
    const bookList = get(bookCharacters).characters;
    const sourceCanonicalExists = bookList.some((character) => normalizeCharacterKey(character.name) === oldKey);
    const targetCanonicalExists = bookList.some((character) => normalizeCharacterKey(character.name) === normalizeCharacterKey(newName));
    if (sourceCanonicalExists && targetCanonicalExists) {
      await mergeBookCharacters(oldName, newName);
    } else {
      await renameBookCharacter(oldName, newName);
    }
    await updateChapterCharactersFile((existing) => {
      const list = existing?.characters ?? [];
      const withoutOld = list.filter(c => normalizeCharacterKey(c.name) !== oldKey);
      if (!withoutOld.some(c => normalizeCharacterKey(c.name) === normalizeCharacterKey(newName))) {
        withoutOld.push({ name: newName, color: null, voice: null } as Character);
      }
      return { formatVersion: existing?.formatVersion || '2.0', characters: withoutOld };
    });
    // Chapter-level characters will refresh automatically on chapter change
    if (openColorIndex === sourceIndex) openColorIndex = null;
  }

  async function applyToSelection(name: string) {
    const scr = get(currentScript);
    if (!scr) return;
    const sel = get(selection);
    if (sel.startLineId == null || sel.endLineId == null) return;
    const [a, b] = sel.startLineId <= sel.endLineId ? [sel.startLineId, sel.endLineId] : [sel.endLineId, sel.startLineId];
    for (const line of scr.lines) {
      if (line.id >= a && line.id <= b) {
        line.chosenSpeaker = name;
        line.isConflict = !line.chosenSpeaker || (line.candidates && line.candidates.length > 1 && !line.candidates.some(c => c.name === name));
      }
    }
    const ch: any = get(currentChapter);
    const root = get(bookRoot);
    if (ch && root) {
      const path = ch.scriptPath ?? getScriptPath(ch.title);
      await writeScript(path, scr);
      currentScript.set(scr);
    }
    ensureCharacterInList(name);
  }

  let addCharacterModalOpen = false;
  let addCharacterName = '';
  let addCharacterError = '';
  let addCharacterSaving = false;

  function openAddCharacterModal() {
    addCharacterName = '';
    addCharacterError = '';
    addCharacterSaving = false;
    addCharacterModalOpen = true;
  }

  function closeAddCharacterModal() {
    if (addCharacterSaving) return;
    addCharacterModalOpen = false;
  }

  function focusOnMount(node: HTMLInputElement) {
    node.focus();
  }

  async function confirmAddCharacter() {
    const name = addCharacterName.trim();
    if (!name) {
      addCharacterError = 'Please enter a character name.';
      return;
    }
    const key = normalizeCharacterKey(name);
    if (get(bookCharacters).characters.some(c => normalizeCharacterKey(c.name) === key)) {
      addCharacterError = `A character named "${name}" already exists.`;
      return;
    }
    addCharacterSaving = true;
    try {
      const added = await addCharacter(name);
      if (!added) {
        addCharacterError = 'Could not create the character.';
        return;
      }
      addCharacterModalOpen = false;
      selectedCharacterName = name;
    } finally {
      addCharacterSaving = false;
    }
  }

  async function addCharacterImpl(name: string): Promise<boolean> {
    // v3: mint a GUID + write one characters/<Title>.json (silent auto-upsert).
    const created = await addBookCharacter(name, 'Unknown');
    if (!created) return false;
    // Explicit, user-initiated roster add — same v3 exemption as the
    // book-character drop (see addBookCharacterToCurrentChapter).
    await updateChapterCharactersFile((existing) => {
      const list = existing?.characters ?? [];
      if (list.some(c => c.name === name)) return existing ?? { formatVersion: '2.0', characters: list };
      return {
        formatVersion: existing?.formatVersion || '2.0',
        characters: [...list, { name, color: null, voice: null } as Character]
      };
    }, { bypassV3Guard: true });
    // Update local display so the new character appears immediately
    const chars = get(characters);
    if (!chars.characters.some(c => c.name === name)) {
      const nextChars: CharactersJson = {
        formatVersion: chars.formatVersion || '2.0',
        characters: [...chars.characters, { name, color: null, voice: null } as Character]
      };
      characters.set(nextChars);
    }
    return true;
  }

  async function setColor(index: number, opaqueColor: string | null) {
    const chars = get(characters);
    const name = chars.characters[index]?.name;
    if (!name) return;
    
    // Convert the opaque color back to the original vibrant color that will render at 0.22 alpha
    const originalColor = opaqueColor ? opaqueHexToRgba(opaqueColor, DISPLAY_ALPHA) : null;
    
    // ONLY update book-level (single source of truth)
    await upsertBookCharacter(name, { color: originalColor });
    
    // Update local display by re-reading from book-level
    const updated: CharactersJson = { 
      formatVersion: chars.formatVersion,
      characters: chars.characters.map((c, i) => 
        i === index ? { ...c, color: originalColor } : c
      ) 
    };
    characters.set(updated);
    openColorIndex = null;
  }

  async function createAndApplyNewCharacterImpl() {
    const name = newCharacterName.trim();
    if (!name) return;
    const bookChars = get(bookCharacters);
    const exists = bookChars.characters.some(c => c.name === name);
    if (!exists) {
      // v3: mint a GUID + write one characters/<Title>.json (silent auto-upsert).
      const created = await addBookCharacter(name, 'Unknown');
      if (!created) return;
    } else if (newCharacterColor) {
      // Update color if character already exists
      await upsertBookCharacter(name, { color: newCharacterColor });
    }
    ensureCharacterInList(name, newCharacterColor ?? null);
    await applyToSelection(name);
    newCharacterName = '';
    newCharacterColor = null;
  }

</script>

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .modal-content {
    background: var(--app-surface);
    border-radius: 12px;
    box-shadow: var(--app-shadow-lg);
    width: min(400px, calc(100vw - 32px));
    padding: 24px;
    box-sizing: border-box;
    color: var(--app-text);
  }

  .modal-title {
    font-size: 18px;
    font-weight: 700;
    margin: 0 0 12px 0;
  }

  .modal-input {
    width: 100%;
    box-sizing: border-box;
    padding: 8px 10px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    background: var(--app-surface-alt, var(--app-surface));
    color: var(--app-text);
    font-size: 14px;
  }

  .modal-input:focus {
    outline: none;
    border-color: var(--app-primary);
  }

  .modal-error {
    margin: 8px 0 0 0;
    font-size: 13px;
    color: var(--app-danger);
  }

  .modal-actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
    margin-top: 20px;
  }

  .modal-btn {
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .modal-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .cancel-btn {
    background: var(--app-surface-hover);
    color: var(--app-text);
  }

  .cancel-btn:hover:not(:disabled) {
    background: var(--app-surface-active);
  }

  .confirm-btn {
    background: var(--app-primary);
    color: var(--app-text-inverse);
  }

  .confirm-btn:hover:not(:disabled) {
    background: color-mix(in srgb, var(--app-primary) 85%, black);
  }

  .character-panel {
    padding: 8px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 14px;
    width: 100%;
    box-sizing: border-box;
    color: var(--app-text);
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 4px;
    gap: 10px;
  }

  .panel-tabs {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px;
    border-radius: 12px;
    background: var(--app-surface-subtle);
    border: 1px solid var(--app-border);
  }

  .panel-tab {
    border: none;
    background: transparent;
    color: var(--app-text-muted);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
  }

  .panel-tab:hover {
    background: var(--app-surface-hover);
    color: var(--app-text);
  }

  .panel-tab.active {
    background: var(--app-surface-raised);
    color: var(--app-text);
    box-shadow: var(--app-shadow-sm);
  }

  .panel-tab.has-alert {
    color: var(--app-primary-text);
  }

  .panel-menu-trigger {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
  }

  .panel-menu-trigger.has-alert {
    color: var(--app-primary-text);
  }

  .panel-menu-options {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .panel-menu-options .panel-tab {
    text-align: left;
  }

  .header-btn {
    width: 24px;
    height: 24px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--app-text-muted);
    cursor: pointer;
    outline: none;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
  }

  .header-btn:hover {
    background-color: var(--app-surface-hover);
    color: var(--app-primary-text);
  }
  
  .header-actions {
    display: flex;
    gap: 4px;
    align-items: center;
  }

  .panel-divider {
    height: 1px;
    background-color: var(--app-border-subtle);
    margin: 2px 0 6px 0;
  }

  .new-character-form {
    display: flex;
    align-items: center;
    gap: 8px;
    border: 1px dashed var(--app-border);
    padding: 6px;
    border-radius: 6px;
    box-sizing: border-box;
    background: var(--app-surface-subtle);
  }

  .character-input {
    flex: 1;
    padding: 4px 6px;
    border: 1px solid var(--app-border);
    border-radius: 4px;
    font-size: 14px;
    background: var(--app-surface-raised);
    color: var(--app-text);
  }

  .color-preview-btn {
    width: 20px;
    height: 20px;
    border-radius: 6px;
    border: 1px solid var(--app-border);
  }

  .create-btn {
    font-size: 13px;
  }

  .no-characters {
    color: var(--app-text-muted);
    font-size: 14px;
  }

</style>

<div class="character-panel" use:observePanelWidth>
  <div class="panel-header">
    {#if compactTabs}
      <Dropdown
        title="Character panel tabs"
        showCaret={false}
        minWidth={160}
        align="left"
      >
        <span class="panel-menu-trigger" class:has-alert={panelTabs.some((tab) => tab.hasAlert)}>
          <Menu size={18} />
          <span>{selectedTab.label}</span>
        </span>
        <div slot="panel" let:close class="panel-menu-options" role="group" aria-label="Character panel tabs">
          {#each panelTabs as tab}
            <button
              class="panel-tab"
              class:active={activeTab === tab.value}
              class:has-alert={tab.hasAlert}
              aria-pressed={activeTab === tab.value}
              on:click={() => { activeTab = tab.value; close(); }}
            >
              {tab.label}
            </button>
          {/each}
        </div>
      </Dropdown>
    {:else}
      <div class="panel-tabs" role="tablist" aria-label="Character panel tabs">
        {#each panelTabs as tab}
          <button
            class="panel-tab"
            class:active={activeTab === tab.value}
            class:has-alert={tab.hasAlert}
            role="tab"
            aria-selected={activeTab === tab.value}
            on:click={() => activeTab = tab.value}
          >
            {tab.label}
          </button>
        {/each}
      </div>
    {/if}

    {#if activeTab === 'characters'}
      <div class="header-actions">
        <Dropdown
          items={[
            { value: 'name', label: 'Sort by Name' },
            { value: 'lines', label: 'Sort by Lines' },
            { value: 'color', label: 'Sort by Color' }
          ]}
          selected={sortMode}
          on:select={(e) => sortMode = e.detail.value as SortMode}
          title="Sort characters"
          minWidth={150}
          align="right"
        >
          <button class="header-btn" title="Sort characters" aria-label="Sort characters">
            <ArrowUpDown size={18} />
          </button>
        </Dropdown>
        <button class="header-btn" title="Add character" aria-label="Add character" on:click={openAddCharacterModal}>
          <Plus size={18} />
        </button>
      </div>
    {/if}
  </div>
  <div class="panel-divider"></div>

  {#if activeTab === 'ai'}
    <ChapterAiAssistPanel />
  {:else if activeTab === 'local-ai'}
    <ChapterLocalAiPanel />
  {:else if activeTab === 'jev'}
    <ChapterJevPanel />
  {:else}
    <ClosedWorldCharacterReview
      items={closedWorldReviewItems}
      characters={$bookCharacters.characters}
      onAdd={addReviewedCharacter}
      onAlias={mergeReviewedAlias}
      onDescriptor={mergeReviewedDescriptor}
      onNonSpeaker={markReviewedAsNonSpeaker}
      onJump={jumpToReviewLine}
    />
    {#if $selectionActive}
      <div class="new-character-form">
        <input class="character-input" placeholder="New character name" bind:value={newCharacterName} on:keydown={(e) => { if (e.key==='Enter') createAndApplyNewCharacter(); }} />
        <button class="color-preview-btn" title="Pick color" on:click={() => newCharacterColor = prompt('Enter hex color (e.g. #2196f3)') || newCharacterColor} style={`background:${newCharacterColor ?? 'var(--app-primary-soft)'}`}></button>
        <button class="create-btn" on:click={createAndApplyNewCharacter}>Create & Apply</button>
      </div>
    {/if}

    <ChapterCharacterList
      items={sortedCharacters}
      {openColorIndex}
      selectionActive={$selectionActive}
      {DISPLAY_ALPHA}
      lineCounts={chapterLineCounts}
      jumpLineCounts={chapterLineCounts}
      onSelect={(name: string) => selectedCharacterName = name}
      onToggleColor={(index: number) => openColorIndex = openColorIndex === index ? null : index}
      onSetColor={(index: number, color: string | null) => setColor(index, color)}
      onApplyToSelection={(name: string) => applyToSelection(name)}
      onMerge={(source: number, target: number) => mergeCharacters(source, target)}
      onDropBookCharacter={(name: string) => addBookCharacterToCurrentChapter(name)}
      onJumpToNext={(name: string) => jumpToNextCharacter(name)}
      onDelete={async (index: number) => {
        const ch: any = $currentChapter;
        const root = $bookRoot;
        if (!ch || !root) return;
        const chars = $characters;
        const name = chars.characters[index]?.name;
        if (!name) return;
        const scr = $currentScript;
        const hasAssignments = !!scr?.lines.some(l => l.chosenSpeaker === name);
        if (hasAssignments) {
          const ok = confirm(`Delete character "${name}"? This will clear ${name}'s assignments in this chapter.`);
          if (!ok) return;
        }
        if (scr) {
          for (const line of scr.lines) {
            if (line.chosenSpeaker === name) {
              line.chosenSpeaker = null;
              line.isConflict = true;
            }
          }
          await writeScript(ch.scriptPath ?? getScriptPath(ch.title), scr);
          currentScript.set(scr);
        }
        await removeBookCharacter(name);
        await updateChapterCharactersFile((existing) => {
          const list = existing?.characters ?? [];
          const nextList = list.filter(c => c.name !== name);
          return { formatVersion: existing?.formatVersion || '2.0', characters: nextList };
        });
        characters.set({
          formatVersion: chars.formatVersion || '2.0',
          characters: chars.characters.filter((c) => c.name !== name)
        });
        if (openColorIndex === index) openColorIndex = null;
      }}
    />
    {#if !$characters.characters.length}
      <p class="no-characters">No characters detected</p>
    {/if}

    <ChapterCharacterBookList
      open={bookListOpen}
      items={sortedBookCharacters}
      selectedName={selectedCharacterName}
      onToggle={() => bookListOpen = !bookListOpen}
      onSelect={(name) => selectedCharacterName = name}
      onDelete={(name, count) => deleteBookCharacter(name, count)}
      onMerge={(sourceName, targetName) => mergeBookCharactersByName(sourceName, targetName)}
      resolveColor={resolveCharacterColor}
    />

    {#if selectedCharacter}
      <CharacterDetails
        character={selectedCharacter}
        chapterLineCount={selectedChapterLineCount}
        onSetTitle={(newName: string) => commitCharacterTitle(newName)}
        onSetGender={(gender: Gender) => upsertBookCharacter(selectedCharacter.name, { gender })}
        onAddAlias={(alias: string) => addBookCharacterAlias(selectedCharacter.name, alias)}
        onRemoveAlias={(alias: string) => detachBookCharacterAlias(selectedCharacter.name, alias)}
        onAddDescriptor={(descriptor: string) => addCharacterDescriptor(descriptor)}
        onRemoveDescriptor={(descriptor: string) => removeCharacterDescriptor(descriptor)}
      />
    {/if}
  {/if}
</div>

{#if addCharacterModalOpen}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="modal-backdrop" on:click={closeAddCharacterModal}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div
      class="modal-content"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-character-title"
      on:click|stopPropagation
    >
      <h3 class="modal-title" id="add-character-title">Add Character</h3>
      <form on:submit|preventDefault={confirmAddCharacter}>
        <input
          class="modal-input"
          placeholder="Character name"
          aria-label="Character name"
          bind:value={addCharacterName}
          on:input={() => addCharacterError = ''}
          on:keydown={(e) => { if (e.key === 'Escape') closeAddCharacterModal(); }}
          disabled={addCharacterSaving}
          use:focusOnMount
        />
        {#if addCharacterError}
          <p class="modal-error">{addCharacterError}</p>
        {/if}
        <div class="modal-actions">
          <button type="button" class="modal-btn cancel-btn" on:click={closeAddCharacterModal} disabled={addCharacterSaving}>
            Cancel
          </button>
          <button type="submit" class="modal-btn confirm-btn" disabled={addCharacterSaving || !addCharacterName.trim()}>
            Confirm
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
