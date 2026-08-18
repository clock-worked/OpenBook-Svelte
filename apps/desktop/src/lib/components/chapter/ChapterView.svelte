<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { currentChapter, currentScript, bookRoot, bookRootAbsolutePath } from '$lib/stores/bookState';
  import type { ScriptJson, DialogueJson } from '$lib/types';
  import { writable } from 'svelte/store';
  import { conflictCursor, toolMode, audioGenerateLineId } from '$lib/stores/selection';
  import { get } from 'svelte/store';
  import { readTextFile, readDialogueForChapter, readScript, readCentralCharacters, writeCentralCharacters, getRootDirInfo, writeDialogue, writeScript, readJsonRelative } from '$lib/services/fs';
  import { readManifest } from '$lib/services/audio';
  import { API_ENDPOINTS, apiFetch } from '$lib/services/apiClient';
  import { mapRawBookCharacters } from '$lib/services/bookCharacterRepository';
  import { characters, buildIdToNameMap } from '$lib/stores/characters';
  import { addBookCharacterAlias, bookCharacters } from '$lib/stores/bookCharacters';
  import ParagraphRow from '$lib/components/chapter/ParagraphRow.svelte';
  import RawTextBlock from '$lib/components/chapter/RawTextBlock.svelte';
  import Dropdown from '$lib/components/common/Dropdown.svelte';
  
  // Tool modules
  import type { UnifiedLine, MenuContext } from '$lib/components/chapter/tools/types';
  import { displayTitle, recomputeStats } from '$lib/components/chapter/tools/shared/toolUtils';
  import { clampConfidence, normalizeAttribution, normalizeScriptData } from '$lib/components/chapter/tools/shared/chapterNormalization';
  import {
    buildChapterAnimationPlanData,
    buildParagraphRuns,
    createStaticChapterAnimationPlan,
    type ChapterRun,
  } from '$lib/components/chapter/tools/shared/chapterPresentation';
  import {
    buildGeneratedAudioMarkerState,
    buildLegacyScriptFromNormalized,
    mergeNormalizedFromLegacy,
  } from '$lib/components/chapter/tools/shared/chapterStateSync';
  import {
    ensureCentralCharactersForNames as ensureCentralCharactersForNamesShared,
    ensureCharacterNameToIdMapCached,
  } from '$lib/components/chapter/tools/shared/chapterCharacterBootstrap';
  import { loadChapterContent, saveDialogueFromNormalized } from '$lib/components/chapter/tools/shared/chapterPersistence';
  import { 
    getCharacterOptionsForLine, 
    getCharacterOptionsForContext,
    assignCharacterToLines 
  } from '$lib/components/chapter/tools/review/reviewHandlers';
  import CharacterSelectionMenu from '$lib/components/chapter/tools/review/CharacterSelectionMenu.svelte';
  import {
    handleLineAudioClick as audioHandleClick,
    handleLineAudioContextMenu as audioHandleContextMenu,
    handleGenerateLineAudio,
    regenerateAudioLine,
    deleteAudioLine
  } from '$lib/components/chapter/tools/audio/audioHandlers';
  import GenerateAudioPrompt from '$lib/components/chapter/tools/audio/GenerateAudioPrompt.svelte';
  import AudioContextMenu from '$lib/components/chapter/tools/audio/AudioContextMenu.svelte';
  import { 
    startEditLine,
    saveEditedLine as saveEdit
  } from '$lib/components/chapter/tools/edit/editHandlers';
  import EditOverlay from '$lib/components/chapter/tools/edit/EditOverlay.svelte';
  import {
    joinLines as performJoinLines,
    splitLine as performSplitLine,
    handleTextSelection as handleSplitTextSelection
  } from '$lib/components/chapter/tools/join-split/joinSplitHandlers';
  import JoinToolbar from '$lib/components/chapter/tools/join-split/JoinToolbar.svelte';
  import SplitButton from '$lib/components/chapter/tools/join-split/SplitButton.svelte';
  import { audioUpdateTrigger } from '$lib/stores/audioUpdates';
  import { parserHints } from '$lib/stores/settings';
  import {
    registerDialogueAiAssistController,
    resetDialogueAiAssistState,
  } from '$lib/stores/dialogueAiAssist';
  import {
    registerLocalDialogueAiController,
    resetLocalDialogueAiState,
  } from '$lib/stores/localDialogueAi';

  const UNKNOWN_SPEAKER_LABEL = 'Unknown';

  function getUnknownThreshold(): number {
    const hints = get(parserHints);
    const threshold = hints?.attribution?.unknownThreshold;
    return clampConfidence(typeof threshold === 'number' ? threshold : 0.62);
  }

  // Internal normalized script store
  const normalizedScript = writable<{ lines: UnifiedLine[]; stats: any } | null>(null);
  
  // Cache for character name-to-ID map (built once when needed)
  let characterNameToIdMap: Map<string, string> | null = null;
  
  async function ensureCharacterNameToIdMap() {
    return ensureCharacterNameToIdMapCached({
      cachedMap: characterNameToIdMap,
      setCachedMap: (map) => {
        characterNameToIdMap = map;
      },
      root: get(bookRoot),
      readCentralCharacters,
    });
  }

  async function ensureCentralCharactersForNames(names: string[]): Promise<void> {
    await ensureCentralCharactersForNamesShared({
      names,
      root: get(bookRoot),
      readCentralCharacters,
      writeCentralCharacters,
      getBackendRootAbsolutePath: () => get(bookRootAbsolutePath),
      apiSave: (payload) =>
        apiFetch(API_ENDPOINTS.save, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
      onCharactersUpdated: (central) => {
        bookCharacters.set(mapRawBookCharacters(central as any));
      },
      resetCharacterNameToIdCache: () => {
        characterNameToIdMap = null;
      },
    });
  }

  // Track which format version we're using for saving
  let isV2Format = false;

  const rawText = writable<string | null>(null);
  const loading = writable<boolean>(false);
  let hoveredCharacter: string | null = null;
  const setHovered = (s: string | null) => { hoveredCharacter = s; };

  const paragraphRuns = writable<ChapterRun[][]>([]);
  let paragraphRowRefs: Array<HTMLDivElement | null> = [];
  let paragraphAnimateOnLoad: boolean[] = [];
  let paragraphTypingDelays: number[] = [];
  let paragraphHighlightDelays: number[] = [];
  let paragraphRevealDelays: number[] = [];
  let animationPlanVersion = 0;
  let animationPlanReady = false;
  let plannedAnimationChapterTitle: string | null = null;
  let chapterAnimationPlanSeq = 0;
  let animationCancelledByScroll = false;

  function applyStaticPlanForCurrentChapter(runsSnapshot: ChapterRun[][], chapterTitle: string) {
    const staticPlan = createStaticChapterAnimationPlan(runsSnapshot.length);
    paragraphAnimateOnLoad = staticPlan.animateOnLoad;
    paragraphTypingDelays = staticPlan.typingDelays;
    paragraphHighlightDelays = staticPlan.highlightDelays;
    paragraphRevealDelays = staticPlan.revealDelays;
    plannedAnimationChapterTitle = chapterTitle;
    animationPlanReady = true;
    animationPlanVersion += 1;
  }

  function cancelChapterAnimation() {
    if (animationCancelledByScroll) return;
    const chapterTitle = $currentChapter?.title;
    if (!chapterTitle) return;
    animationCancelledByScroll = true;
    applyStaticPlanForCurrentChapter($paragraphRuns, chapterTitle);
  }

  async function buildChapterAnimationPlan(chapterTitle: string, runsSnapshot: ChapterRun[][]): Promise<void> {
    const seq = ++chapterAnimationPlanSeq;
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

    if (seq !== chapterAnimationPlanSeq) return;
    if (get(currentChapter)?.title !== chapterTitle) return;
    if (animationCancelledByScroll) {
      applyStaticPlanForCurrentChapter(runsSnapshot, chapterTitle);
      return;
    }

    const nextPlan = buildChapterAnimationPlanData(runsSnapshot);
    paragraphAnimateOnLoad = nextPlan.animateOnLoad;
    paragraphTypingDelays = nextPlan.typingDelays;
    paragraphHighlightDelays = nextPlan.highlightDelays;
    paragraphRevealDelays = nextPlan.revealDelays;
    plannedAnimationChapterTitle = chapterTitle;
    animationPlanReady = true;
    animationPlanVersion += 1;
  }

  $: if ($currentChapter?.title !== plannedAnimationChapterTitle) {
    animationCancelledByScroll = false;
    paragraphAnimateOnLoad = [];
    paragraphTypingDelays = [];
    paragraphHighlightDelays = [];
    paragraphRevealDelays = [];
    animationPlanReady = false;
    animationPlanVersion += 1;
  }

  $: if ($currentChapter?.title && $paragraphRuns.length > 0 && $currentChapter.title !== plannedAnimationChapterTitle) {
    if (animationCancelledByScroll) {
      applyStaticPlanForCurrentChapter($paragraphRuns, $currentChapter.title);
    } else {
      void buildChapterAnimationPlan($currentChapter.title, $paragraphRuns);
    }
  }

  onMount(() => {
    const handleUserScroll = () => {
      cancelChapterAnimation();
    };

    window.addEventListener('scroll', handleUserScroll, { passive: true });
    window.addEventListener('wheel', handleUserScroll, { passive: true });
    window.addEventListener('touchmove', handleUserScroll, { passive: true });

    return () => {
      window.removeEventListener('scroll', handleUserScroll);
      window.removeEventListener('wheel', handleUserScroll);
      window.removeEventListener('touchmove', handleUserScroll);
    };
  });

  onDestroy(() => {
    chapterAnimationPlanSeq += 1;
    unregisterDialogueAiAssistController();
    unregisterLocalDialogueAiController();
  });

  // Review tool state
  let menuContext: MenuContext | null = null;
  const reviewMenuVisible = writable<boolean>(false);
  const reviewMenuX = writable<number>(0);
  const reviewMenuY = writable<number>(0);
  const reviewMenuItems = writable<{ value: string; label: string }[]>([]);
  const reviewMenuSelected = writable<string | null>(null);
  // Audio tool state
  let audioExistsCache = new Map<string, boolean>();
  let generatedAudioLineIds = new Set<number>();
  let generatedAudioLineTexts = new Set<string>();
  let generatingLineId: number | null = null;
  
  const generatePromptVisible = writable<boolean>(false);
  const generatePromptX = writable<number>(0);
  const generatePromptY = writable<number>(0);
  let generatePromptLineId: number | null = null;
  let generatePromptCharacterName: string | null = null;

  const audioCtxMenuVisible = writable<boolean>(false);
  const audioCtxMenuX = writable<number>(0);
  const audioCtxMenuY = writable<number>(0);
  let audioCtxMenuLineId: number | null = null;
  let audioCtxMenuCharacterName: string | null = null;

  // Edit tool state
  const editOverlayVisible = writable<boolean>(false);
  let editingLineId: number | null = null;
  let editingText: string = '';

  // Join-split tool state
  const joinToolbarVisible = writable<boolean>(false);
  const joinToolbarX = writable<number>(0);
  const joinToolbarY = writable<number>(0);
  let joinToolbarLineId: number | null = null;

  const splitButtonVisible = writable<boolean>(false);
  const splitButtonX = writable<number>(0);
  const splitButtonY = writable<number>(0);
  let splitButtonLineId: number | null = null;
  let splitButtonPosition: number | null = null;

  let saveTimer: any = null;
  let chapterLoadSeq = 0; // cancellation token for chapter loads

  let syncingFromNormalized = false;
  let syncingFromCurrent = false;

  function syncCurrentFromNormalized(scr: { lines: UnifiedLine[]; stats: any }) {
    if (syncingFromCurrent) return;
    syncingFromNormalized = true;
    const ch = get(currentChapter) as { title?: string | null; path?: string | null } | null;
    const v1Script = buildLegacyScriptFromNormalized(scr, ch);
    currentScript.set(v1Script);
    syncingFromNormalized = false;
  }

  function syncNormalizedFromCurrent(v1: ScriptJson, normalized: { lines: UnifiedLine[]; stats: any } | null) {
    if (!normalized || syncingFromNormalized) return;
    const merged = mergeNormalizedFromLegacy({
      legacy: v1,
      normalized,
      normalizeAttribution,
      unknownThreshold: getUnknownThreshold(),
    });
    if (!merged) return;

    syncingFromCurrent = true;
    const next = { ...normalized, lines: merged.lines };
    recomputeStats(next);
    normalizedScript.set(next);
    rebuildParagraphRuns();
    scheduleSave();
    syncingFromCurrent = false;
  }

  // Clear audio cache when chapter changes
  $: if ($currentChapter) {
    audioExistsCache.clear();
    generatedAudioLineIds = new Set();
    generatedAudioLineTexts = new Set();
    audioGenerateLineId.set(null);
  }

  async function refreshGeneratedAudioLineIds() {
    const ch = get(currentChapter);
    const scr = get(normalizedScript);
    if (!ch || !scr) {
      generatedAudioLineIds = new Set();
      return;
    }

    const chapterTitle = ch.title;
    const markerState = await buildGeneratedAudioMarkerState({
      chapterTitle,
      lines: scr.lines,
      root: get(bookRoot),
      readJsonRelative,
      readManifest,
      readCentralCharacters,
    });

    const latestChapter = get(currentChapter);
    if (!latestChapter || latestChapter.title !== chapterTitle) return;

    generatedAudioLineIds = markerState.generatedLineIds;
    generatedAudioLineTexts = markerState.generatedLineTexts;
    audioExistsCache = markerState.audioExistsCache;
  }

  $: if ($currentChapter && $normalizedScript) {
    void $audioUpdateTrigger;
    void refreshGeneratedAudioLineIds();
  }

  $: if ($normalizedScript && !syncingFromCurrent) {
    syncCurrentFromNormalized($normalizedScript);
  }

  $: if ($currentScript && !syncingFromNormalized) {
    syncNormalizedFromCurrent($currentScript, $normalizedScript);
  }

  // Review tool handlers
  function openCharacterMenu(ev: MouseEvent, context: MenuContext) {
    ev.preventDefault();
    ev.stopPropagation();
    menuContext = context;
    const options = getCharacterOptionsForContext(context, normalizedScript);
    const scr = get(normalizedScript);
    let selected: string | null = null;
    if (scr) {
      const first = scr.lines.find(l => l.id === context.lineIds[0]);
      if (first?.characterName) {
        const allSame = context.lineIds.every(id => scr.lines.find(l => l.id === id)?.characterName === first.characterName);
        selected = allSame ? first.characterName : null;
      }
    }
    reviewMenuItems.set(options);
    reviewMenuSelected.set(selected);
    reviewMenuX.set(ev.clientX);
    reviewMenuY.set(ev.clientY + 8);
    reviewMenuVisible.set(true);
  }

  function closeReviewMenu() {
    reviewMenuVisible.set(false);
    reviewMenuSelected.set(null);
    menuContext = null;
  }

  async function ensureAndAssignCharacters(lineIds: number[], characterName: string) {
    if (!characterName) return;
    const scr = get(normalizedScript);
    const linesById = new Map((scr?.lines || []).map((line) => [line.id, line] as const));
    const aliasLearningCandidates = new Set<string>();
    for (const lineId of lineIds) {
      const line = linesById.get(lineId);
      if (!line?.attribution) continue;
      if (line.attribution.resolutionStatus !== 'unknown') continue;
      const sourceAlias = String(line.attribution.sourceAlias || '').trim();
      if (sourceAlias) aliasLearningCandidates.add(sourceAlias);
    }

    await ensureCentralCharactersForNames([characterName]);
    assignCharacterToLines(lineIds, characterName, normalizedScript, rebuildParagraphRuns, scheduleSave);

    const normalizedTarget = characterName.trim().toLowerCase();
    const knownCanonicalNames = new Set(
      (get(bookCharacters)?.characters || [])
        .map((character) => String(character?.name || '').trim().toLowerCase())
        .filter(Boolean)
    );
    for (const aliasCandidate of aliasLearningCandidates) {
      const normalizedAlias = aliasCandidate.trim().toLowerCase();
      if (!normalizedAlias) continue;
      if (normalizedAlias === normalizedTarget) continue;
      if (normalizedAlias === 'narrator' || normalizedAlias === UNKNOWN_SPEAKER_LABEL.toLowerCase()) continue;
      if (knownCanonicalNames.has(normalizedAlias)) continue;
      await addBookCharacterAlias(characterName, aliasCandidate);
    }
  }

  async function handleReviewMenuChoose(e: CustomEvent<{ value: string }>) {
    if (!menuContext) return;
    await ensureAndAssignCharacters(menuContext.lineIds, e.detail.value);
    closeReviewMenu();
  }

  function handleParagraphAssign(characterName: string, lineIds: number[]) {
    void ensureAndAssignCharacters(lineIds, characterName);
  }

  function getDialogueAiChapterInput() {
    const chapter = get(currentChapter);
    const normalized = get(normalizedScript);
    const text = get(rawText);

    if (!chapter || !normalized || !text) return null;

    return {
      chapterPath: chapter.path || chapter.title,
      chapterTitle: chapter.title,
      rawText: text,
      normalizedScript: normalized,
    };
  }

  const dialogueAiController = {
    getChapterInput: getDialogueAiChapterInput,
    ensureAndAssignCharacters,
  };
  const unregisterDialogueAiAssistController = registerDialogueAiAssistController(dialogueAiController);
  const unregisterLocalDialogueAiController = registerLocalDialogueAiController(dialogueAiController);

  // Audio tool handlers
  async function handleLineAudioClick(lineId: number, characterName: string, event?: MouseEvent) {
    await audioHandleClick(
      lineId,
      characterName,
      audioExistsCache,
      (lineId, characterName) => {
        generatePromptLineId = lineId;
        generatePromptCharacterName = characterName;
        if (event) {
          const target = event.target as HTMLElement;
          const rect = target.getBoundingClientRect();
          generatePromptX.set(rect.left + rect.width / 2);
          generatePromptY.set(rect.bottom + 8);
        }
        generatePromptVisible.set(true);
      },
      event
    );
  }

  function closeGeneratePrompt() {
    generatePromptVisible.set(false);
    generatePromptLineId = null;
    generatePromptCharacterName = null;
  }

  async function confirmGenerateAudio() {
    if (generatePromptLineId === null) return;
    const lineId = generatePromptLineId;
    closeGeneratePrompt();
    await handleGenerateLineAudio(lineId, normalizedScript, audioExistsCache, (id) => { generatingLineId = id; });
  }

  async function handleLineAudioContextMenu(lineId: number, characterId: string, event: MouseEvent) {
    event.preventDefault();
    audioGenerateLineId.set(lineId);
    await audioHandleContextMenu(
      lineId,
      characterId,
      audioExistsCache,
      (lineId, characterName) => {
        audioCtxMenuLineId = lineId;
        audioCtxMenuCharacterName = characterName;
        audioCtxMenuX.set(event.clientX);
        audioCtxMenuY.set(event.clientY);
        audioCtxMenuVisible.set(true);
        return true;
      }
    );
  }

  function closeAudioContextMenu() {
    audioCtxMenuVisible.set(false);
    audioCtxMenuLineId = null;
    audioCtxMenuCharacterName = null;
  }

  async function handleAudioContextAction(e: CustomEvent<{ action: string }>) {
    if (audioCtxMenuLineId === null) return;
    
    if (e.detail.action === 'regenerate') {
      const lineId = audioCtxMenuLineId;
      closeAudioContextMenu();
      const shouldReplace = confirm('Regenerate this line and replace existing audio?');
      if (!shouldReplace) return;
      await regenerateAudioLine(lineId, normalizedScript, audioExistsCache, (id) => { generatingLineId = id; });
    } else if (e.detail.action === 'delete') {
      const lineId = audioCtxMenuLineId;
      closeAudioContextMenu();
      await deleteAudioLine(lineId, normalizedScript, audioExistsCache);
    }
  }

  // Edit tool handlers
  function handleStartEditLine(lineId: number) {
    startEditLine(lineId, normalizedScript, (id, text) => {
      editingLineId = id;
      editingText = text;
      editOverlayVisible.set(true);
    });
  }

  function handleCancelEdit() {
    editOverlayVisible.set(false);
    editingLineId = null;
    editingText = '';
  }

  function handleSaveEdit(e: CustomEvent<{ text: string }>) {
    if (editingLineId === null) return;
    saveEdit(editingLineId, e.detail.text, normalizedScript, rebuildParagraphRuns, scheduleSave);
    handleCancelEdit();
  }

  // Join-split tool handlers
  function handleOpenJoinMenu(ev: MouseEvent, lineId: number) {
    ev.preventDefault();
    ev.stopPropagation();
    joinToolbarLineId = lineId;
    joinToolbarX.set(ev.clientX);
    joinToolbarY.set(ev.clientY);
    joinToolbarVisible.set(true);
  }

  function closeJoinToolbar() {
    joinToolbarVisible.set(false);
    joinToolbarLineId = null;
  }

  function handleJoinAction(e: CustomEvent<{ action: string; lineId: number }>) {
    const action = e.detail.action;
    const lineId = e.detail.lineId;
    
    if (action === 'join-left') {
      performJoinLines(lineId, 'left', normalizedScript, rebuildParagraphRuns, scheduleSave);
    } else if (action === 'join-right') {
      performJoinLines(lineId, 'right', normalizedScript, rebuildParagraphRuns, scheduleSave);
    } else if (action === 'join-both') {
      performJoinLines(lineId, 'both', normalizedScript, rebuildParagraphRuns, scheduleSave);
    }
    
    closeJoinToolbar();
  }

  function handleTextSelection(ev: MouseEvent, lineId: number) {
    handleSplitTextSelection(
      lineId,
      normalizedScript,
      (lineId, position, x, y) => {
        splitButtonLineId = lineId;
        splitButtonPosition = position;
        splitButtonX.set(x);
        splitButtonY.set(y);
        splitButtonVisible.set(true);
      },
      () => {
        splitButtonVisible.set(false);
      }
    );
  }

  function handleSplitConfirm(e: CustomEvent<{ lineId: number; position: number }>) {
    performSplitLine(e.detail.lineId, e.detail.position, normalizedScript, rebuildParagraphRuns, scheduleSave);
    splitButtonVisible.set(false);
    splitButtonLineId = null;
    splitButtonPosition = null;
  }

  function scheduleSave() {
    console.log('[ChapterView] scheduleSave called, will execute in 350ms');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      console.log('[ChapterView] scheduleSave timer fired, reading from store...');
      const scr = get(normalizedScript);
      const ch = get(currentChapter);
      const root = get(bookRoot);
      if (!scr || !ch || !root) {
        console.warn('[ChapterView] scheduleSave: Missing required data', { hasScript: !!scr, hasChapter: !!ch, hasRoot: !!root });
        return;
      }

      await saveDialogueFromNormalized({
        normalized: scr,
        chapter: { title: ch.title, path: ch.path },
        root,
        rawText: get(rawText),
        unknownThreshold: getUnknownThreshold(),
        unknownSpeakerLabel: UNKNOWN_SPEAKER_LABEL,
        ensureCentralCharactersForNames,
        ensureCharacterNameToIdMap,
        normalizeAttribution,
        writeDialogue,
        getRootDirInfo,
        getBackendRootAbsolutePath: () => get(bookRootAbsolutePath),
        apiSave: (relativePath, content) =>
          apiFetch(API_ENDPOINTS.save, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: relativePath, content }),
          }),
      });
    }, 350);
  }

  function rebuildParagraphRuns() {
    const scr = get(normalizedScript);
    const txt = get(rawText);
    if (!scr) { paragraphRuns.set([]); return; }

    paragraphRuns.set(buildParagraphRuns(scr.lines, txt));
  }

  $: if ($currentChapter) {
    const seq = ++chapterLoadSeq;
    (async () => {
      console.log('[ChapterView] Reactive block triggered - loading chapter:', $currentChapter.title);
      loading.set(true);
      normalizedScript.set(null);
      rawText.set(null);
      paragraphRuns.set([]);
      resetDialogueAiAssistState();
      resetLocalDialogueAiState();
      isV2Format = false;
      const ch: any = $currentChapter;
      console.log('[ChapterView] Chapter path:', ch.path);
      try {
        const root = get(bookRoot);
        if (ch && root) {
          const loaded = await loadChapterContent({
            chapter: { title: ch.title, path: ch.path },
            root,
            unknownThreshold: getUnknownThreshold(),
            unknownSpeakerLabel: UNKNOWN_SPEAKER_LABEL,
            readDialogueForChapter,
            normalizeScriptData,
            readCentralCharacters,
            buildIdToNameMap,
            readTextFile,
          });

          if (seq !== chapterLoadSeq) return; // superseded by newer chapter switch
          isV2Format = loaded.isV2Format;

          if (loaded.normalized) {
            normalizedScript.set({ lines: loaded.normalized.lines, stats: loaded.normalized.stats });
            if (loaded.currentScript) {
              currentScript.set(loaded.currentScript as ScriptJson);
            }
            if (loaded.normalized.hadAliasChanges) {
              characterNameToIdMap = null;
              scheduleSave();
            }
          }

          rawText.set(loaded.rawText);
        }
      } finally {
        if (seq === chapterLoadSeq) {
          loading.set(false);
          rebuildParagraphRuns();
        }
      }
    })();
  }

  $: if ($normalizedScript && $rawText) {
    rebuildParagraphRuns();
  }

  $: if ($conflictCursor != null) {
    const el = document.getElementById('line-' + $conflictCursor);
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
</script>

<div class="center-wrap" on:wheel={cancelChapterAnimation} on:touchmove={cancelChapterAnimation}>
  <div class="content">
    {#if $currentChapter}
      <h2 class="chapter-title">{displayTitle($currentChapter.title)}</h2>
    {/if}

    {#if $currentScript && $rawText && $paragraphRuns.length}
      {#each $paragraphRuns as runs, rowIndex}
        <div bind:this={paragraphRowRefs[rowIndex]}>
          <ParagraphRow
            {runs}
            planReady={animationPlanReady}
            animateOnLoad={paragraphAnimateOnLoad[rowIndex] ?? false}
            typingDelayMs={paragraphTypingDelays[rowIndex] ?? 0}
            highlightDelayMs={paragraphHighlightDelays[rowIndex] ?? 0}
            revealDelayMs={paragraphRevealDelays[rowIndex] ?? 0}
            {animationPlanVersion}
            hoveredCharacter={hoveredCharacter}
            {setHovered}
            toolMode={$toolMode}
            {generatedAudioLineIds}
            {generatedAudioLineTexts}
            on:paragraphMenu={(e) => openCharacterMenu(e.detail.ev, { kind: 'paragraph', lineIds: e.detail.lineIds })}
            on:paragraphAssign={(e) => handleParagraphAssign(e.detail.characterName, e.detail.lineIds)}
            on:lineMenu={(e) => openCharacterMenu(e.detail.ev, { kind: 'line', lineIds: e.detail.lineIds })}
            on:selectionMenu={(e) => { openCharacterMenu(e.detail.ev, { kind: 'selection', lineIds: e.detail.lineIds }); reviewMenuX.set(e.detail.anchorX); reviewMenuY.set(e.detail.anchorY); }}
            on:joinMenu={(e) => handleOpenJoinMenu(e.detail.ev, e.detail.lineId)}
            on:splitMenu={(e) => handleTextSelection(e.detail.ev, e.detail.lineId)}
            on:editLine={(e) => handleStartEditLine(e.detail.lineId)}
            on:audioClick={(e) => handleLineAudioClick(e.detail.lineId, e.detail.characterName, e.detail.ev)}
            on:audioContextMenu={(e) => handleLineAudioContextMenu(e.detail.lineId, e.detail.characterName, e.detail.ev)}
          />
        </div>
      {/each}
    {:else if $normalizedScript && $normalizedScript.lines.length > 0}
      {#each $normalizedScript.lines as line}
        <div class="line-row">
          <div class="character-col">
            <Dropdown
              items={getCharacterOptionsForLine(line.id, normalizedScript)}
              selected={line.characterName || null}
              on:select={(e) => { void ensureAndAssignCharacters([line.id], e.detail.value); }}
              title="Assign character"
              minWidth={180}
              align="left"
            >
              <span class="character-chip">{line.characterName || 'Unknown'}</span>
            </Dropdown>
          </div>
          <div class="line-bubble">
            {line.text}
          </div>
        </div>
      {/each}
    {:else if $loading}
      {#each Array(10) as _, i}
        <div class="skeleton-line"></div>
      {/each}
    {:else if $rawText}
      <RawTextBlock rawText={$rawText} />
    {:else}
      <p class="placeholder">No content. Use Regenerate to parse script.</p>
    {/if}
  </div>
</div>

<!-- Review Tool UI -->
<CharacterSelectionMenu
  items={$reviewMenuItems}
  selected={$reviewMenuSelected}
  x={$reviewMenuX}
  y={$reviewMenuY}
  visible={$reviewMenuVisible}
  on:select={handleReviewMenuChoose}
  on:close={closeReviewMenu}
/>

<!-- Audio Tool UI -->
<GenerateAudioPrompt
  lineId={generatePromptLineId || 0}
  characterName={generatePromptCharacterName || ''}
  x={$generatePromptX}
  y={$generatePromptY}
  visible={$generatePromptVisible}
  on:confirm={confirmGenerateAudio}
  on:close={closeGeneratePrompt}
/>

<AudioContextMenu
  x={$audioCtxMenuX}
  y={$audioCtxMenuY}
  visible={$audioCtxMenuVisible}
  on:action={handleAudioContextAction}
  on:close={closeAudioContextMenu}
/>

<!-- Edit Tool UI -->
<EditOverlay
  text={editingText}
  visible={$editOverlayVisible}
  on:save={handleSaveEdit}
  on:cancel={handleCancelEdit}
/>

<!-- Join-Split Tool UI -->
{#if $normalizedScript}
<JoinToolbar
  lineId={joinToolbarLineId || 0}
  x={$joinToolbarX}
  y={$joinToolbarY}
  visible={$joinToolbarVisible}
  isFirstLine={$normalizedScript.lines.findIndex(l => l.id === joinToolbarLineId) === 0}
  isLastLine={$normalizedScript.lines.findIndex(l => l.id === joinToolbarLineId) === $normalizedScript.lines.length - 1}
  on:action={handleJoinAction}
  on:close={closeJoinToolbar}
/>
{/if}

<SplitButton
  lineId={splitButtonLineId || 0}
  position={splitButtonPosition || 0}
  x={$splitButtonX}
  y={$splitButtonY}
  visible={$splitButtonVisible}
  on:split={handleSplitConfirm}
/>

<style>
  .center-wrap { display:flex; justify-content:center; width:100%; }
  .content { width:80%; max-width:1400px; padding:16px 24px 160px; }
  .chapter-title { margin-top:0; text-align:center; }

  .line-row { display:flex; gap:12px; align-items:stretch; margin:6px 0; }
  .line-bubble { 
    flex:1; 
    background: var(--bg, transparent); 
    padding:8px 10px; 
    border-radius:6px; 
    white-space:pre-wrap; 
    margin:0; 
    transition: box-shadow 120ms ease;
    position: relative;
    overflow: hidden;
  }

  .skeleton-line { height:1em; margin:10px 0; border-radius:4px; background:linear-gradient(180deg, rgba(0,0,0,0.06), rgba(0,0,0,0.12), rgba(0,0,0,0.06)); background-size:100% 200%; animation:pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0% { background-position: 0% 0%; } 100% { background-position: 0% 200%; } }

  .placeholder { color:var(--app-text-muted); }

  .character-col { width:56px; display:flex; flex-direction:column; align-items:flex-end; gap:4px; padding-top:2px; }
  .character-chip { cursor:default; user-select:none; font-size:12px; padding:2px 6px; border-radius:10px; background: var(--bg, transparent); color:var(--app-text); white-space:nowrap; }
</style>
