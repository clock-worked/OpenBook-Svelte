<script lang="ts">
  import {
    applyLocalDialogueAiSuggestion,
    jumpToLocalDialogueAiResult,
    localDialogueAiState,
    localDialogueAiVisibleResults,
    runLocalDialogueAiForCurrentChapter,
    updateLocalDialogueAiSettings,
  } from '$lib/stores/localDialogueAi';

  $: progress = $localDialogueAiState.progress;
  $: progressProcessed = Math.max(0, progress?.processedLines ?? 0);
  $: progressTotal = Math.max(progressProcessed, progress?.totalLines ?? 0);
  $: progressPercent = Math.round(Math.max(0, Math.min(1, progress?.progressRatio ?? 0)) * 100);
  $: showProgress = $localDialogueAiState.running && progressTotal > 0;
</script>

<section class="local-ai-panel">
  <div class="local-ai-header">
    <div>
      <h3>Local AI</h3>
      <p>Check and automatically apply recognized speaker assignments with LM Studio.</p>
    </div>
    <button
      class="run-btn"
      on:click={() => void runLocalDialogueAiForCurrentChapter()}
      disabled={$localDialogueAiState.running}
    >
      {$localDialogueAiState.running ? 'Running…' : 'Run Local AI'}
    </button>
  </div>

  <div class="settings-card">
    <label class="range-setting">
      <span>
        <strong>Context window</strong>
        <small>{$localDialogueAiState.settings.contextWindow} characters on each side</small>
      </span>
      <input
        type="range"
        min="100"
        max="2400"
        step="100"
        value={$localDialogueAiState.settings.contextWindow}
        disabled={$localDialogueAiState.running}
        on:input={(event) => updateLocalDialogueAiSettings({
          contextWindow: Number((event.currentTarget as HTMLInputElement).value),
        })}
      />
    </label>

    <label class="toggle-setting">
      <input
        type="checkbox"
        checked={$localDialogueAiState.settings.includePreviousSpeaker}
        disabled={$localDialogueAiState.running}
        on:change={(event) => updateLocalDialogueAiSettings({
          includePreviousSpeaker: (event.currentTarget as HTMLInputElement).checked,
        })}
      />
      <span>
        <strong>Previous speaker</strong>
        <small>Include the most recent labeled speaker.</small>
      </span>
    </label>

    <label class="toggle-setting">
      <input
        type="checkbox"
        checked={$localDialogueAiState.settings.includeSpeakerContext}
        disabled={$localDialogueAiState.running}
        on:change={(event) => updateLocalDialogueAiSettings({
          includeSpeakerContext: (event.currentTarget as HTMLInputElement).checked,
        })}
      />
      <span>
        <strong>Speaker context</strong>
        <small>Include nearby scene character IDs.</small>
      </span>
    </label>
  </div>

  {#if showProgress}
    <div class="progress" aria-live="polite">
      <div class="progress-copy">
        <span>Asking the local model…</span>
        <span>{progressProcessed}/{progressTotal}</span>
      </div>
      <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progressPercent}>
        <div class="progress-fill" style={`width:${progressPercent}%`}></div>
      </div>
    </div>
  {/if}

  {#if $localDialogueAiState.statusMessage}
    <p
      class="status"
      class:success={$localDialogueAiState.statusTone === 'success'}
      class:error={$localDialogueAiState.statusTone === 'error'}
    >{$localDialogueAiState.statusMessage}</p>
  {/if}

  {#if $localDialogueAiState.error}
    <p class="status error">{$localDialogueAiState.error}</p>
  {/if}

  {#if $localDialogueAiState.summary}
    <div class="summary-grid">
      <div class="summary-card"><strong>{$localDialogueAiState.summary.scannedLines}</strong><span>scanned</span></div>
      <div class="summary-card"><strong>{$localDialogueAiState.summary.unchangedCount}</strong><span>confirmed</span></div>
      <div class="summary-card"><strong>{$localDialogueAiState.summary.suggestionCount}</strong><span>suggestions</span></div>
      <div class="summary-card"><strong>{$localDialogueAiState.summary.unresolvedCount}</strong><span>unresolved</span></div>
      <div class="summary-card"><strong>{$localDialogueAiState.summary.errorCount}</strong><span>errors</span></div>
    </div>
  {/if}

  {#if $localDialogueAiVisibleResults.length > 0}
    <div class="results">
      {#each $localDialogueAiVisibleResults as result (result.lineId)}
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
              <button class="inline-btn" on:click={() => jumpToLocalDialogueAiResult(result.lineId)}>Jump</button>
              {#if result.outcome === 'suggestion' && result.suggestedCharacterName}
                <button class="inline-btn primary" on:click={() => void applyLocalDialogueAiSuggestion(result)}>Apply</button>
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
  .local-ai-panel { display: flex; flex-direction: column; gap: 12px; }
  .local-ai-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .local-ai-header h3 { margin: 0 0 4px; font-size: 16px; }
  .local-ai-header p { margin: 0; color: var(--app-text-muted); font-size: 13px; line-height: 1.4; }
  .run-btn, .inline-btn { border: 1px solid var(--app-border); background: var(--app-surface-raised); color: var(--app-text); border-radius: 10px; padding: 8px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }
  .run-btn, .inline-btn.primary { background: var(--app-primary); border-color: var(--app-primary); color: var(--app-text-inverse); }
  .run-btn:disabled, .inline-btn:disabled { opacity: .55; cursor: default; }
  .settings-card { display: flex; flex-direction: column; gap: 12px; padding: 12px; border: 1px solid var(--app-border); border-radius: 10px; background: var(--app-surface-subtle); }
  .range-setting, .toggle-setting { display: flex; gap: 10px; color: var(--app-text); font-size: 13px; }
  .range-setting { flex-direction: column; }
  .range-setting > span, .toggle-setting > span { display: flex; flex-direction: column; gap: 2px; }
  .range-setting > span { flex-direction: row; justify-content: space-between; align-items: baseline; }
  .range-setting input { width: 100%; accent-color: var(--app-primary); }
  .toggle-setting { align-items: flex-start; }
  .toggle-setting input { margin-top: 2px; accent-color: var(--app-primary); }
  .settings-card small { color: var(--app-text-muted); font-size: 12px; font-weight: 400; }
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
