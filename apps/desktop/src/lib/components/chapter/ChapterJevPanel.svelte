<script lang="ts">
  import {
    applyJevDialogueAiSuggestion,
    jumpToJevDialogueAiResult,
    jevDialogueAiState,
    jevDialogueAiVisibleResults,
    jevVerifySummary,
    runJevDialogueAiForCurrentChapter,
  } from '$lib/stores/jevDialogueAi';

  $: progress = $jevDialogueAiState.progress;
  $: progressProcessed = Math.max(0, progress?.processedLines ?? 0);
  $: progressTotal = Math.max(progressProcessed, progress?.totalLines ?? 0);
  $: progressPercent = Math.round(Math.max(0, Math.min(1, progress?.progressRatio ?? 0)) * 100);
  $: showProgress = $jevDialogueAiState.running && progressTotal > 0;

  // Parse-time verification summary (meta.jevVerify). Absent on older parses.
  $: verify = $jevVerifySummary;
  $: verifySkipped = verify ? (verify.skipped ?? null) : null;
  $: verifyLine = verify && !verifySkipped
    ? `JEV: ${verify.targets ?? 0} targets · ${verify.runs ?? 0} runs · ${verify.cacheHits ?? 0} cache hits · ${verify.errors ?? 0} errors (${((verify.elapsedMs ?? 0) / 1000).toFixed(1)} s)`
    : null;
</script>

<section class="jev-ai-panel">
  <div class="jev-ai-header">
    <div>
      <h3>JEV Assist</h3>
      <p>Cross-verify speaker assignments with the JEV model. Fixed 1500-char context window.</p>
    </div>
    <button
      class="run-btn"
      on:click={() => void runJevDialogueAiForCurrentChapter()}
      disabled={$jevDialogueAiState.running}
    >
      {$jevDialogueAiState.running ? 'Running…' : 'Run JEV'}
    </button>
  </div>

  {#if verifySkipped}
    <p class="verify-line muted">JEV: skipped — {verifySkipped === 'no_api_key' ? 'no API key' : verifySkipped}</p>
  {:else if verifyLine}
    <p class="verify-line">{verifyLine}{verify.budgetExhausted ? ' (budget exhausted)' : ''}</p>
  {/if}

  {#if showProgress}
    <div class="progress" aria-live="polite">
      <div class="progress-copy">
        <span>Asking the JEV model…</span>
        <span>{progressProcessed}/{progressTotal}</span>
      </div>
      <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progressPercent}>
        <div class="progress-fill" style={`width:${progressPercent}%`}></div>
      </div>
    </div>
  {/if}

  {#if $jevDialogueAiState.statusMessage}
    <p
      class="status"
      class:success={$jevDialogueAiState.statusTone === 'success'}
      class:error={$jevDialogueAiState.statusTone === 'error'}
    >{$jevDialogueAiState.statusMessage}</p>
  {/if}

  {#if $jevDialogueAiState.error}
    <p class="status error">{$jevDialogueAiState.error}</p>
  {/if}

  {#if $jevDialogueAiState.summary}
    <div class="summary-grid">
      <div class="summary-card"><strong>{$jevDialogueAiState.summary.scannedLines}</strong><span>scanned</span></div>
      <div class="summary-card"><strong>{$jevDialogueAiState.summary.unchangedCount}</strong><span>confirmed</span></div>
      <div class="summary-card"><strong>{$jevDialogueAiState.summary.suggestionCount}</strong><span>suggestions</span></div>
      <div class="summary-card"><strong>{$jevDialogueAiState.summary.unresolvedCount}</strong><span>unresolved</span></div>
      <div class="summary-card"><strong>{$jevDialogueAiState.summary.errorCount}</strong><span>errors</span></div>
    </div>
  {/if}

  {#if $jevDialogueAiVisibleResults.length > 0}
    <div class="results">
      {#each $jevDialogueAiVisibleResults as result (result.lineId)}
        <article class="result">
          <div class="result-header">
            <div>
              <strong>Line {result.lineId}</strong>
              <span>
                {result.currentCharacterName || 'Unknown'}
                →
                {result.outcome === 'suggestion' ? result.suggestedCharacterName : 'Unresolved'}
              </span>
            </div>
            <div class="result-actions">
              <button class="inline-btn" on:click={() => jumpToJevDialogueAiResult(result.lineId)}>Jump</button>
              {#if result.outcome === 'suggestion' && result.suggestedCharacterName}
                <button class="inline-btn primary" on:click={() => void applyJevDialogueAiSuggestion(result)}>Apply</button>
              {/if}
            </div>
          </div>
          <p class="line-text">{result.currentParagraphText}</p>
          <p class="meta">Model output: {result.rawOutput || 'empty'}</p>
          {#if result.error}<p class="meta error">{result.error}</p>{/if}
          {#if result.uiMessage}<p class="meta" class:error={result.uiStatus === 'error'}>{result.uiMessage}</p>{/if}
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .jev-ai-panel { display: flex; flex-direction: column; gap: 12px; }
  .jev-ai-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .jev-ai-header h3 { margin: 0 0 4px; font-size: 16px; }
  .jev-ai-header p { margin: 0; color: var(--app-text-muted); font-size: 13px; line-height: 1.4; }
  .run-btn, .inline-btn { border: 1px solid var(--app-border); background: var(--app-surface-raised); color: var(--app-text); border-radius: 10px; padding: 8px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .run-btn, .inline-btn.primary { background: var(--app-primary); border-color: var(--app-primary); color: var(--app-text-inverse); }
  .run-btn:disabled, .inline-btn:disabled { opacity: .55; cursor: default; }
  .verify-line { margin: 0; padding: 8px 12px; border: 1px solid var(--app-border); border-radius: 10px; background: var(--app-surface-subtle); color: var(--app-text); font-size: 12px; }
  .verify-line.muted { color: var(--app-text-muted); }
  .progress { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border: 1px solid var(--app-border); border-radius: 10px; background: var(--app-surface-subtle); }
  .progress-copy { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
  .progress-track { width: 100%; height: 10px; border-radius: 999px; overflow: hidden; background: var(--app-primary-soft); }
  .progress-fill { height: 100%; border-radius: inherit; background: var(--app-primary); transition: width .25s ease; }
  .status { margin: 0; font-size: 13px; }
  .status.success { color: var(--app-success); }
  .status.error, .meta.error { color: var(--app-danger); }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(88px, 1fr)); gap: 8px; }
  .summary-card { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 10px; background: var(--app-surface-raised); border: 1px solid var(--app-border); }
  .summary-card strong { font-size: 18px; line-height: 1; }
  .summary-card span { color: var(--app-text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  .results { display: flex; flex-direction: column; gap: 10px; }
  .result { padding: 12px; border-radius: 10px; background: var(--app-surface-raised); border: 1px solid var(--app-border); }
  .result-header { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
  .result-header strong { display: block; font-size: 13px; }
  .result-header span { color: var(--app-text-muted); font-size: 12px; }
  .result-actions { display: flex; align-items: flex-start; gap: 8px; }
  .line-text, .meta { margin: 0 0 6px; color: var(--app-text); font-size: 13px; line-height: 1.45; }
  .meta { color: var(--app-text-muted); font-size: 12px; }
</style>
