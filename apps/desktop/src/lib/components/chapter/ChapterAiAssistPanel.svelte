<script lang="ts">
  import {
    applyDialogueAiAliasSuggestion,
    applyDialogueAiCharacterSuggestion,
    dialogueAiAssistState,
    dialogueAiAssistVisibleResults,
    formatDialogueAiReasonCodes,
    formatDialogueAiToolTrace,
    jumpToDialogueAiResult,
    runDialogueAiAssistForCurrentChapter,
  } from '$lib/stores/dialogueAiAssist';

  $: progress = $dialogueAiAssistState.progress;
  $: progressProcessed = Math.max(0, progress?.processedLines ?? 0);
  $: progressTotal = Math.max(progressProcessed, progress?.totalLines ?? 0);
  $: progressPercent = Math.round(
    Math.max(0, Math.min(1, progress?.progressRatio ?? 0)) * 100
  );
  $: showProgress = $dialogueAiAssistState.running && progressTotal > 0;
</script>

<section class="ai-assist-panel">
  <div class="ai-assist-header">
    <div>
      <h3>AI Assist</h3>
      <p>Validate non-narrator speaker assignments for the current chapter.</p>
    </div>
    <button
      class="ai-assist-btn"
      on:click={() => void runDialogueAiAssistForCurrentChapter()}
      disabled={$dialogueAiAssistState.running}
    >
      {$dialogueAiAssistState.running ? 'Running…' : 'Run AI Assist'}
    </button>
  </div>

  {#if showProgress}
    <div class="ai-assist-progress" aria-live="polite">
      <div class="ai-assist-progress-copy">
        <span>AI Assist is processing this chapter.</span>
        <span>{progressProcessed}/{progressTotal} lines</span>
      </div>
      <div
        class="ai-assist-progress-track"
        role="progressbar"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow={progressPercent}
      >
        <div class="ai-assist-progress-fill" style={`width:${progressPercent}%`}></div>
      </div>
    </div>

    <p class="ai-assist-progress-copy">
      <span>Processing {progressProcessed} of {progressTotal} lines.</span>
      {#if progressPercent > 0}
        <span>{progressPercent}%</span>
      {/if}
    </p>
  {/if}

  {#if $dialogueAiAssistState.statusMessage}
    <p
      class:success={$dialogueAiAssistState.statusTone === 'success'}
      class:error={$dialogueAiAssistState.statusTone === 'error'}
      class="ai-assist-status"
    >
      {$dialogueAiAssistState.statusMessage}
    </p>
  {/if}

  {#if $dialogueAiAssistState.error}
    <p class="ai-assist-status error">{$dialogueAiAssistState.error}</p>
  {/if}

  {#if $dialogueAiAssistState.summary}
    <div class="ai-assist-summary-grid">
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.scannedLines}</strong>
        <span>scanned</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.autoApplyCount}</strong>
        <span>auto</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.reviewCount}</strong>
        <span>review</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.aliasReviewCount}</strong>
        <span>alias</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.newCharacterCount}</strong>
        <span>new</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.errorCount}</strong>
        <span>errors</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.cacheHits ?? 0}</strong>
        <span>cache hits</span>
      </div>
      <div class="ai-assist-summary-card">
        <strong>{$dialogueAiAssistState.summary.modelCalls ?? 0}</strong>
        <span>live calls</span>
      </div>
    </div>

    {#if $dialogueAiAssistState.summary.logDirectory}
      <p class="ai-assist-meta">
        Run logs: {$dialogueAiAssistState.summary.logDirectory}
      </p>
    {/if}
  {/if}

  {#if $dialogueAiAssistVisibleResults.length > 0}
    <div class="ai-assist-results">
      {#each $dialogueAiAssistVisibleResults as result (result.lineId)}
        <article class="ai-assist-result">
          <div class="ai-assist-result-header">
            <div>
              <strong>Line {result.lineId}</strong>
              <span>
                {result.currentCharacterName || 'Unknown'}
                →
                {result.suggestedCharacterName || 'Review'}
              </span>
            </div>
            <div class="ai-assist-result-actions">
              <button
                class="ai-inline-btn"
                on:click={() => jumpToDialogueAiResult(result.lineId)}
              >
                Jump
              </button>
              {#if result.disposition === 'review' && result.suggestedCharacterId && result.suggestedCharacterName && result.action !== 'keep_existing'}
                <button
                  class="ai-inline-btn primary"
                  on:click={() => void applyDialogueAiCharacterSuggestion(result)}
                >
                  Apply
                </button>
              {/if}
              {#if result.disposition === 'alias_review' && result.aliasToAdd}
                <button
                  class="ai-inline-btn primary"
                  on:click={() => void applyDialogueAiAliasSuggestion(result)}
                >
                  Add Alias
                </button>
              {/if}
            </div>
          </div>

          <p class="ai-assist-line-text">{result.currentParagraphText}</p>

          {#if result.reasonCodes.length > 0}
            <p class="ai-assist-reasons">
              Reasons: {formatDialogueAiReasonCodes(result.reasonCodes)}
            </p>
          {/if}

          {#if result.toolTrace.length > 0}
            <p class="ai-assist-meta">
              Tools: {formatDialogueAiToolTrace(result.toolTrace)}
            </p>
          {/if}

          <p class="ai-assist-meta">
            Source: {result.cacheHit ? 'cached' : 'live'} model response.
          </p>

          {#if result.aliasToAdd}
            <p class="ai-assist-meta">
              Alias proposal: add {result.aliasToAdd.alias} to {result.aliasToAdd.characterName}.
            </p>
          {/if}

          {#if result.newCharacterProposal}
            <p class="ai-assist-meta">
              New character proposal: {result.newCharacterProposal.name}
            </p>
          {/if}

          {#if result.logPath}
            <p class="ai-assist-meta">Log: {result.logPath}</p>
          {/if}

          {#if result.uiMessage}
            <p class:error={result.uiStatus === 'error'} class="ai-assist-meta">
              {result.uiMessage}
            </p>
          {/if}
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .ai-assist-panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .ai-assist-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .ai-assist-header h3 {
    margin: 0 0 4px;
    font-size: 16px;
  }

  .ai-assist-header p {
    margin: 0;
    color: var(--app-text-muted);
    font-size: 13px;
    line-height: 1.4;
  }

  .ai-assist-btn,
  .ai-inline-btn {
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-text);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .ai-assist-btn:disabled,
  .ai-inline-btn:disabled {
    opacity: 0.55;
    cursor: default;
  }

  .ai-inline-btn.primary,
  .ai-assist-btn {
    background: var(--app-primary);
    border-color: var(--app-primary);
    color: var(--app-text-inverse);
  }

  .ai-assist-progress-copy,
  .ai-assist-status {
    margin: 0;
    font-size: 13px;
    color: var(--app-text);
  }

  .ai-assist-progress {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid var(--app-border);
    border-radius: 10px;
    background: linear-gradient(180deg, var(--app-surface-subtle) 0%, var(--app-surface-hover) 100%);
  }

  .ai-assist-progress-copy {
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .ai-assist-progress-track {
    width: 100%;
    height: 10px;
    border-radius: 999px;
    overflow: hidden;
    background: var(--app-primary-soft);
  }

  .ai-assist-progress-fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, var(--app-primary) 0%, var(--app-primary-hover) 100%);
    transition: width 0.25s ease;
  }

  .ai-assist-status.success {
    color: var(--app-success);
  }

  .ai-assist-status.error {
    color: var(--app-danger);
  }

  .ai-assist-summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
    gap: 10px;
  }

  .ai-assist-summary-card {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--app-surface-raised);
    border: 1px solid var(--app-border);
  }

  .ai-assist-summary-card strong {
    font-size: 18px;
    line-height: 1;
  }

  .ai-assist-summary-card span {
    color: var(--app-text-muted);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .ai-assist-results {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .ai-assist-result {
    padding: 12px;
    border-radius: 10px;
    background: var(--app-surface-raised);
    border: 1px solid var(--app-border);
  }

  .ai-assist-result-header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }

  .ai-assist-result-header strong {
    display: block;
    font-size: 13px;
  }

  .ai-assist-result-header span {
    color: var(--app-text-muted);
    font-size: 12px;
  }

  .ai-assist-result-actions {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }

  .ai-assist-line-text,
  .ai-assist-reasons,
  .ai-assist-meta {
    margin: 0 0 6px;
    font-size: 13px;
    line-height: 1.45;
    color: var(--app-text);
  }

  .ai-assist-meta.error {
    color: var(--app-danger);
  }
</style>
