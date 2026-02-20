<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { currentChapter, currentScript, bookRoot, bookRootAbsolutePath } from '$lib/stores/bookState';
  import type { ScriptJson, DialogueJson, DialogueLine, LineItem } from '$lib/types';
  import { writable } from 'svelte/store';
  import { conflictCursor, toolMode } from '$lib/stores/selection';
  import { get } from 'svelte/store';
  import { readTextFile, readDialogueForChapter, readScript, readCentralCharacters, writeCentralCharacters, getRootDirInfo, writeDialogue, writeScript, readJsonRelative } from '$lib/services/fs';
  import { readManifest } from '$lib/services/audio';
  import { characters, buildIdToNameMap, buildNameToIdMap } from '$lib/stores/characters';
  import { addBookCharacterAlias, bookCharacters } from '$lib/stores/bookCharacters';
  import ParagraphRow from '$lib/components/chapter/ParagraphRow.svelte';
  import RawTextBlock from '$lib/components/chapter/RawTextBlock.svelte';
  import Dropdown from '$lib/components/common/Dropdown.svelte';
  import { audioState } from '$lib/stores/audio';
  
  // Tool modules
  import type { UnifiedLine, MenuContext } from '$lib/components/chapter/tools/types';
  import { displayTitle, recomputeStats } from '$lib/components/chapter/tools/shared/toolUtils';
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

  const UNKNOWN_SPEAKER_LABEL = 'Unknown';

  function clampConfidence(value: number | null | undefined): number {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    if (numeric < 0) return 0;
    if (numeric > 1) return 1;
    return numeric;
  }

  function getUnknownThreshold(): number {
    const hints = get(parserHints);
    const threshold = hints?.attribution?.unknownThreshold;
    return clampConfidence(typeof threshold === 'number' ? threshold : 0.62);
  }

  function normalizeAttribution(
    existing: any,
    candidates: Array<{ name: string; characterId?: string | null; confidence: number }>,
    characterName: string | null,
    threshold: number,
    fallbackSourceAlias: string | null = null,
    fallbackSourceCandidates: string[] = []
  ) {
    const sortedCandidates = [...(Array.isArray(candidates) ? candidates : [])]
      .filter((candidate) => candidate && candidate.name)
      .map((candidate) => ({
        ...candidate,
        characterId: candidate.characterId ?? null,
        confidence: clampConfidence(candidate.confidence),
      }))
      .sort((left, right) => right.confidence - left.confidence);

    const normalizedName = String(characterName || '').trim().toLowerCase();
    const hasAssignedSpeaker = !!characterName && normalizedName !== UNKNOWN_SPEAKER_LABEL.toLowerCase();
    const top = sortedCandidates[0]?.confidence ?? 0;
    const inferredTop = sortedCandidates.length > 0 ? top : (hasAssignedSpeaker ? 1 : 0);
    const second = sortedCandidates[1]?.confidence ?? 0;
    const margin = Math.max(0, inferredTop - second);
    const confidence = clampConfidence(typeof existing?.confidence === 'number' ? existing.confidence : inferredTop);
    const risk = clampConfidence(
      typeof existing?.misattributionRisk === 'number'
        ? existing.misattributionRisk
        : (1 - inferredTop) * 0.7 + (1 - margin) * 0.3
    );
    const sourceAlias =
      typeof existing?.sourceAlias === 'string'
        ? existing.sourceAlias
        : fallbackSourceAlias;
    const sourceCandidates = Array.isArray(existing?.sourceCandidates)
      ? existing.sourceCandidates.filter((candidate: any) => typeof candidate === 'string' && candidate.trim().length > 0)
      : fallbackSourceCandidates;

    let resolutionStatus: 'auto' | 'unknown' | 'user_confirmed' =
      existing?.resolutionStatus === 'auto' || existing?.resolutionStatus === 'unknown' || existing?.resolutionStatus === 'user_confirmed'
        ? existing.resolutionStatus
        : 'auto';

    const narratorLabel = 'narrator';
    const isNarratorWithoutCandidates =
      normalizedName === narratorLabel &&
      sortedCandidates.length === 0;

    if (resolutionStatus !== 'user_confirmed') {
      if (!characterName || normalizedName === UNKNOWN_SPEAKER_LABEL.toLowerCase()) {
        resolutionStatus = 'unknown';
      } else if (isNarratorWithoutCandidates) {
        resolutionStatus = 'auto';
      } else if (sortedCandidates.length > 0 && top < threshold) {
        resolutionStatus = 'unknown';
      } else {
        resolutionStatus = 'auto';
      }
    }

    return {
      confidence,
      topCandidateConfidence: inferredTop,
      marginToSecond: margin,
      misattributionRisk: risk,
      resolutionStatus,
      thresholdUsed: clampConfidence(typeof existing?.thresholdUsed === 'number' ? existing.thresholdUsed : threshold),
      sourceAlias: sourceAlias || null,
      sourceCandidates,
      candidates: sortedCandidates,
    };
  }

  // Helper to normalize v1.0 or v2.0 data to unified format
  function normalizeLine(line: DialogueLine | LineItem, charactersMap?: Map<string, string>): UnifiedLine {
    const unknownThreshold = getUnknownThreshold();
    // v2.0 format has characterId
    if ('characterId' in line) {
      const metadataTags = (line.metadata?.customTags || {}) as Record<string, any>;
      const fallbackSourceAlias = typeof metadataTags.sourceAlias === 'string' ? metadataTags.sourceAlias : null;
      const fallbackSourceCandidates = Array.isArray(metadataTags.sourceCandidates)
        ? metadataTags.sourceCandidates.filter((candidate): candidate is string => typeof candidate === 'string')
        : [];
      const rawAttribution = (line as any).attribution || metadataTags.attribution || null;

      let charName: string | null = null;
      if (line.characterId) {
        charName = charactersMap?.get(line.characterId) || line.characterId;
      }
      // Treat span { start: 0, end: 0 } as null (indicates edited line)
      const normalizedSpan = (line.span && line.span.start === 0 && line.span.end === 0) ? null : line.span;
      const normalizedCandidates = line.candidates.map(c => {
        const candName = c.characterId ? (charactersMap?.get(c.characterId) || c.characterId) : null;
        return {
          name: candName || UNKNOWN_SPEAKER_LABEL,
          characterId: c.characterId || null,
          confidence: clampConfidence(c.confidence)
        };
      });

      const normalizedAttribution = normalizeAttribution(
        rawAttribution,
        normalizedCandidates,
        charName,
        unknownThreshold,
        fallbackSourceAlias,
        fallbackSourceCandidates
      );

      if (normalizedAttribution.resolutionStatus === 'unknown') {
        charName = UNKNOWN_SPEAKER_LABEL;
      }

      return {
        id: line.id,
        text: line.text,
        span: normalizedSpan,
        characterName: charName,
        candidates: normalizedCandidates.map(c => ({ name: c.name, confidence: c.confidence })),
        isConflict: line.isConflict || normalizedAttribution.resolutionStatus === 'unknown',
        attribution: normalizedAttribution
      };
    }
    // v1.0 format has chosenSpeaker
    const normalizedSpan = (line.span && line.span.start === 0 && line.span.end === 0) ? null : line.span;
    const normalizedCandidates = (line.candidates || []).map((candidate) => ({
      name: candidate.name || UNKNOWN_SPEAKER_LABEL,
      confidence: clampConfidence(candidate.confidence),
    }));
    const normalizedAttribution = normalizeAttribution(
      (line as any).attribution || null,
      normalizedCandidates,
      line.chosenSpeaker,
      unknownThreshold
    );
    const normalizedSpeaker = normalizedAttribution.resolutionStatus === 'unknown'
      ? UNKNOWN_SPEAKER_LABEL
      : line.chosenSpeaker;

    return {
      id: line.id,
      text: line.text,
      span: normalizedSpan,
      characterName: normalizedSpeaker,
      candidates: normalizedCandidates,
      isConflict: line.isConflict || normalizedAttribution.resolutionStatus === 'unknown',
      attribution: normalizedAttribution
    };
  }

  function buildAliasMaps(centralCharacters: any[] | null | undefined) {
    const aliasToCanonical = new Map<string, string>();
    const nameToCanonical = new Map<string, string>();
    for (const entry of centralCharacters ?? []) {
      if (!entry?.name) continue;
      const canonical = String(entry.name);
      nameToCanonical.set(canonical.toLowerCase(), canonical);
      const aliases = Array.isArray(entry.aliases) ? entry.aliases : [];
      for (const alias of aliases) {
        const key = String(alias || '').trim().toLowerCase();
        if (!key) continue;
        aliasToCanonical.set(key, canonical);
      }
    }
    return { aliasToCanonical, nameToCanonical };
  }

  function canonicalizeName(
    name: string | null | undefined,
    aliasToCanonical: Map<string, string>,
    nameToCanonical: Map<string, string>
  ): string | null {
    if (!name) return null;
    const key = String(name).trim().toLowerCase();
    if (!key) return null;
    if (key === UNKNOWN_SPEAKER_LABEL.toLowerCase()) return UNKNOWN_SPEAKER_LABEL;
    return aliasToCanonical.get(key) || nameToCanonical.get(key) || name;
  }

  // Helper to normalize script data
  async function normalizeScript(
    data: ScriptJson | DialogueJson | null,
    root?: string
  ): Promise<{ lines: UnifiedLine[]; stats: any; hadAliasChanges: boolean } | null> {
    if (!data) return null;
    
    // Build character ID to name map for v2.0 using centralized helper
    let charactersMap = new Map<string, string>();
    let aliasToCanonical = new Map<string, string>();
    let nameToCanonical = new Map<string, string>();
    if ('formatVersion' in data && data.formatVersion === '2.0') {
      // For v2.0, load centralized characters.json to map IDs to names
      if (root) {
        try {
          const centralChars = await readCentralCharacters(root);
          console.log('[ChapterView] Central characters loaded:', centralChars?.characters?.length, 'characters');
          if (centralChars?.characters) {
            // Use centralized helper to build the map
            charactersMap = buildIdToNameMap(centralChars.characters);
            console.log('[ChapterView] CharactersMap built with', charactersMap.size, 'entries');
            const aliasMaps = buildAliasMaps(centralChars.characters);
            aliasToCanonical = aliasMaps.aliasToCanonical;
            nameToCanonical = aliasMaps.nameToCanonical;
          }
        } catch (e) {
          console.warn('Failed to load central characters:', e);
        }
      }
    }

    if (aliasToCanonical.size === 0 || nameToCanonical.size === 0) {
      const centralChars = root ? await readCentralCharacters(root) : null;
      const aliasMaps = buildAliasMaps(centralChars?.characters);
      aliasToCanonical = aliasMaps.aliasToCanonical;
      nameToCanonical = aliasMaps.nameToCanonical;
    }

    let hadAliasChanges = false;
    const lines = data.lines.map(line => {
      const normalized = normalizeLine(line, charactersMap);
      const canonical = canonicalizeName(normalized.characterName, aliasToCanonical, nameToCanonical);
      if (canonical && canonical !== normalized.characterName) {
        hadAliasChanges = true;
      }
      const nextCandidates = normalized.candidates.map(c => {
        const nextName = canonicalizeName(c.name, aliasToCanonical, nameToCanonical) || c.name;
        if (nextName !== c.name) hadAliasChanges = true;
        return { ...c, name: nextName };
      });
      const nextAttribution = normalized.attribution
        ? {
            ...normalized.attribution,
            candidates: normalized.attribution.candidates.map((candidate) => {
              const nextName = canonicalizeName(candidate.name, aliasToCanonical, nameToCanonical) || candidate.name;
              return { ...candidate, name: nextName };
            }),
          }
        : undefined;
      return { ...normalized, characterName: canonical, candidates: nextCandidates, attribution: nextAttribution };
    });
    
    return {
      lines,
      stats: data.stats,
      hadAliasChanges
    };
  }

  // Internal normalized script store
  const normalizedScript = writable<{ lines: UnifiedLine[]; stats: any } | null>(null);
  
  // Cache for character name-to-ID map (built once when needed)
  let characterNameToIdMap: Map<string, string> | null = null;
  
  async function ensureCharacterNameToIdMap() {
    if (characterNameToIdMap) return characterNameToIdMap;
    
    const root = get(bookRoot);
    if (!root) return new Map();
    
    try {
      const centralChars = await readCentralCharacters(root);
      if (centralChars?.characters) {
        characterNameToIdMap = buildNameToIdMap(centralChars.characters);
      } else {
        characterNameToIdMap = new Map();
      }
    } catch (e) {
      console.warn('[ChapterView] Failed to load characters for name-to-ID map:', e);
      characterNameToIdMap = new Map();
    }
    
    return characterNameToIdMap;
  }

  function slugifyCharacterId(name: string): string {
    const cleaned = name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    return cleaned.length ? cleaned.replace(/\s/g, '-') : 'character';
  }

  function stripNumericSlugSuffix(name: string): string {
    return String(name || '').trim().toLowerCase().replace(/(?:[-_]\d+)+$/g, '');
  }

  function resolveExistingCanonicalForSlugVariant(
    name: string,
    existingByName: Map<string, any>
  ): string | null {
    const normalized = String(name || '').trim().toLowerCase();
    if (!normalized) return null;
    if (existingByName.has(normalized)) return null;

    const base = stripNumericSlugSuffix(normalized);
    if (!base || base === normalized) return null;

    const baseEntry = existingByName.get(base);
    if (!baseEntry || typeof baseEntry.name !== 'string') return null;
    return baseEntry.name;
  }

  async function ensureCentralCharactersForNames(names: string[]): Promise<void> {
    const root = get(bookRoot);
    if (!root) return;

    let central = await readCentralCharacters(root);
    if (!central || !Array.isArray(central.characters)) {
      central = { formatVersion: '2.0', characters: [] } as any;
    }

    const existingByName = new Map(
      central.characters
        .filter((c: any) => c && typeof c.name === 'string')
        .map((c: any) => [c.name.toLowerCase(), c] as const)
    );
    const existingIds = new Set(
      central.characters
        .map((c: any) => (c?.id ? String(c.id).toLowerCase() : null))
        .filter((id: string | null): id is string => !!id)
    );

    let changed = false;
    for (const entry of central.characters) {
      if (!entry || typeof entry.name !== 'string') continue;
      if (!entry.id) {
        let id = slugifyCharacterId(entry.name);
        let suffix = 2;
        while (existingIds.has(id)) {
          id = `${slugifyCharacterId(entry.name)}-${suffix}`;
          suffix += 1;
        }
        entry.id = id;
        existingIds.add(id);
        changed = true;
      }
    }
    for (const rawName of names) {
      const name = rawName.trim();
      if (!name) continue;
      const key = name.toLowerCase();
      if (existingByName.has(key)) continue;

      const canonicalForVariant = resolveExistingCanonicalForSlugVariant(name, existingByName);
      if (canonicalForVariant) {
        const canonicalKey = canonicalForVariant.toLowerCase();
        const canonicalEntry = existingByName.get(canonicalKey);
        if (canonicalEntry) {
          const aliases = Array.isArray(canonicalEntry.aliases) ? canonicalEntry.aliases : [];
          if (!aliases.some((alias: string) => String(alias || '').trim().toLowerCase() === key)) {
            canonicalEntry.aliases = [...aliases, name];
            changed = true;
          }
          existingByName.set(key, canonicalEntry);
        }
        continue;
      }

      let id = slugifyCharacterId(name);
      let suffix = 2;
      while (existingIds.has(id)) {
        id = `${slugifyCharacterId(name)}-${suffix}`;
        suffix += 1;
      }

      existingIds.add(id);
      central.characters.push({
        id,
        name,
        gender: 'Unknown',
        aliases: [],
        color: null,
        notes: '',
        stats: {
          totalLines: 0,
          chapterCount: 0,
        },
        voice: null,
        provider: null,
        voiceId: null,
        voiceMeta: null,
        manifestStats: null,
        count: 0,
        firstAppearance: null,
        chapterCount: 0
      });
      existingByName.set(key, central.characters[central.characters.length - 1]);
      changed = true;
    }

    if (!changed) return;

    const savedLocally = await writeCentralCharacters(root, central as any);
    if (!savedLocally) {
      const backendRoot = get(bookRootAbsolutePath);
      if (!backendRoot) {
        console.warn('[ChapterView] Failed to save characters.json: no backend root path available');
      } else {
        try {
          const payload = {
            file_path: 'characters.json',
            content: central
          };
          const response = await fetch('http://127.0.0.1:8010/api/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });

          if (response.ok) {
            console.log('[ChapterView] Successfully saved characters.json via API');
          } else {
            const errorData = await response.json();
            console.error('[ChapterView] Failed to save characters.json via API:', errorData.detail || response.statusText);
          }
        } catch (error) {
          console.error('[ChapterView] Network error saving characters.json via API:', error);
        }
      }
    }
    bookCharacters.set(central as any);
    characterNameToIdMap = null;
  }

  // Track which format version we're using for saving
  let isV2Format = false;

  const rawText = writable<string | null>(null);
  const loading = writable<boolean>(false);
  let hoveredCharacter: string | null = null;
  const setHovered = (s: string | null) => { hoveredCharacter = s; };

  type Run = { text: string; characterId: string | null; lineId?: number };
  const paragraphRuns = writable<Run[][]>([]);
  const LINE_FADE_DURATION_MS = 300;
  const LINE_FADE_STAGGER_MS = 50;
  const APPROX_CHARS_PER_LINE = 72;
  const ROW_HIGHLIGHT_MAX_DURATION_MS = 750;
  const ROW_HIGHLIGHT_MIN_DURATION_MS = 750;
  const ROW_HIGHLIGHT_MS_PER_CHARACTER = 3;
  const HIGHLIGHT_START_LEAD_MS = 4000;
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

  function estimateRowLineChunkCount(runs: Run[]): number {
    let totalChars = 0;
    for (const run of runs) {
      totalChars += run.text?.length ?? 0;
    }
    if (totalChars <= 0) return 0;
    return Math.max(1, Math.ceil(totalChars / APPROX_CHARS_PER_LINE));
  }

  function estimateRowTypingDurationMs(runs: Run[]): number {
    const lineCount = estimateRowLineChunkCount(runs);
    if (lineCount <= 0) return 0;
    return ((lineCount - 1) * LINE_FADE_STAGGER_MS) + LINE_FADE_DURATION_MS;
  }

  function estimateRowHighlightDurationMs(runs: Run[]): number {
    const totalChars = runs.reduce((sum, run) => {
      const speaker = run.characterId?.toLowerCase();
      const canHighlight = Boolean(run.characterId) && speaker !== 'narrator';
      return sum + (canHighlight ? (run.text?.length ?? 0) : 0);
    }, 0);
    if (totalChars <= 0) return 0;
    return Math.max(
      ROW_HIGHLIGHT_MIN_DURATION_MS,
      Math.min(ROW_HIGHLIGHT_MAX_DURATION_MS, totalChars * ROW_HIGHLIGHT_MS_PER_CHARACTER)
    );
  }

  function applyStaticPlanForCurrentChapter(runsSnapshot: Run[][], chapterTitle: string) {
    const rowCount = runsSnapshot.length;
    paragraphAnimateOnLoad = new Array<boolean>(rowCount).fill(false);
    paragraphTypingDelays = new Array<number>(rowCount).fill(0);
    paragraphHighlightDelays = new Array<number>(rowCount).fill(0);
    paragraphRevealDelays = new Array<number>(rowCount).fill(0);
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

  async function buildChapterAnimationPlan(chapterTitle: string, runsSnapshot: Run[][]): Promise<void> {
    const seq = ++chapterAnimationPlanSeq;
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

    if (seq !== chapterAnimationPlanSeq) return;
    if (get(currentChapter)?.title !== chapterTitle) return;
    if (animationCancelledByScroll) {
      applyStaticPlanForCurrentChapter(runsSnapshot, chapterTitle);
      return;
    }

    const animatedRowIndices: number[] = [];
    for (let index = 0; index < runsSnapshot.length; index += 1) {
      animatedRowIndices.push(index);
    }

    const animate = new Array<boolean>(runsSnapshot.length).fill(false);
    const typingDelays = new Array<number>(runsSnapshot.length).fill(0);
    const highlightDelays = new Array<number>(runsSnapshot.length).fill(0);
    const revealDelays = new Array<number>(runsSnapshot.length).fill(0);

    let nextGlobalLineStartMs = 0;
    const typingEndByRow = new Map<number, number>();
    for (const rowIndex of animatedRowIndices) {
      animate[rowIndex] = true;
      typingDelays[rowIndex] = nextGlobalLineStartMs;
      const typingDuration = estimateRowTypingDurationMs(runsSnapshot[rowIndex] ?? []);
      typingEndByRow.set(rowIndex, typingDelays[rowIndex] + typingDuration);

      const lineCount = estimateRowLineChunkCount(runsSnapshot[rowIndex] ?? []);
      nextGlobalLineStartMs += Math.max(1, lineCount) * LINE_FADE_STAGGER_MS;
    }

    const cumulativeTypingMs = Math.max(0, ...Array.from(typingEndByRow.values()));

    const highlightStartMs = Math.max(0, cumulativeTypingMs - HIGHLIGHT_START_LEAD_MS);
    let highlightCursorMs = highlightStartMs;
    for (let i = animatedRowIndices.length - 1; i >= 0; i -= 1) {
      const rowIndex = animatedRowIndices[i];
      const typingEndMs = typingEndByRow.get(rowIndex) ?? 0;
      highlightDelays[rowIndex] = Math.max(0, highlightCursorMs - typingEndMs);
      const highlightDuration = estimateRowHighlightDurationMs(runsSnapshot[rowIndex] ?? []);
      highlightCursorMs += highlightDuration;
    }

    for (let index = 0; index < runsSnapshot.length; index += 1) {
      if (!animate[index]) revealDelays[index] = highlightCursorMs;
    }

    paragraphAnimateOnLoad = animate;
    paragraphTypingDelays = typingDelays;
    paragraphHighlightDelays = highlightDelays;
    paragraphRevealDelays = revealDelays;
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

  function candidatesEqual(a: { name: string; confidence: number }[] = [], b: { name: string; confidence: number }[] = []): boolean {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (a[i]?.name !== b[i]?.name || a[i]?.confidence !== b[i]?.confidence) return false;
    }
    return true;
  }

  function syncCurrentFromNormalized(scr: { lines: UnifiedLine[]; stats: any }) {
    if (syncingFromCurrent) return;
    syncingFromNormalized = true;
    const ch = get(currentChapter);
    const v1Script: ScriptJson = {
      chapter: ch?.title || '',
      sourceFile: ch?.path || '',
      lines: scr.lines.map(l => ({
        id: l.id,
        text: l.text,
        span: l.span,
        chosenSpeaker: l.characterName,
        candidates: l.candidates,
        isConflict: l.isConflict,
        attribution: l.attribution
      })),
      stats: scr.stats
    };
    currentScript.set(v1Script);
    syncingFromNormalized = false;
  }

  function syncNormalizedFromCurrent(v1: ScriptJson, normalized: { lines: UnifiedLine[]; stats: any } | null) {
    if (!normalized || syncingFromNormalized) return;
    const byId = new Map(v1.lines.map(l => [l.id, l] as const));
    let changed = false;
    const nextLines = normalized.lines.map(line => {
      const src = byId.get(line.id);
      if (!src) return line;
      const nextName = src.chosenSpeaker ?? null;
      const nextCandidates = Array.isArray(src.candidates) ? src.candidates : [];
      const nextConflict = !!src.isConflict;
      const nextAttribution = normalizeAttribution(
        src.attribution || line.attribution || null,
        nextCandidates.map((candidate) => ({
          name: candidate.name,
          confidence: candidate.confidence,
          characterId: null,
        })),
        nextName,
        getUnknownThreshold()
      );
      const sameCandidates = candidatesEqual(line.candidates, nextCandidates);
      const sameAttribution = JSON.stringify(line.attribution || null) === JSON.stringify(nextAttribution || null);
      if (line.characterName !== nextName || line.isConflict !== nextConflict || !sameCandidates || !sameAttribution) {
        changed = true;
        return { ...line, characterName: nextName, candidates: nextCandidates, isConflict: nextConflict, attribution: nextAttribution };
      }
      return line;
    });
    if (!changed) return;
    syncingFromCurrent = true;
    const next = { ...normalized, lines: nextLines };
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
  }

  async function refreshGeneratedAudioLineIds() {
        const normalizeAudioLineText = (value: string): string =>
          value
            .normalize('NFKC')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();

    const ch = get(currentChapter);
    const scr = get(normalizedScript);
    if (!ch || !scr) {
      generatedAudioLineIds = new Set();
      return;
    }

    const chapterTitle = ch.title;
    const namesToCheck = new Set<string>();
    const next = new Set<number>();
    const nextTexts = new Set<string>();

    // Primary source: chapter-level manifest keyed by line ID
    const chapterManifest = await readJsonRelative<{ lines?: Array<{ id: number | string; text?: string; skipped?: boolean; output?: string | null }> }>(
      `${chapterTitle}/audio_lines/manifest.json`
    );
    if (Array.isArray(chapterManifest?.lines)) {
      for (const line of chapterManifest.lines) {
        const normalizedLineId = Number(line?.id);
        if (!Number.isFinite(normalizedLineId)) continue;
        if (line?.skipped) continue;
        if (!line?.output) continue;
        next.add(normalizedLineId);
        if (typeof line?.text === 'string') {
          const normalizedText = normalizeAudioLineText(line.text);
          if (normalizedText) nextTexts.add(normalizedText);
        }
      }
    }

    for (const line of scr.lines) {
      if (line.characterName) namesToCheck.add(line.characterName);
    }

    const root = get(bookRoot);
    if (root) {
      try {
        const central = await readCentralCharacters(root);
        for (const character of central?.characters ?? []) {
          if (character?.name) {
            namesToCheck.add(String(character.name));
          }
        }
      } catch (error) {
        console.warn('[ChapterView] Failed to load central characters for audio marker refresh:', error);
      }
    }

    if (next.size === 0) {
      await Promise.all(
        Array.from(namesToCheck).map(async (characterName) => {
          const manifest = await readManifest(chapterTitle, characterName);
          if (!manifest?.clips) return;
          for (const clip of manifest.clips) {
            const clipChapter = String((clip as any).chapter ?? '').trim();
            const normalizedLineId = Number((clip as any).id);
            if (clipChapter === chapterTitle && Number.isFinite(normalizedLineId)) {
              next.add(normalizedLineId);
            }
          }
        })
      );
    }

    const latestChapter = get(currentChapter);
    if (!latestChapter || latestChapter.title !== chapterTitle) return;

    generatedAudioLineIds = next;
    generatedAudioLineTexts = nextTexts;

    const nextCache = new Map<string, boolean>();
    for (const line of scr.lines) {
      if (!line.characterName) continue;
      const cacheKey = `${chapterTitle}-${line.characterName}-${line.id}`;
      nextCache.set(cacheKey, next.has(line.id));
    }
    audioExistsCache = nextCache;
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
    closeGeneratePrompt();
    const ch = get(currentChapter);
    await handleGenerateLineAudio(generatePromptLineId, normalizedScript, audioExistsCache, (id) => { generatingLineId = id; });
  }

  async function handleLineAudioContextMenu(lineId: number, characterId: string, event: MouseEvent) {
    event.preventDefault();
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
      
      const rootInfo = getRootDirInfo();
      console.log('[ChapterView] Saving changes...', { format: 'v3.0', numLines: scr.lines.length, chapter: ch.title, root, rootDirHandle: rootInfo });
      
      // Save in v2.0 format (dialogue.json)
      // Build name-to-ID map to convert character names to IDs
      const allNames = new Set<string>();
      for (const line of scr.lines) {
        if (line.characterName) allNames.add(line.characterName);
        for (const c of line.candidates || []) {
          if (c?.name) allNames.add(c.name);
        }
      }
      await ensureCentralCharactersForNames(Array.from(allNames));
      const nameToIdMap = await ensureCharacterNameToIdMap();
      const unknownThreshold = getUnknownThreshold();
      
      const characterBreakdown: Record<string, number> = {};
      for (const line of scr.lines) {
        if (line.characterName) {
          // Convert character name to ID - NO FALLBACK, must exist in map
          const charId = nameToIdMap.get(line.characterName.toLowerCase());
          if (charId) {
            characterBreakdown[charId] = (characterBreakdown[charId] || 0) + 1;
          } else {
            console.warn(`[ChapterView] Character "${line.characterName}" not found in characters.json, skipping from breakdown`);
          }
        }
      }
      
      const v2Dialogue: DialogueJson = {
        formatVersion: '3.0',
        chapterId: ch.title,
        lines: scr.lines.map(l => {
          const attribution = normalizeAttribution(
            l.attribution || null,
            (l.candidates || []).map((candidate) => ({
              name: candidate.name,
              characterId: candidate.name ? (nameToIdMap.get(candidate.name.toLowerCase()) || null) : null,
              confidence: candidate.confidence,
            })),
            l.characterName,
            unknownThreshold
          );

          const nameLower = String(l.characterName || '').trim().toLowerCase();
          // Convert character name to ID - default to narrator if not found
          let characterId: string | null = null;
          if (attribution.resolutionStatus === 'unknown' || nameLower === UNKNOWN_SPEAKER_LABEL.toLowerCase()) {
            characterId = null;
          } else if (l.characterName) {
            characterId = nameToIdMap.get(l.characterName.toLowerCase()) || null;
            if (!characterId) {
              console.warn(`[ChapterView] Character "${l.characterName}" not found in characters.json for line ${l.id}, defaulting to narrator`);
              characterId = 'narrator';
            }
          } else {
            characterId = null;
          }
          
          return {
            id: l.id,
            characterId,
            text: l.text,
            span: l.span || { start: 0, end: 0 },
            metadata: {
              emotion: null,
              intensity: 1.0,
              pacing: null,
              prefix: null,
              customTags: {
                attribution,
                sourceAlias: attribution.sourceAlias,
                sourceCandidates: attribution.sourceCandidates,
              }
            },
            candidates: l.candidates
              .map(c => {
                // Convert candidate name to ID - NO FALLBACK
                const candidateId = c.name ? (nameToIdMap.get(c.name.toLowerCase()) || null) : null;
                if (c.name && !candidateId) {
                  console.warn(`[ChapterView] Candidate "${c.name}" not found in characters.json`);
                  return null;
                }
                if (!candidateId) return null;
                return {
                  characterId: candidateId,
                  confidence: c.confidence
                };
              })
              .filter((c): c is { characterId: string; confidence: number } => !!c),
            isConflict: l.isConflict || attribution.resolutionStatus === 'unknown',
            attribution
          };
        }),
        stats: {
          totalLines: scr.lines.length,
          conflicts: scr.stats?.numConflicts || 0,
          characterBreakdown
        }
      };
      
      const relativePath = `${ch.title}/dialogue.json`;
      console.log('[ChapterView] Writing dialogue to relative path:', relativePath);

      const savedLocally = await writeDialogue(relativePath, v2Dialogue);
      if (savedLocally) {
        console.log('[ChapterView] ✓ Successfully saved dialogue.json via File System API');
      } else {
        const backendRoot = get(bookRootAbsolutePath);
        if (!backendRoot) {
          console.warn('[ChapterView] ✗ Save failed: no backend root path available');
          return;
        }

        try {
          const payload = {
            file_path: relativePath,
            content: v2Dialogue
          };
          const response = await fetch('http://127.0.0.1:8010/api/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });

          if (response.ok) {
            console.log('[ChapterView] ✓ Successfully saved dialogue.json via API');
          } else {
            const errorData = await response.json();
            console.error('[ChapterView] ✗ Failed to save dialogue.json via API:', errorData.detail || response.statusText);
          }
        } catch (error) {
          console.error('[ChapterView] ✗ Network error saving dialogue.json via API:', error);
        }
      }
    }, 350);
  }

  type ParagraphSpan = { text: string; start: number; end: number };
  function splitParagraphsWithOffsets(src: string): ParagraphSpan[] {
    const result: ParagraphSpan[] = [];
    const n = src.length;
    let lineStart = 0;
    const pushSpan = (s0: number, e0: number) => {
      let s = s0;
      let e = e0;
      while (s < e && /\s/.test(src[s]) && src[s] !== '\n' && src[s] !== '\r') s++;
      while (e > s && /\s/.test(src[e - 1]) && src[e - 1] !== '\n' && src[e - 1] !== '\r') e--;
      if (e > s) result.push({ text: src.slice(s, e), start: s, end: e });
    };
    let i = 0;
    while (i < n) {
      const ch = src[i];
      if (ch === '\n' || ch === '\r') {
        pushSpan(lineStart, i);
        if (ch === '\r' && i + 1 < n && src[i + 1] === '\n') {
          i += 1;
        }
        i += 1;
        lineStart = i;
        continue;
      }
      i += 1;
    }
    pushSpan(lineStart, n);
    return result;
  }

  function buildRunsForParagraphLoose(paragraph: string, paraStart: number, paraEnd: number, lines: UnifiedLine[], startIdx: number): { runs: Run[]; nextIdx: number } {
    const runs: Run[] = [];
    let cursor = 0;
    let i = startIdx;
    while (i < lines.length) {
      const line = lines[i];
      const hasSpan = line.span && typeof line.span.start === 'number' && typeof line.span.end === 'number';
      
      if (!hasSpan) {
        if (runs.length > 0) break;
        runs.push({ text: line.text, characterId: line.characterName, lineId: line.id });
        return { runs, nextIdx: i + 1 };
      }

      const startAbs = line.span.start as number;
      const endAbs = line.span.end as number;
      if (startAbs >= paraEnd) break;
      if (endAbs <= paraStart) { i++; continue; }
      const start = Math.max(0, startAbs - paraStart);
      const end = Math.min(paragraph.length, endAbs - paraStart);

      if (start < cursor) { i++; continue; }
      if (cursor < start) {
        runs.push({ text: paragraph.slice(cursor, start), characterId: null });
      }
      const textSlice = paragraph.slice(start, Math.min(end, paragraph.length));
      runs.push({ text: textSlice, characterId: line.characterName, lineId: line.id });
      cursor = Math.min(end, paragraph.length);
      if (cursor >= paragraph.length) { i++; break; }
      i++;
    }

    if (cursor < paragraph.length) runs.push({ text: paragraph.slice(cursor), characterId: null });
    return { runs, nextIdx: i };
  }

  function rebuildParagraphRuns() {
    const scr = get(normalizedScript);
    const txt = get(rawText);
    if (!scr) { paragraphRuns.set([]); return; }
    
    if (!txt) {
      const all: Run[][] = scr.lines.map(line => [
        { text: line.text, characterId: line.characterName, lineId: line.id }
      ]);
      paragraphRuns.set(all);
      return;
    }
    
    const paras = splitParagraphsWithOffsets(txt);
    const all: Run[][] = [];
    let idx = 0;
    
    while (idx < scr.lines.length && !scr.lines[idx].span) {
      const line = scr.lines[idx];
      all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
      idx++;
    }
    
    for (const p of paras) {
      if (idx >= scr.lines.length) break;
      
      const { runs, nextIdx } = buildRunsForParagraphLoose(p.text, p.start, p.end, scr.lines, idx);
      
      if (runs.length === 0 || (runs.length === 1 && runs[0].characterId === null && runs[0].text === p.text)) {
        all.push([{ text: p.text, characterId: 'Narrator' }]);
        idx = Math.min(idx + 1, scr.lines.length);
      } else {
        all.push(runs);
        idx = nextIdx;
      }
    }
    
    while (idx < scr.lines.length && !scr.lines[idx].span) {
      const line = scr.lines[idx];
      all.push([{ text: line.text, characterId: line.characterName, lineId: line.id }]);
      idx++;
    }
    
    paragraphRuns.set(all);
  }

  $: if ($currentChapter) {
    const seq = ++chapterLoadSeq;
    (async () => {
      console.log('[ChapterView] Reactive block triggered - loading chapter:', $currentChapter.title);
      loading.set(true);
      normalizedScript.set(null);
      rawText.set(null);
      paragraphRuns.set([]);
      isV2Format = false;
      const ch: any = $currentChapter;
      console.log('[ChapterView] Chapter path:', ch.path);
      try {
        const root = get(bookRoot);
        if (ch && root) {
          const dialogue = await readDialogueForChapter(ch.title);
          if (seq !== chapterLoadSeq) return; // superseded by newer chapter switch
          if (dialogue) {
            isV2Format = 'formatVersion' in dialogue && (dialogue.formatVersion === '2.0' || dialogue.formatVersion === '3.0');
            
            const normalized = await normalizeScript(dialogue, root);
            if (seq !== chapterLoadSeq) return; // superseded
            if (normalized) {
              normalizedScript.set({ lines: normalized.lines, stats: normalized.stats });
              const v1Script: ScriptJson = {
                chapter: ch.title,
                sourceFile: ch.path || '',
                lines: normalized.lines.map(l => ({
                  id: l.id,
                  text: l.text,
                  span: l.span,
                  chosenSpeaker: l.characterName,
                  candidates: l.candidates,
                  isConflict: l.isConflict,
                  attribution: l.attribution
                })),
                stats: normalized.stats
              };
              currentScript.set(v1Script);
              if (normalized.hadAliasChanges) {
                characterNameToIdMap = null;
                scheduleSave();
              }
            }
            // Read raw text file - use chapter path directly
            if (ch.path) {
              console.log('[ChapterView] Reading text file from path:', ch.path);
              const txt = await readTextFile(ch.path);
              if (seq !== chapterLoadSeq) return; // superseded
              console.log('[ChapterView] Text file read:', txt ? `${txt.length} characters` : 'null');
              rawText.set(txt);
            }
          } else if (ch.path) {
            // No dialogue file, but we have a text file path
            console.log('[ChapterView] No dialogue found, reading text file from path:', ch.path);
            const txt = await readTextFile(ch.path);
            if (seq !== chapterLoadSeq) return; // superseded
            console.log('[ChapterView] Text file read:', txt ? `${txt.length} characters` : 'null');
            rawText.set(txt);
          }
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

  .placeholder { color:#777; }

  .character-col { width:56px; display:flex; flex-direction:column; align-items:flex-end; gap:4px; padding-top:2px; }
  .character-chip { cursor:default; user-select:none; font-size:12px; padding:2px 6px; border-radius:10px; background: var(--bg, transparent); color:#222; white-space:nowrap; }
</style>
