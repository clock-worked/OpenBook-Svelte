<script lang="ts">
  import { onDestroy } from 'svelte';
  import {
    audioQueue,
    cancelAllJobs,
    clearFinishedJobs,
    removeQueuedJob,
    type AudioQueueItem,
  } from '$lib/stores/audioQueue';
  import { ChevronDown, ChevronUp, X } from 'lucide-svelte';

  type OverlayTab = 'queue' | 'overview';

  interface ChapterViewRow {
    chapterTitle: string;
    processableTotal: number;
    missingVoiceTotal: number;
    processed: number;
    percent: number;
    items: AudioQueueItem[];
    createdAt: number;
  }

  let activeTab: OverlayTab = 'queue';
  let panelCollapsed = false;
  let chapterCollapsed: Record<string, boolean> = {};
  let now = Date.now();
  let ticker: ReturnType<typeof setInterval> | null = null;
  let etaCompletionAtMs: number | null = null;
  let etaSignature = '';
  let etaRunStartAt: number | null = null;

  const INITIAL_RATE_SAMPLE_SIZE = 5;
  const RATE_REFINE_SPAN = 15;

  $: runningItem = $audioQueue.items.find((item) => item.status === 'running') || null;
  $: queuedItems = $audioQueue.items.filter((item) => item.status === 'queued');
  $: activeItems = $audioQueue.items.filter((item) => item.status === 'queued' || item.status === 'running');
  $: showOverlay = Boolean(runningItem) || queuedItems.length > 0;

  $: runStartAt = $audioQueue.runStartedAt ?? runningItem?.startedAt ?? null;
  $: overviewTotals = Object.values($audioQueue.chapterOverview);
  $: processableTotalFromOverview = overviewTotals.reduce((sum, chapter) => sum + chapter.processableTotal, 0);
  $: processableTotalFromItems = $audioQueue.items.reduce((sum, item) => sum + Math.max(0, item.total), 0);
  $: processableTotal = processableTotalFromOverview > 0 ? processableTotalFromOverview : processableTotalFromItems;
  $: processedLines = $audioQueue.items.reduce((sum, item) => sum + Math.max(0, Math.min(item.current, item.total)), 0);
  $: totalCharacters = $audioQueue.items.reduce((sum, item) => sum + Math.max(0, item.totalCharacters ?? 0), 0);
  $: processedCharacters = $audioQueue.items.reduce((sum, item) => sum + Math.max(0, Math.min(item.processedCharacters ?? 0, item.totalCharacters ?? 0)), 0);
  $: elapsedSeconds = runStartAt ? Math.max(0, Math.floor((now - runStartAt) / 1000)) : 0;
  $: completedGenerationRates = $audioQueue.items
    .filter((item) => item.status === 'completed' && item.startedAt && item.finishedAt && item.finishedAt > item.startedAt)
    .map((item) => {
      const chars = Math.max(0, item.totalCharacters ?? item.processedCharacters ?? 0);
      const durationSeconds = Math.max(0, (item.finishedAt! - item.startedAt!) / 1000);
      if (chars <= 0 || durationSeconds <= 0) return 0;
      return chars / durationSeconds;
    })
    .filter((rate) => Number.isFinite(rate) && rate > 0);
  $: firstSampleRates = completedGenerationRates.slice(0, INITIAL_RATE_SAMPLE_SIZE);
  $: sampledCharactersPerSecond =
    firstSampleRates.length > 0 ? firstSampleRates.reduce((sum, rate) => sum + rate, 0) / firstSampleRates.length : 0;
  $: refinedCharactersPerSecond =
    completedGenerationRates.length > 0
      ? completedGenerationRates.reduce((sum, rate) => sum + rate, 0) / completedGenerationRates.length
      : 0;
  $: refinementWeight =
    completedGenerationRates.length <= INITIAL_RATE_SAMPLE_SIZE
      ? 0
      : Math.min(1, (completedGenerationRates.length - INITIAL_RATE_SAMPLE_SIZE) / RATE_REFINE_SPAN);
  $: sampledBaseRate = sampledCharactersPerSecond > 0 ? sampledCharactersPerSecond : refinedCharactersPerSecond;
  $: estimatedCharactersPerSecond =
    sampledBaseRate > 0
      ? sampledBaseRate * (1 - refinementWeight) + refinedCharactersPerSecond * refinementWeight
      : elapsedSeconds > 0 && processedCharacters > 0
        ? processedCharacters / elapsedSeconds
        : 0;
  $: remainingCharacters = Math.max(0, totalCharacters - processedCharacters);
  $: rawEstimatedRemainingSeconds =
    estimatedCharactersPerSecond > 0 ? Math.max(0, remainingCharacters / estimatedCharactersPerSecond) : null;
  $: {
    const hasEstimate = rawEstimatedRemainingSeconds !== null && Number.isFinite(rawEstimatedRemainingSeconds);
    const signature = [
      runStartAt ?? 'none',
      totalCharacters,
      processedCharacters,
      completedGenerationRates.length,
      Math.round(estimatedCharactersPerSecond * 100),
    ].join('|');

    if (!showOverlay || !hasEstimate) {
      etaCompletionAtMs = null;
      etaSignature = '';
      etaRunStartAt = runStartAt;
    } else if (remainingCharacters <= 0) {
      etaCompletionAtMs = now;
      etaSignature = signature;
      etaRunStartAt = runStartAt;
    } else {
      const nextCompletionAtMs = now + rawEstimatedRemainingSeconds * 1000;
      const shouldReset = etaCompletionAtMs === null || runStartAt !== etaRunStartAt || etaSignature === '';

      if (shouldReset) {
        etaCompletionAtMs = nextCompletionAtMs;
      } else if (signature !== etaSignature) {
        etaCompletionAtMs = Math.min(etaCompletionAtMs ?? nextCompletionAtMs, nextCompletionAtMs);
      }

      etaSignature = signature;
      etaRunStartAt = runStartAt;
    }
  }
  $: estimatedRemainingSeconds = etaCompletionAtMs !== null ? Math.max(0, Math.ceil((etaCompletionAtMs - now) / 1000)) : null;
  $: estimatedTotalSeconds = estimatedCharactersPerSecond > 0 ? Math.round(totalCharacters / estimatedCharactersPerSecond) : null;
  $: overallPercent = processableTotal > 0 ? Math.min(100, (processedLines / processableTotal) * 100) : 0;

  $: chapterRows = (() => {
    const byChapter = new Map<string, ChapterViewRow>();

    for (const item of activeItems) {
      const title = item.chapterTitle;
      const existing = byChapter.get(title);
      if (existing) {
        existing.items.push(item);
        existing.createdAt = Math.min(existing.createdAt, item.createdAt);
      } else {
        byChapter.set(title, {
          chapterTitle: title,
          processableTotal: 0,
          missingVoiceTotal: 0,
          processed: 0,
          percent: 0,
          items: [item],
          createdAt: item.createdAt,
        });
      }
    }

    for (const chapter of overviewTotals) {
      const row = byChapter.get(chapter.chapterTitle);
      if (row) {
        row.processableTotal = chapter.processableTotal;
        row.missingVoiceTotal = chapter.missingVoiceTotal;
      } else {
        byChapter.set(chapter.chapterTitle, {
          chapterTitle: chapter.chapterTitle,
          processableTotal: chapter.processableTotal,
          missingVoiceTotal: chapter.missingVoiceTotal,
          processed: 0,
          percent: 0,
          items: [],
          createdAt: Number.MAX_SAFE_INTEGER,
        });
      }
    }

    for (const row of byChapter.values()) {
      const chapterItems = $audioQueue.items.filter((item) => item.chapterTitle === row.chapterTitle);
      row.processed = chapterItems.reduce((sum, item) => sum + Math.max(0, Math.min(item.current, item.total)), 0);

      if (row.processableTotal <= 0) {
        row.processableTotal = chapterItems.reduce((sum, item) => sum + Math.max(0, item.total), 0);
      }

      row.percent = row.processableTotal > 0 ? Math.min(100, (row.processed / row.processableTotal) * 100) : 0;
      row.items.sort((a, b) => a.createdAt - b.createdAt);
    }

    return Array.from(byChapter.values()).sort((a, b) => {
      if (a.createdAt !== b.createdAt) return a.createdAt - b.createdAt;
      return a.chapterTitle.localeCompare(b.chapterTitle);
    });
  })();

  $: {
    const next = { ...chapterCollapsed };
    let changed = false;

    for (const chapter of chapterRows) {
      if (next[chapter.chapterTitle] === undefined) {
        next[chapter.chapterTitle] = false;
        changed = true;
      }
    }

    for (const key of Object.keys(next)) {
      if (!chapterRows.some((chapter) => chapter.chapterTitle === key)) {
        delete next[key];
        changed = true;
      }
    }

    if (changed) chapterCollapsed = next;
  }

  $: {
    if (showOverlay) {
      if (!ticker) {
        ticker = setInterval(() => {
          now = Date.now();
        }, 1000);
      }
    } else if (ticker) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  onDestroy(() => {
    if (ticker) clearInterval(ticker);
  });

  function formatDuration(seconds: number | null): string {
    if (seconds === null) return 'Estimating…';
    const total = Math.max(0, Math.round(seconds));
    const hours = Math.floor(total / 3600);
    const mins = Math.floor((total % 3600) / 60);
    const secs = total % 60;

    if (hours > 0) return `${hours}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  }

  function toggleChapter(chapterTitle: string): void {
    chapterCollapsed = {
      ...chapterCollapsed,
      [chapterTitle]: !chapterCollapsed[chapterTitle],
    };
  }

  function chapterMissingPercent(chapter: ChapterViewRow): number {
    const combined = chapter.processableTotal + chapter.missingVoiceTotal;
    if (combined <= 0 || chapter.missingVoiceTotal <= 0) return 0;
    return Math.max(0, Math.min(100, (chapter.missingVoiceTotal / combined) * 100));
  }

  function chapterProcessablePercent(chapter: ChapterViewRow): number {
    return 100 - chapterMissingPercent(chapter);
  }
</script>

{#if showOverlay}
  <div class="queue-overlay">
    <div class="overlay-header">
      <div class="header-main">
        <h4>Audio Queue</h4>
        <div class="header-subtitle">{processedLines}/{processableTotal} lines · ETA {formatDuration(estimatedRemainingSeconds)}</div>
      </div>
      <div class="actions">
        <button class="btn icon" title={panelCollapsed ? 'Expand' : 'Collapse'} on:click={() => (panelCollapsed = !panelCollapsed)}>
          {#if panelCollapsed}
            <ChevronDown size={14} />
          {:else}
            <ChevronUp size={14} />
          {/if}
        </button>
        {#if !panelCollapsed}
          <button class="btn ghost" on:click={clearFinishedJobs}>Clear done</button>
          <button class="btn danger" on:click={cancelAllJobs}>Cancel all</button>
        {/if}
      </div>
    </div>

    {#if !panelCollapsed}
      <div class="running-card">
        <div class="running-title">
          {#if runningItem}
            Generating: {runningItem.characterName}
          {:else}
            Waiting for next queued item
          {/if}
        </div>
        <div class="running-meta">Processed {processedLines}/{processableTotal} · Elapsed {formatDuration(elapsedSeconds)} · Total est. {formatDuration(estimatedTotalSeconds)}</div>
        <div class="progress-track">
          <div class="progress-fill" style={`width: ${overallPercent}%`}></div>
        </div>
      </div>

      <div class="tabs">
        <button class={`tab ${activeTab === 'queue' ? 'active' : ''}`} on:click={() => (activeTab = 'queue')}>Queue</button>
        <button class={`tab ${activeTab === 'overview' ? 'active' : ''}`} on:click={() => (activeTab = 'overview')}>Overview</button>
      </div>

      {#if activeTab === 'queue'}
        <div class="queue-scroll">
          {#if chapterRows.length === 0}
            <div class="empty-state">No queued audio lines.</div>
          {:else}
            {#each chapterRows as chapter (chapter.chapterTitle)}
              <div class="chapter-group">
                <button class="chapter-header" on:click={() => toggleChapter(chapter.chapterTitle)}>
                  <span>{chapter.chapterTitle}</span>
                  <span>{chapter.items.length} item{chapter.items.length === 1 ? '' : 's'} · {chapter.processed}/{chapter.processableTotal}</span>
                </button>

                {#if !chapterCollapsed[chapter.chapterTitle]}
                  <div class="chapter-items">
                    {#if chapter.items.length === 0}
                      <div class="empty-state small">No active queue items in this chapter.</div>
                    {:else}
                      {#each chapter.items as item (item.id)}
                        <div class="queue-row">
                          <div class="queue-row-main">
                            <span class="queue-name">{item.characterName}</span>
                            <span class="queue-count">{item.current}/{item.total} · {item.status}</span>
                          </div>
                          {#if item.status === 'queued'}
                            <button class="remove-btn" title="Remove from queue" on:click={() => removeQueuedJob(item.id)}>
                              <X size={14} />
                            </button>
                          {/if}
                        </div>
                      {/each}
                    {/if}
                  </div>
                {/if}
              </div>
            {/each}
          {/if}
        </div>
      {:else}
        <div class="queue-scroll">
          {#if chapterRows.length === 0}
            <div class="empty-state">No overview data yet.</div>
          {:else}
            {#each chapterRows as chapter (chapter.chapterTitle)}
              <div class="overview-card">
                <div class="overview-top">
                  <span class="overview-title">{chapter.chapterTitle}</span>
                  <span class="overview-percent">{chapter.percent.toFixed(1)}%</span>
                </div>
                <div class="overview-meta">
                  {chapter.processed}/{chapter.processableTotal} processed
                  {#if chapter.missingVoiceTotal > 0}
                    · {chapter.missingVoiceTotal} missing voice
                  {/if}
                </div>

                <div class="dual-bars">
                  <div class="progress-track" style={`width: ${chapterProcessablePercent(chapter)}%`}>
                    <div class="progress-fill" style={`width: ${chapter.percent}%`}></div>
                  </div>
                  {#if chapterMissingPercent(chapter) > 0}
                    <div class="missing-track" style={`width: ${chapterMissingPercent(chapter)}%`}></div>
                  {/if}
                </div>
              </div>
            {/each}
          {/if}
        </div>
      {/if}
    {:else}
      <div class="collapsed-summary">
        <span>{activeItems.length} queued item{activeItems.length === 1 ? '' : 's'}</span>
        <span>{processedLines}/{processableTotal} lines processed</span>
      </div>
    {/if}
  </div>
{/if}

<style>
  .queue-overlay {
    position: fixed;
    top: 10px;
    right: 16px;
    width: 420px;
    max-height: 70vh;
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 10px;
    box-shadow: var(--app-shadow-md);
    z-index: 1100;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .overlay-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .header-main {
    min-width: 0;
  }

  .overlay-header h4 {
    margin: 0;
    font-size: 14px;
    color: var(--app-text);
  }

  .header-subtitle {
    font-size: 12px;
    color: var(--app-text-muted);
    margin-top: 2px;
  }

  .actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  .btn {
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-text);
    font-size: 12px;
    padding: 4px 8px;
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
  }

  .btn.icon {
    width: 28px;
    padding: 4px;
  }

  .btn.ghost:hover {
    background: var(--app-surface-hover);
  }

  .btn.danger {
    border-color: var(--app-danger);
    color: var(--app-danger);
  }

  .btn.danger:hover {
    background: var(--app-danger-soft);
  }

  .running-card {
    border: 1px solid var(--app-border);
    border-radius: 8px;
    padding: 8px;
    background: var(--app-surface-subtle);
  }

  .running-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text);
  }

  .running-meta {
    font-size: 12px;
    color: var(--app-text-muted);
    margin-top: 2px;
    margin-bottom: 6px;
  }

  .tabs {
    display: flex;
    gap: 6px;
  }

  .tab {
    border: 1px solid var(--app-border);
    border-radius: 999px;
    background: var(--app-surface-raised);
    color: var(--app-text-muted);
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .tab.active {
    background: var(--app-primary-soft);
    color: var(--app-primary-text);
    border-color: var(--app-primary);
  }

  .queue-scroll {
    display: flex;
    flex-direction: column;
    gap: 8px;
    overflow-y: auto;
    max-height: 44vh;
    padding-right: 2px;
  }

  .chapter-group,
  .overview-card {
    border: 1px solid var(--app-border);
    border-radius: 8px;
    background: var(--app-surface-raised);
  }

  .chapter-header {
    width: 100%;
    border: none;
    background: var(--app-surface-subtle);
    border-bottom: 1px solid var(--app-border);
    border-radius: 8px 8px 0 0;
    padding: 8px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: var(--app-text);
    cursor: pointer;
  }

  .chapter-items {
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .queue-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    border: 1px solid var(--app-border);
    border-radius: 8px;
    padding: 6px 8px;
    background: var(--app-surface);
  }

  .queue-row-main {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .queue-name {
    font-size: 13px;
    color: var(--app-text);
    font-weight: 500;
  }

  .queue-count {
    font-size: 12px;
    color: var(--app-text-muted);
  }

  .remove-btn {
    border: none;
    background: transparent;
    color: var(--app-text-subtle);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    border-radius: 4px;
    padding: 2px;
  }

  .remove-btn:hover {
    color: var(--app-danger);
    background: var(--app-danger-soft);
  }

  .overview-card {
    padding: 8px;
  }

  .overview-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .overview-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text);
  }

  .overview-percent {
    font-size: 12px;
    color: var(--app-primary-text);
    font-weight: 600;
  }

  .overview-meta {
    margin-top: 2px;
    margin-bottom: 6px;
    font-size: 12px;
    color: var(--app-text-muted);
  }

  .dual-bars {
    display: flex;
    align-items: stretch;
    gap: 6px;
    width: 100%;
  }

  .progress-track {
    height: 8px;
    background: var(--app-border-subtle);
    border-radius: 999px;
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--app-primary);
    transition: width 0.2s ease;
  }

  .missing-track {
    height: 8px;
    border-radius: 999px;
    border: 1px dashed var(--app-danger);
    background: var(--app-danger-soft);
  }

  .empty-state {
    border: 1px dashed var(--app-border-strong);
    border-radius: 8px;
    padding: 10px;
    font-size: 12px;
    color: var(--app-text-muted);
    text-align: center;
  }

  .empty-state.small {
    padding: 6px;
  }

  .collapsed-summary {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
    color: var(--app-text-muted);
    border-top: 1px solid var(--app-border-subtle);
    padding-top: 6px;
  }
</style>
