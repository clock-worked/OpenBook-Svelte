<script lang="ts">
  import { createEventDispatcher, onDestroy, onMount } from 'svelte';
  import { colorForCharacter, hexToRgba, characters } from '$lib/stores/characters';
  import { conflictCursor } from '$lib/stores/selection';
  import type { LineAttributionDecisionTrace, ParagraphRun } from '$lib/types';
  import type { ToolMode } from '$lib/stores/selection';
  import { currentChapter, currentScript } from '$lib/stores/bookState';
  import { audioState, seek } from '$lib/stores/audio';
  import { get } from 'svelte/store';
  import { CheckCircle2, Music } from 'lucide-svelte';

  export let runs: ParagraphRun[] = [];
  export let hoveredCharacter: string | null = null;
  export let setHovered: (s: string | null) => void;
  export let toolMode: ToolMode = 'review';
  export let planReady = false;
  export let animateOnLoad = true;
  export let animationPlanVersion = 0;
  export let typingDelayMs = 0;
  export let highlightDelayMs = 0;
  export let generatedAudioLineIds: Set<number> = new Set();
  export let generatedAudioLineTexts: Set<string> = new Set();

  let layoutVersion = 0;
  
  // Reactively get the current playing element
  $: currentPlayingElement = (() => {
    const currentLineId = $audioState.currentLineId;
    if (currentLineId === null) return null;
    return document.getElementById(`line-${currentLineId}`) as HTMLElement | null;
  })();

  function sortedTextRects(lineElement: HTMLElement): DOMRect[] {
    const textElement = lineElement.querySelector('.run-text') as HTMLElement | null;
    if (!textElement) return [];

    return Array.from(textElement.getClientRects())
      .filter((rect) => rect.width > 0 && rect.height > 0)
      .sort((a, b) => (a.top - b.top) || (a.left - b.left));
  }
  
  // Reactive calculation of marker position - explicitly depends on currentProgress and currentPlayingElement
  $: markerPosition = (() => {
    const progress = currentProgress;
    const _layoutVersion = layoutVersion;
    
    if (!currentPlayingElement || ($audioState.currentLineId == null) || (!$audioState.isPlaying && !$audioState.isPaused)) {
      return null;
    }

    const lineRects = sortedTextRects(currentPlayingElement);
    if (lineRects.length === 0) {
      return null;
    }

    const totalWidth = lineRects.reduce((sum, rect) => sum + rect.width, 0);
    if (totalWidth <= 0) return null;

    const clampedProgress = Math.max(0, Math.min(100, progress));
    const targetDistance = (clampedProgress / 100) * totalWidth;

    let traversed = 0;
    let activeRect = lineRects[0];
    let activeLeft = activeRect.left;

    for (const rect of lineRects) {
      const nextTraversed = traversed + rect.width;
      if (targetDistance <= nextTraversed) {
        activeRect = rect;
        activeLeft = rect.left + (targetDistance - traversed);
        break;
      }

      traversed = nextTraversed;
      activeRect = rect;
      activeLeft = rect.right;
    }

    return {
      left: activeLeft,
      top: activeRect.top,
      height: activeRect.height,
    };
  })();
  
  // Make this reactive so it updates when audioState changes
  $: currentProgress = (() => {
    // Use the audio element's duration if available, as it might be loaded but not yet in state
    const duration = $audioState.audioElement?.duration || $audioState.duration;
    if (!duration || duration === 0 || !isFinite(duration)) return 0;
    const progress = ($audioState.currentTime / duration) * 100;
    return Math.min(100, Math.max(0, progress)); // Clamp between 0 and 100
  })();
  
  
  async function handleAudioSeek(lineId: number, event: MouseEvent) {
    if ($audioState.currentLineId !== lineId || !$audioState.isPlaying) return;
    
    const target = event.currentTarget as HTMLElement;
    const lineRects = sortedTextRects(target);
    if (lineRects.length === 0) return;

    const totalWidth = lineRects.reduce((sum, rect) => sum + rect.width, 0);
    if (totalWidth <= 0 || !$audioState.duration || !isFinite($audioState.duration)) return;

    let chosenIndex = lineRects.findIndex((rect) => event.clientY >= rect.top && event.clientY <= rect.bottom);
    if (chosenIndex === -1) {
      let bestDistance = Number.POSITIVE_INFINITY;
      for (let index = 0; index < lineRects.length; index += 1) {
        const rect = lineRects[index];
        const midY = rect.top + (rect.height / 2);
        const distance = Math.abs(event.clientY - midY);
        if (distance < bestDistance) {
          bestDistance = distance;
          chosenIndex = index;
        }
      }
    }

    const chosenRect = lineRects[Math.max(0, chosenIndex)];
    const clampedX = Math.max(chosenRect.left, Math.min(chosenRect.right, event.clientX));
    const prefixWidth = lineRects
      .slice(0, Math.max(0, chosenIndex))
      .reduce((sum, rect) => sum + rect.width, 0);
    const offsetInRect = clampedX - chosenRect.left;
    const progressRatio = Math.max(0, Math.min(1, (prefixWidth + offsetInRect) / totalWidth));

    seek(progressRatio * $audioState.duration);
  }

  const RUN_HIGHLIGHT_ALPHA = 0.42;
  const RUN_HIGHLIGHT_DIM_ALPHA = 0.16;
  const LINE_FADE_DURATION_MS = 300;
  const LINE_FADE_STAGGER_MS = 50;
  const APPROX_CHARS_PER_LINE = 72;
  const MAX_HIGHLIGHT_DURATION_MS = 1100;
  const MIN_HIGHLIGHT_DURATION_MS = 180;
  const HIGHLIGHT_MS_PER_CHARACTER = 3;
  const TRACE_TOOLTIP_DELAY_MS = 1800;
  const TRACE_TOOLTIP_MOVE_THRESHOLD_PX = 8;
  const TRACE_TOOLTIP_WIDTH_PX = 360;
  const TRACE_TOOLTIP_HEIGHT_PX = 320;

  const dispatch = createEventDispatcher();

  type RunConfidenceBadge = { label: string; low: boolean; unknown: boolean; confirmed: boolean } | null;
  type RunRenderInfo = {
    run: ParagraphRun;
    audioLineNumberLabel: string | null;
    confidenceBadge: RunConfidenceBadge;
    badgeShowsCheck: boolean;
    hasGeneratedAudio: boolean;
    hasPrefixMarker: boolean;
    displayText: string;
  };

  type TraceTooltipEntry = {
    label: string;
    value: string;
  };

  type TraceTooltipData = {
    selectedCandidate: string | null;
    resolutionStatus: string | null;
    confidence: string | null;
    parserBackend: string | null;
    contextGender: string | null;
    contextGenderCue: string | null;
    genderConflict: boolean;
    selectedReasons: string[];
    overrideReason: string | null;
    signalEntries: TraceTooltipEntry[];
  };

  let revealedLineCount = Number.MAX_SAFE_INTEGER;
  let typingAnimationFrame: number | null = null;
  let typingDelayTimeout: number | null = null;
  let typingPhaseTimeout: number | null = null;
  let highlightCharacterCount = Number.MAX_SAFE_INTEGER;
  let highlightAnimationFrame: number | null = null;
  let highlightDelayTimeout: number | null = null;
  let animationPhase: 'idle' | 'typing' | 'highlight' | 'complete' = 'idle';
  let lastAnimationTriggerKey = '';
  let runRenderInfo: RunRenderInfo[] = [];
  let renderedRuns: Array<RunRenderInfo & { typedText: string; isRevealed: boolean; whiteText: string; colorText: string; lineChunks: string[]; revealedLines: number }> = [];
  let totalDisplayCharacters = 0;
  let totalRevealLines = 0;
  let totalHighlightCharacters = 0;
  let traceTooltip: TraceTooltipData | null = null;
  let pendingTraceTooltip: TraceTooltipData | null = null;
  let traceTooltipTimer: number | null = null;
  let traceHoverLineId: number | null = null;
  let traceHoverOrigin: { x: number; y: number } | null = null;
  let traceTooltipX = 12;
  let traceTooltipY = 12;

  function splitTextIntoApproxLines(text: string, maxChars: number): string[] {
    if (!text) return [];
    if (text.length <= maxChars) return [text];

    const parts = text.split(/(\s+)/);
    const lines: string[] = [];
    let current = '';

    for (const part of parts) {
      if (!part) continue;
      const next = current + part;
      const exceeds = next.length > maxChars && current.trim().length > 0;
      if (exceeds) {
        lines.push(current);
        current = part;
      } else {
        current = next;
      }
    }

    if (current.length > 0) lines.push(current);
    return lines.length > 0 ? lines : [text];
  }

  function resetHighlightState() {
    if (highlightAnimationFrame != null) {
      cancelAnimationFrame(highlightAnimationFrame);
      highlightAnimationFrame = null;
    }
    if (highlightDelayTimeout != null) {
      clearTimeout(highlightDelayTimeout);
      highlightDelayTimeout = null;
    }
    highlightCharacterCount = 0;
  }

  function startHighlightSweep() {
    resetHighlightState();
    const beginHighlight = () => {
      animationPhase = 'highlight';

      if (!Number.isFinite(totalHighlightCharacters) || totalHighlightCharacters <= 0) {
        highlightCharacterCount = 0;
        animationPhase = 'complete';
        return;
      }

      const durationMs = Math.max(
        MIN_HIGHLIGHT_DURATION_MS,
        Math.min(MAX_HIGHLIGHT_DURATION_MS, totalHighlightCharacters * HIGHLIGHT_MS_PER_CHARACTER)
      );

      const startedAt = performance.now();
      const tick = (now: number) => {
        const elapsed = now - startedAt;
        const progress = Math.min(1, elapsed / durationMs);
        highlightCharacterCount = Math.floor(progress * totalHighlightCharacters);

        if (progress < 1) {
          highlightAnimationFrame = requestAnimationFrame(tick);
          return;
        }

        highlightCharacterCount = totalHighlightCharacters;
        highlightAnimationFrame = null;
        animationPhase = 'complete';
      };

      highlightAnimationFrame = requestAnimationFrame(tick);
    };

    const safeDelayMs = Math.max(0, Math.floor(highlightDelayMs || 0));
    if (safeDelayMs === 0) {
      beginHighlight();
      return;
    }

    highlightDelayTimeout = window.setTimeout(() => {
      highlightDelayTimeout = null;
      beginHighlight();
    }, safeDelayMs);
  }

  function stopTypingAnimation() {
    if (typingPhaseTimeout != null) {
      clearTimeout(typingPhaseTimeout);
      typingPhaseTimeout = null;
    }
    if (typingDelayTimeout != null) {
      clearTimeout(typingDelayTimeout);
      typingDelayTimeout = null;
    }
    if (typingAnimationFrame != null) {
      cancelAnimationFrame(typingAnimationFrame);
      typingAnimationFrame = null;
    }
  }

  function startTypingAnimation(totalCharacters: number, startDelayMs: number) {
    stopTypingAnimation();
    resetHighlightState();
    animationPhase = 'idle';

    if (!Number.isFinite(totalCharacters) || totalCharacters <= 0 || totalRevealLines <= 0) {
      revealedLineCount = 0;
      animationPhase = 'complete';
      return;
    }

    const durationMs = ((Math.max(1, totalRevealLines) - 1) * LINE_FADE_STAGGER_MS) + LINE_FADE_DURATION_MS;

    const beginAnimation = () => {
      animationPhase = 'typing';
      revealedLineCount = 0;
      const startedAt = performance.now();

      const tick = (now: number) => {
        const elapsed = now - startedAt;
        const startedLines = Math.max(0, Math.floor(elapsed / LINE_FADE_STAGGER_MS) + 1);
        revealedLineCount = Math.min(totalRevealLines, startedLines);

        if (elapsed < durationMs) {
          typingAnimationFrame = requestAnimationFrame(tick);
          return;
        }

        revealedLineCount = totalRevealLines;
        typingAnimationFrame = null;
        startHighlightSweep();
      };

      typingAnimationFrame = requestAnimationFrame(tick);
    };

    const safeDelayMs = Math.max(0, Math.floor(startDelayMs || 0));
    revealedLineCount = 0;
    if (safeDelayMs === 0) {
      beginAnimation();
      return;
    }

    typingDelayTimeout = window.setTimeout(() => {
      typingDelayTimeout = null;
      beginAnimation();
    }, safeDelayMs);
  }

  onDestroy(() => {
    stopTypingAnimation();
    resetHighlightState();
    hideTraceTooltip();
  });

  onMount(() => {
    const refreshLayout = () => {
      layoutVersion += 1;
      hideTraceTooltip();
    };

    window.addEventListener('scroll', refreshLayout, { passive: true });
    window.addEventListener('resize', refreshLayout);

    return () => {
      window.removeEventListener('scroll', refreshLayout);
      window.removeEventListener('resize', refreshLayout);
    };
  });
  
  // Subscribe to characters to force re-render when colors change
  $: charsVersion = $characters;

  function handleParagraphMouseUp(e: MouseEvent) {
    if (toolMode !== 'review') return; // Only handle multi-select in review mode
    
    const selection = (window as any).getSelection?.();
    if (!selection || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    const rects = Array.from(range.getClientRects()) as DOMRect[];
    if (rects.length === 0) return;
    const paragraphEl = (e.currentTarget as HTMLElement);
    const spans = Array.from(paragraphEl.querySelectorAll('span.run-character')) as HTMLSpanElement[];
    const chosen = new Set<number>();
    for (const spEl of spans) {
      const idAttr = spEl.getAttribute('data-lineid') || (spEl.id ? spEl.id.replace('line-','') : '');
      const lineId = idAttr ? parseInt(idAttr, 10) : NaN;
      if (!lineId || Number.isNaN(lineId)) continue;
      const spr = spEl.getBoundingClientRect();
      const intersects = rects.some((r: DOMRect) => !(r.right < spr.left || r.left > spr.right || r.bottom < spr.top || r.top > spr.bottom));
      if (intersects) chosen.add(lineId);
    }
    if (chosen.size) {
      const lastRect = rects[rects.length - 1] as DOMRect;
      dispatch('selectionMenu', { ev: e, lineIds: Array.from(chosen), anchorX: lastRect.right, anchorY: lastRect.bottom + 6 });
    }
  }

  function bgForRun(run: ParagraphRun, hovered: string | null): string {
    if (!run.characterId || run.characterId.toLowerCase() === 'narrator') return 'transparent';
    const activeCharacter = run.characterId ?? 'Unknown';
    const alpha = hovered && hovered !== activeCharacter ? RUN_HIGHLIGHT_DIM_ALPHA : RUN_HIGHLIGHT_ALPHA;
    // If this line is a conflict and has at least two candidates, render gradient between top two
    const scr = get(currentScript);
    if (scr && run.lineId != null) {
      const line = scr.lines.find(l => l.id === run.lineId);
      if (line && line.isConflict && Array.isArray(line.candidates) && line.candidates.length >= 2) {
        const left = line.candidates[0]?.name ?? activeCharacter;
        const right = line.candidates[1]?.name ?? activeCharacter;
        const c1 = hexToRgba(colorForCharacter(left), alpha);
        const c2 = hexToRgba(colorForCharacter(right), alpha);
        return `linear-gradient(to right, ${c1}, ${c2})`;
      }
    }
    return hexToRgba(colorForCharacter(run.characterId), alpha);
  }

  function normalizeLineText(value: string): string {
    return value
      .normalize('NFKC')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function runHasGeneratedAudio(run: ParagraphRun): boolean {
    if (run.lineId != null && generatedAudioLineIds.has(run.lineId)) return true;
    if (!run.text) return false;
    const normalizedRunText = normalizeLineText(run.text);
    if (!normalizedRunText) return false;
    if (generatedAudioLineTexts.has(normalizedRunText)) return true;

    if (normalizedRunText.length < 24) return false;
    for (const candidate of generatedAudioLineTexts) {
      if (candidate.length < 24) continue;
      if (
        normalizedRunText.startsWith(candidate) ||
        candidate.startsWith(normalizedRunText) ||
        normalizedRunText.includes(candidate) ||
        candidate.includes(normalizedRunText)
      ) {
        return true;
      }
    }

    return false;
  }

  function getLineData(lineId: number | undefined) {
    if (lineId == null) return null;
    const scr = get(currentScript);
    if (!scr) return null;
    return scr.lines.find((line) => line.id === lineId) || null;
  }

  function clearTraceTooltipTimer() {
    if (traceTooltipTimer != null) {
      clearTimeout(traceTooltipTimer);
      traceTooltipTimer = null;
    }
  }

  function hideTraceTooltip() {
    clearTraceTooltipTimer();
    traceTooltip = null;
    pendingTraceTooltip = null;
    traceHoverLineId = null;
    traceHoverOrigin = null;
  }

  function formatTraceLabel(value: string | null | undefined): string {
    if (!value) return '';
    return value
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function trimTraceValue(value: string, maxLength = 140): string {
    if (value.length <= maxLength) return value;
    return `${value.slice(0, maxLength - 1).trimEnd()}...`;
  }

  function formatTraceValue(value: unknown): string {
    if (value == null) return 'None';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return 'Unknown';
      const text = Math.abs(value) >= 10 ? value.toFixed(1) : value.toFixed(2);
      return text.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    }
    if (typeof value === 'string') return trimTraceValue(value);
    if (Array.isArray(value)) {
      return trimTraceValue(value.map((item) => formatTraceValue(item)).join(', '));
    }

    try {
      return trimTraceValue(JSON.stringify(value));
    } catch {
      return trimTraceValue(String(value));
    }
  }

  function formatTraceConfidence(value: unknown): string | null {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    const clamped = Math.max(0, Math.min(1, numeric));
    return `${Math.round(clamped * 100)}%`;
  }

  function toTraceSignalEntries(signals: Record<string, unknown> | null | undefined): TraceTooltipEntry[] {
    return Object.entries(signals ?? {})
      .filter(([, value]) => {
        if (value == null) return false;
        if (typeof value === 'string') return value.trim().length > 0;
        if (Array.isArray(value)) return value.length > 0;
        return true;
      })
      .map(([label, value]) => ({
        label: formatTraceLabel(label),
        value: formatTraceValue(value),
      }));
  }

  function positionTraceTooltip(clientX: number, clientY: number) {
    const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : TRACE_TOOLTIP_WIDTH_PX + 24;
    const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : TRACE_TOOLTIP_HEIGHT_PX + 24;
    traceTooltipX = Math.max(12, Math.min(clientX + 18, viewportWidth - TRACE_TOOLTIP_WIDTH_PX - 12));
    traceTooltipY = Math.max(12, Math.min(clientY + 18, viewportHeight - TRACE_TOOLTIP_HEIGHT_PX - 12));
  }

  function buildTraceTooltip(lineId: number): TraceTooltipData | null {
    const line = getLineData(lineId) as { attribution?: any } | null;
    const attribution = line?.attribution;
    const decisionTrace = attribution?.decisionTrace as LineAttributionDecisionTrace | null | undefined;
    if (!decisionTrace) return null;

    const selectedReasons = Array.isArray(decisionTrace.selectedReasons)
      ? decisionTrace.selectedReasons.filter((reason): reason is string => typeof reason === 'string' && reason.trim().length > 0)
      : [];

    const selectedCandidate =
      typeof decisionTrace.selectedCandidate === 'string' && decisionTrace.selectedCandidate.trim().length > 0
        ? decisionTrace.selectedCandidate
        : (typeof attribution?.candidates?.[0]?.name === 'string' ? attribution.candidates[0].name : null);

    return {
      selectedCandidate,
      resolutionStatus:
        typeof attribution?.resolutionStatus === 'string' && attribution.resolutionStatus.trim().length > 0
          ? formatTraceLabel(attribution.resolutionStatus)
          : null,
      confidence: formatTraceConfidence(attribution?.confidence),
      parserBackend:
        typeof attribution?.parserBackend === 'string' && attribution.parserBackend.trim().length > 0
          ? attribution.parserBackend
          : null,
      contextGender:
        typeof attribution?.contextGender === 'string' && attribution.contextGender.trim().length > 0
          ? formatTraceLabel(attribution.contextGender)
          : null,
      contextGenderCue:
        typeof attribution?.contextGenderCue === 'string' && attribution.contextGenderCue.trim().length > 0
          ? attribution.contextGenderCue
          : null,
      genderConflict: Boolean(attribution?.genderConflict),
      selectedReasons,
      overrideReason:
        typeof decisionTrace.overrideReason === 'string' && decisionTrace.overrideReason.trim().length > 0
          ? decisionTrace.overrideReason
          : null,
      signalEntries: toTraceSignalEntries(decisionTrace.signals),
    };
  }

  function queueTraceTooltip(run: ParagraphRun, event: MouseEvent) {
    if (toolMode !== 'review' || run.lineId == null) return;

    const payload = buildTraceTooltip(run.lineId);
    if (!payload) return;

    clearTraceTooltipTimer();
    pendingTraceTooltip = payload;
    traceTooltip = null;
    traceHoverLineId = run.lineId;
    traceHoverOrigin = { x: event.clientX, y: event.clientY };
    positionTraceTooltip(event.clientX, event.clientY);

    traceTooltipTimer = window.setTimeout(() => {
      traceTooltipTimer = null;
      if (traceHoverLineId !== run.lineId || !pendingTraceTooltip) return;
      traceTooltip = pendingTraceTooltip;
    }, TRACE_TOOLTIP_DELAY_MS);
  }

  function handleRunMouseEnter(run: ParagraphRun, event: MouseEvent) {
    if (run.characterId && run.characterId.toLowerCase() !== 'narrator') {
      setHovered(run.characterId);
    }
    hideTraceTooltip();
    queueTraceTooltip(run, event);
  }

  function handleRunMouseMove(run: ParagraphRun, event: MouseEvent) {
    if (toolMode !== 'review' || run.lineId == null || traceHoverLineId !== run.lineId || !traceHoverOrigin) {
      return;
    }

    positionTraceTooltip(event.clientX, event.clientY);
    const distance = Math.hypot(event.clientX - traceHoverOrigin.x, event.clientY - traceHoverOrigin.y);
    if (distance > TRACE_TOOLTIP_MOVE_THRESHOLD_PX) {
      queueTraceTooltip(run, event);
    }
  }

  function handleRunMouseLeave() {
    setHovered(null);
    hideTraceTooltip();
  }

  function handleRunFocus(run: ParagraphRun) {
    if (run.characterId && run.characterId.toLowerCase() !== 'narrator') {
      setHovered(run.characterId);
    }
  }

  function handleRunBlur() {
    setHovered(null);
    hideTraceTooltip();
  }

  function confidenceBadgeForRun(run: ParagraphRun): { label: string; low: boolean; unknown: boolean; confirmed: boolean } | null {
    if (run.characterId?.toLowerCase() === 'narrator') return null;

    const line = getLineData(run.lineId);
    const attribution = (line as any)?.attribution;
    if (!attribution) return null;

    const status = attribution.resolutionStatus;
    if (status === 'unknown') {
      return { label: 'UNK', low: true, unknown: true, confirmed: false };
    }

    const confidence = Number(attribution.confidence);
    if (!Number.isFinite(confidence)) return null;
    const normalizedConfidence = Math.max(0, Math.min(1, confidence));
    const roundedPercent = Math.round(normalizedConfidence * 100);
    const isUserConfirmed = status === 'user_confirmed';
    const isFullyConfident = roundedPercent >= 100;
    const isConfirmed = isUserConfirmed || isFullyConfident;

    return {
      label: isConfirmed ? '✓' : `${roundedPercent}%`,
      low: confidence < (Number(attribution.thresholdUsed) || 0.62),
      unknown: false,
      confirmed: isConfirmed,
    };
  }

  function displayTextForRun(run: ParagraphRun, hasPrefix: boolean): string {
    const value = run.text ?? '';
    if (!hasPrefix) return value;
    return value.replace(/^\s+/, '');
  }

  function highlightBucketForRun(run: ParagraphRun): string | null {
    const rawCharacterId = run.characterId;
    if (!rawCharacterId) return null;
    const normalized = rawCharacterId.toLowerCase();
    if (normalized === 'narrator') return null;
    return normalized;
  }

  $: runRenderInfo = runs.map((run) => {
    const audioLineNumberLabel = toolMode === 'audio' && run.lineId != null ? `L${run.lineId}` : null;
    const confidenceBadge = confidenceBadgeForRun(run);
    const badgeShowsCheck = Boolean(confidenceBadge && (confidenceBadge.confirmed || confidenceBadge.label === '100%'));
    const hasGeneratedAudio = runHasGeneratedAudio(run);
    const hasPrefixMarker = Boolean(audioLineNumberLabel) || Boolean(confidenceBadge) || hasGeneratedAudio;
    const displayText = displayTextForRun(run, hasPrefixMarker);

    return {
      run,
      audioLineNumberLabel,
      confidenceBadge,
      badgeShowsCheck,
      hasGeneratedAudio,
      hasPrefixMarker,
      displayText,
    };
  });

  $: totalDisplayCharacters = runRenderInfo.reduce((sum, info) => sum + info.displayText.length, 0);
  $: totalRevealLines = runRenderInfo.reduce((sum, info) => {
    if (!info.displayText.length) return sum;
    return sum + splitTextIntoApproxLines(info.displayText, APPROX_CHARS_PER_LINE).length;
  }, 0);
  $: totalHighlightCharacters = runRenderInfo.reduce((sum, info) => {
    const canHighlight = Boolean(highlightBucketForRun(info.run));
    return sum + (canHighlight ? info.displayText.length : 0);
  }, 0);

  $: {
    const chapterTitle = $currentChapter?.title ?? null;
    if (chapterTitle && totalDisplayCharacters > 0) {
      const triggerKey = `${chapterTitle}::${animationPlanVersion}::${planReady ? 'ready' : 'pending'}`;
      if (triggerKey !== lastAnimationTriggerKey) {
        lastAnimationTriggerKey = triggerKey;
        if (!planReady) {
          stopTypingAnimation();
          resetHighlightState();
          revealedLineCount = 0;
          highlightCharacterCount = 0;
          animationPhase = 'idle';
        } else if (animateOnLoad) {
          startTypingAnimation(totalDisplayCharacters, typingDelayMs);
        } else {
          stopTypingAnimation();
          resetHighlightState();
          revealedLineCount = totalRevealLines;
          highlightCharacterCount = totalHighlightCharacters;
          animationPhase = 'complete';
        }
      }
    }
  }

  $: {
    let remainingVisibleLines = Math.max(0, Math.floor(revealedLineCount));
    const perRunColorCounts = new Array<number>(runRenderInfo.length).fill(0);
    let remainingHighlight = Math.max(0, Math.floor(highlightCharacterCount));
    const highlightOrder: string[] = [];
    const perCharacterTotals = new Map<string, number>();

    for (const info of runRenderInfo) {
      const bucket = highlightBucketForRun(info.run);
      if (!bucket) continue;
      if (!perCharacterTotals.has(bucket)) {
        highlightOrder.push(bucket);
        perCharacterTotals.set(bucket, 0);
      }
      perCharacterTotals.set(bucket, (perCharacterTotals.get(bucket) || 0) + info.displayText.length);
    }

    const perCharacterHighlighted = new Map<string, number>();
    for (const bucket of highlightOrder) {
      const bucketTotal = perCharacterTotals.get(bucket) || 0;
      const bucketHighlighted = Math.max(0, Math.min(bucketTotal, remainingHighlight));
      perCharacterHighlighted.set(bucket, bucketHighlighted);
      remainingHighlight = Math.max(0, remainingHighlight - bucketTotal);
    }

    const perCharacterRemaining = new Map(perCharacterHighlighted);
    for (let index = 0; index < runRenderInfo.length; index += 1) {
      const info = runRenderInfo[index];
      const bucket = highlightBucketForRun(info.run);
      if (!bucket) {
        perRunColorCounts[index] = info.displayText.length;
        continue;
      }
      const bucketRemaining = perCharacterRemaining.get(bucket) || 0;
      const colorChars = Math.max(0, Math.min(info.displayText.length, bucketRemaining));
      perRunColorCounts[index] = colorChars;
      perCharacterRemaining.set(bucket, Math.max(0, bucketRemaining - info.displayText.length));
    }

    renderedRuns = runRenderInfo.map((info, runIndex) => {
      const typedText = info.displayText;
      const lineChunks = splitTextIntoApproxLines(typedText, APPROX_CHARS_PER_LINE);
      const revealableLines = typedText.length > 0 ? lineChunks.length : 0;
      const revealedLines = Math.max(0, Math.min(revealableLines, remainingVisibleLines));
      remainingVisibleLines = Math.max(0, remainingVisibleLines - revealableLines);
      const isRevealed = revealedLines > 0;
      const canHighlight = Boolean(highlightBucketForRun(info.run));
      const visibleText = isRevealed ? lineChunks.slice(0, revealedLines).join('') : '';

      const coloredVisibleCount = canHighlight
        ? Math.max(0, Math.min(visibleText.length, perRunColorCounts[runIndex]))
        : visibleText.length;
      const whiteCount = Math.max(0, visibleText.length - coloredVisibleCount);
      const whiteText = visibleText.slice(0, whiteCount);
      const colorText = visibleText.slice(whiteCount);

      return {
        ...info,
        typedText,
        isRevealed,
        whiteText,
        colorText,
        lineChunks,
        revealedLines,
      };
    });
  }
</script>

<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
<div 
  class="paragraph" 
  class:paragraph-playing={runs.some(r => r.lineId === $audioState.currentLineId && $audioState.isPlaying)}
  role="region" 
  aria-label="Paragraph text" 
  on:mouseup={handleParagraphMouseUp}
>
  {#key charsVersion}
    {#each renderedRuns as info}
      {@const run = info.run}
      {@const isHighlightPhase = animationPhase === 'highlight'}
      {@const isAnimationActive = animationPhase !== 'complete'}
      {@const showRunMarkers = animationPhase === 'complete' || info.isRevealed}
      {#if run.characterId}
        <span
          id={run.lineId != null ? 'line-'+run.lineId : undefined}
          class="run run-character"
          class:is-revealed={info.isRevealed}
          class:highlight-active={isAnimationActive}
          class:narrator-text={run.characterId?.toLowerCase() === 'narrator'}
          class:is-conflict={$conflictCursor === run.lineId}
          class:is-playing={run.lineId != null && $audioState.currentLineId === run.lineId && $audioState.isPlaying}
          style={`--bg:${bgForRun(run, hoveredCharacter)};`}
          role="button"
          tabindex="0"
          on:mouseenter={(event) => handleRunMouseEnter(run, event)}
          on:mousemove={(event) => handleRunMouseMove(run, event)}
          on:mouseleave={handleRunMouseLeave}
          on:focus={() => handleRunFocus(run)}
          on:blur={handleRunBlur}
          data-lineid={run.lineId}
          on:click={(e) => { 
            if (run.lineId == null) return;
            hideTraceTooltip();
            if (toolMode === 'review') {
              dispatch('lineMenu', { ev: e, lineIds: [run.lineId] });
            } else if (toolMode === 'join-split') {
              const selection = window.getSelection();
              const hasSelection = selection && selection.toString().length > 0;
              if (hasSelection) {
                dispatch('splitMenu', { ev: e, lineId: run.lineId });
              } else {
                dispatch('joinMenu', { ev: e, lineId: run.lineId });
              }
            } else if (toolMode === 'edit') {
              dispatch('editLine', { lineId: run.lineId });
            } else if (toolMode === 'audio') {
              // If audio is already playing for this line, seek instead of starting new playback
              if ($audioState.currentLineId === run.lineId && $audioState.isPlaying) {
                handleAudioSeek(run.lineId, e);
              } else {
                dispatch('audioClick', { lineId: run.lineId, characterName: run.characterId, ev: e });
              }
            }
          }}
          on:contextmenu={(e) => {
            if (run.lineId == null) return;
            hideTraceTooltip();
            if (toolMode === 'audio') {
              dispatch('audioContextMenu', { lineId: run.lineId, characterName: run.characterId, ev: e });
            }
          }}
          on:keydown={(e) => { 
            if ((e.key === 'Enter' || e.key === ' ') && run.lineId != null) { 
              e.preventDefault(); 
              hideTraceTooltip();
              if (toolMode === 'review') {
                dispatch('lineMenu', { ev: e, lineIds: [run.lineId] });
              } else if (toolMode === 'join-split') {
                dispatch('joinMenu', { ev: e, lineId: run.lineId });
              } else if (toolMode === 'edit') {
                dispatch('editLine', { lineId: run.lineId });
              } else if (toolMode === 'audio') {
                dispatch('audioClick', { lineId: run.lineId, characterName: run.characterId, ev: e });
              }
            } 
          }}
        >
          {#if showRunMarkers && info.audioLineNumberLabel}
            <span class="line-number-badge">{info.audioLineNumberLabel}</span>
          {/if}
          {#if showRunMarkers && info.confidenceBadge}
            <span class="confidence-badge" class:low={info.confidenceBadge.low} class:unknown={info.confidenceBadge.unknown} class:confirmed={info.badgeShowsCheck}>
              {#if info.badgeShowsCheck}
                <CheckCircle2 size={16} strokeWidth={2.2} class="confirmed-icon" aria-hidden="true" />
              {:else}
                {info.confidenceBadge.label}
              {/if}
            </span>
          {/if}
          {#if showRunMarkers && run.lineId != null && $audioState.currentLineId === run.lineId && ($audioState.isPlaying || $audioState.isPaused) && markerPosition}
            <span class="audio-progress-line" style="left: {markerPosition.left}px; top: {markerPosition.top}px; height: {Math.max(12, markerPosition.height)}px;"></span>
          {/if}
          {#if showRunMarkers && info.hasGeneratedAudio}
            <span class="generated-audio-marker" aria-hidden="true"><Music size={12} strokeWidth={2.25} /></span>
          {/if}
          <span class="run-text">
            {#if animationPhase === 'typing'}
              {#each info.lineChunks as lineChunk, lineIndex}
                <span class="run-line" class:is-revealed={lineIndex < info.revealedLines}>{lineChunk}</span>
              {/each}
            {:else if isAnimationActive}
              {#if info.whiteText}<span class="run-text-white">{info.whiteText}</span>{/if}
              {#if info.colorText}<span class="run-text-color">{info.colorText}</span>{/if}
            {:else}
              {info.typedText}
            {/if}
          </span>
        </span>
      {:else}
        <span
          id={run.lineId != null ? 'line-'+run.lineId : undefined}
          class="run run-plain"
          class:is-revealed={info.isRevealed}
          class:is-playing={run.lineId != null && $audioState.currentLineId === run.lineId && ($audioState.isPlaying || $audioState.isPaused)}
          data-lineid={run.lineId}
        >
          {#if showRunMarkers && run.lineId != null && $audioState.currentLineId === run.lineId && ($audioState.isPlaying || $audioState.isPaused) && markerPosition}
            <span class="audio-progress-line" style="left: {markerPosition.left}px; top: {markerPosition.top}px; height: {Math.max(12, markerPosition.height)}px;"></span>
          {/if}
          {#if showRunMarkers && info.audioLineNumberLabel}
            <span class="line-number-badge">{info.audioLineNumberLabel}</span>
          {/if}
          {#if showRunMarkers && info.hasGeneratedAudio}
            <span class="generated-audio-marker" aria-hidden="true"><Music size={12} strokeWidth={2.25} /></span>
          {/if}
          <span class="run-text">{#if animationPhase === 'typing'}{#each info.lineChunks as lineChunk, lineIndex}<span class="run-line" class:is-revealed={lineIndex < info.revealedLines}>{lineChunk}</span>{/each}{:else}{info.typedText}{/if}</span>
        </span>
      {/if}
    {/each}
  {/key}

  {#if traceTooltip}
    <div
      class="decision-trace-tooltip"
      role="tooltip"
      style={`left:${traceTooltipX}px; top:${traceTooltipY}px;`}
    >
      <div class="trace-tooltip-title">Attribution Trace</div>
      <div class="trace-meta-grid">
        {#if traceTooltip.selectedCandidate}
          <div class="trace-label">Selected</div>
          <div class="trace-value">{traceTooltip.selectedCandidate}</div>
        {/if}
        {#if traceTooltip.resolutionStatus}
          <div class="trace-label">Status</div>
          <div class="trace-value">{traceTooltip.resolutionStatus}</div>
        {/if}
        {#if traceTooltip.confidence}
          <div class="trace-label">Confidence</div>
          <div class="trace-value">{traceTooltip.confidence}</div>
        {/if}
        {#if traceTooltip.parserBackend}
          <div class="trace-label">Backend</div>
          <div class="trace-value">{traceTooltip.parserBackend}</div>
        {/if}
        {#if traceTooltip.contextGender}
          <div class="trace-label">Context Gender</div>
          <div class="trace-value">{traceTooltip.contextGender}</div>
        {/if}
        {#if traceTooltip.contextGenderCue}
          <div class="trace-label">Gender Cue</div>
          <div class="trace-value">{traceTooltip.contextGenderCue}</div>
        {/if}
        {#if traceTooltip.genderConflict}
          <div class="trace-label">Gender Conflict</div>
          <div class="trace-value">Yes</div>
        {/if}
        {#if traceTooltip.overrideReason}
          <div class="trace-label">Override</div>
          <div class="trace-value">{formatTraceLabel(traceTooltip.overrideReason)}</div>
        {/if}
      </div>

      {#if traceTooltip.selectedReasons.length > 0}
        <div class="trace-section">
          <div class="trace-section-title">Reasons</div>
          <div class="trace-chip-list">
            {#each traceTooltip.selectedReasons as reason}
              <span class="trace-chip">{formatTraceLabel(reason)}</span>
            {/each}
          </div>
        </div>
      {/if}

      {#if traceTooltip.signalEntries.length > 0}
        <div class="trace-section">
          <div class="trace-section-title">Signals</div>
          <div class="trace-signal-list">
            {#each traceTooltip.signalEntries as signal}
              <div class="trace-signal-row">
                <span class="trace-signal-key">{signal.label}</span>
                <span class="trace-signal-value">{signal.value}</span>
              </div>
            {/each}
          </div>
        </div>
      {:else if traceTooltip.selectedReasons.length === 0}
        <div class="trace-section">
          <div class="trace-empty">No additional decision signals recorded.</div>
        </div>
      {/if}
    </div>
  {/if}
  </div>

<style>
  .paragraph { 
    flex:1; 
    padding:8px 10px 8px 14px; 
    border-left:3px solid var(--app-border-subtle); 
    white-space:pre-wrap; 
    line-height:1.6; 
    margin:0;
    transition: border-color 0.2s ease;
  }
  .paragraph.paragraph-playing {
    border-left-color: var(--app-primary);
  }
  .run-character { 
    background: var(--bg, transparent);
    padding:0 2px; 
    border-radius:4px; 
    opacity: 0;
    transition: opacity 300ms ease, box-shadow 120ms ease;
    position: relative;
    white-space: normal;
    /* display: inline-block; */
  }
  .run-character.highlight-active {
    background: transparent;
  }
  .run-character.is-revealed {
    opacity: 1;
  }
  .run-character:not(.is-revealed) {
    pointer-events: none;
  }
  .paragraph > :first-child.run-character {
    padding-left: 0;
  }
  .run-character:hover { box-shadow: inset 0 0 0 9999px var(--app-primary-soft); }
  .run-character.is-playing { 
    cursor: pointer;
    box-shadow: inset 0 0 0 2px rgba(168, 218, 220, 0.48);
  }
  .run-character.narrator-text {
    background: transparent;
  }
  .run-character.narrator-text.is-revealed { opacity:0.85; }

  .run-plain {
    opacity: 0;
    transition: opacity 300ms ease;
  }
  .run-plain.is-revealed {
    opacity: 1;
  }
  .is-conflict { outline:2px solid #ff9800; }
  
  .run-text {
    position: relative;
    white-space: pre-wrap;
  }

  .run-line {
    opacity: 0;
    visibility: hidden;
    color: transparent;
    transition: opacity 300ms ease;
  }

  .run-line.is-revealed {
    opacity: 1;
    visibility: visible;
    color: inherit;
  }

  .run-text-white,
  .run-text-color {
    white-space: pre-wrap;
    border-radius: 4px;
    transition: background-color 180ms linear;
  }

  .run-text-white {
    background: transparent;
  }

  .run-text-color {
    background: var(--bg, transparent);
  }

  .generated-audio-marker {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-right: 3px;
    line-height: 0;
    vertical-align: baseline;
    color: var(--app-primary);
    opacity: 1;
  }

  .line-number-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 28px;
    height: 16px;
    margin-right: 4px;
    padding: 0 6px;
    border-radius: 999px;
    border: 1px solid var(--app-border-subtle);
    background: var(--app-surface-raised);
    color: var(--app-text-muted);
    font-family: 'Courier New', monospace;
    font-size: 10px;
    font-weight: 700;
    line-height: 1;
    vertical-align: middle;
  }

  .confidence-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 16px;
    margin-right: 2px;
    margin-top: -3px;
    font-size: 10px;
    line-height: 1;
    border-radius: 999px;
    padding: 0px 6px;
    border: 1px solid var(--app-border-subtle);
    color: var(--app-text);
    vertical-align: middle;
  }

  .confidence-badge.confirmed {
    font-weight: 700;
    width: 20px;
    min-width: 20px;
    height: 20px;
    padding: 0px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    line-height: 0;
    /* border-radius: 50%; */
    border-color: rgba(248, 250, 252, 0);
    background: 0;
    color: var(--app-text-muted);
    /* box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.15); */
  }

  :global(.confirmed-icon) {
    width: 16px;
    height: 16px;
    display: block;
    margin-top: 1.5px;
    flex-shrink: 0;
  }

  .confidence-badge.low {
    background: var(--app-warning-soft);
    color: var(--app-warning);
  }

  .confidence-badge.unknown {
    background: var(--app-danger-soft);
    color: var(--app-danger);
  }
  
  .audio-progress-line {
    position: fixed;
    width: 1px;
    height: 1.2em;
    background: var(--app-accent-cyan, var(--app-primary));
    pointer-events: none;
    transition: left 0.08s linear, top 0.05s linear;
    z-index: 10;
    /* box-shadow: 0 0 4px rgba(0, 0, 0, 0.3); */
  }

  .decision-trace-tooltip {
    position: fixed;
    z-index: 40;
    width: min(360px, calc(100vw - 24px));
    max-height: min(320px, calc(100vh - 24px));
    overflow: auto;
    padding: 12px 14px;
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.26);
    background: rgba(15, 23, 42, 0.96);
    color: #e2e8f0;
    box-shadow: 0 18px 40px rgba(15, 23, 42, 0.28);
    backdrop-filter: blur(8px);
    pointer-events: none;
  }

  .trace-tooltip-title,
  .trace-section-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #93c5fd;
  }

  .trace-tooltip-title {
    margin-bottom: 10px;
  }

  .trace-meta-grid {
    display: grid;
    grid-template-columns: minmax(92px, auto) minmax(0, 1fr);
    gap: 6px 10px;
  }

  .trace-label,
  .trace-signal-key {
    font-size: 11px;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .trace-value,
  .trace-signal-value,
  .trace-empty {
    font-size: 12px;
    color: #f8fafc;
    word-break: break-word;
  }

  .trace-section {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid rgba(148, 163, 184, 0.18);
  }

  .trace-section-title {
    margin-bottom: 8px;
  }

  .trace-chip-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .trace-chip {
    display: inline-flex;
    align-items: center;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid rgba(96, 165, 250, 0.22);
    background: rgba(59, 130, 246, 0.16);
    color: #dbeafe;
    font-size: 11px;
  }

  .trace-signal-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .trace-signal-row {
    display: grid;
    grid-template-columns: minmax(118px, 132px) minmax(0, 1fr);
    gap: 8px;
    align-items: start;
  }

  .trace-signal-value {
    color: #e2e8f0;
  }

  .trace-empty {
    color: #cbd5e1;
  }
</style>


