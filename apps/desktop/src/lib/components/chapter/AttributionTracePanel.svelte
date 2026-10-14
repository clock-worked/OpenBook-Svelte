<script lang="ts">
  import { currentChapter, currentScript } from '$lib/stores/bookState';
  import { tracedLine } from '$lib/stores/selection';
  import {
    buildTraceExplanation,
    humanizeReason,
    isPenaltyReason,
    signalEntries,
    type TraceSignals
  } from '$lib/components/chapter/tools/shared/attributionTraceGlossary';
  import type { JevVerification, LineAttribution, LineAttributionDecisionTrace } from '$lib/types';

  // --- Which line are we tracing? -------------------------------------------
  let lastChapterId: string | null | undefined = undefined;
  $: if ($currentChapter?.id !== lastChapterId) {
    lastChapterId = $currentChapter?.id ?? null;
    if ($tracedLine) tracedLine.set(null);
  }

  $: traced = $tracedLine;
  $: tracedLineRecord = (
    $currentScript?.lines ?? []
  ).find((line: any) => line.id === traced?.lineId) as any;

  // --- Derived trace view -----------------------------------------------------
  $: attribution = (tracedLineRecord?.attribution ?? null) as LineAttribution | null;
  $: decisionTrace = (attribution?.decisionTrace ?? null) as LineAttributionDecisionTrace | null;
  $: explanation = buildTraceExplanation(decisionTrace);
  $: signals = (decisionTrace?.signals ?? null) as TraceSignals | null;
  $: candidateRows = (attribution?.candidates ?? [])
    .map((candidate) => ({
      name: candidate.name,
      confidence: candidate.confidence,
      reasons: (candidate.reasons ?? []).map((reason) => ({
        phrase: humanizeReason(reason, candidate.name, signals ?? undefined),
        penalty: isPenaltyReason(reason),
      })),
    }))
    .sort((left, right) => right.confidence - left.confidence);
  $: signalRows = signalEntries(signals ?? undefined);
  $: jev = (attribution?.jevVerification ?? null) as JevVerification | null;
  $: legacyRule = attribution?.legacyRule ?? null;
  $: hasTrace = !!decisionTrace;
  $: droppedNames = (decisionTrace?.droppedCandidates ?? []).filter(
    (name): name is string => typeof name === 'string' && name.trim().length > 0
  );
  $: scoredNameKeys = new Set(candidateRows.map((row) => row.name.toLowerCase()));
  $: unscoredProposals = (() => {
    const seen = new Set<string>();
    const names: string[] = [];
    for (const name of decisionTrace?.rawCandidatePool ?? []) {
      if (typeof name !== 'string' || !name.trim()) continue;
      const key = name.toLowerCase();
      if (scoredNameKeys.has(key) || seen.has(key)) continue;
      seen.add(key);
      names.push(name);
    }
    return names;
  })();

  function titleCaseRule(rule: string): string {
    return rule
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function formatPercent(value: number | undefined): string | null {
    if (value == null || !Number.isFinite(value)) return null;
    return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
  }

  function snippet(text: string | null | undefined, maxLength = 90): string {
    const clean = (text ?? '').replace(/\s+/g, ' ').trim();
    if (clean.length <= maxLength) return clean;
    return `${clean.slice(0, maxLength - 1).trimEnd()}…`;
  }

  function jevNote(): string | null {
    if (!jev?.called) return null;
    if (jev.error) return `JEV check failed: ${jev.error}`;
    if (!jev.choice) return 'JEV check ran but returned no choice.';
    const chosen = decisionTrace?.selectedCandidate ?? attribution?.candidates?.[0]?.name ?? null;
    const confidence = formatPercent(jev.confidence);
    const base = `JEV check chose "${jev.choice}"${confidence ? ` at ${confidence}` : ''}`;
    if (chosen && jev.choice.toLowerCase() !== chosen.toLowerCase()) {
      return `${base} — disagrees with the chosen speaker.`;
    }
    return `${base} — agrees with the chosen speaker.`;
  }

  // --- Copy the full trace ----------------------------------------------------
  let copyState: 'idle' | 'copied' | 'failed' = 'idle';
  let copyTimer: number | undefined;

  async function copyFullTrace() {
    if (!tracedLineRecord) return;
    const payload = {
      lineId: tracedLineRecord.id,
      text: tracedLineRecord.text,
      characterName: tracedLineRecord.characterName ?? null,
      attribution: attribution ?? null,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      copyState = 'copied';
    } catch {
      copyState = 'failed';
    }
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      copyState = 'idle';
    }, 1500);
  }
</script>

<div class="trace-panel">
  {#if !traced}
    <div class="trace-empty-state">
      <div class="trace-empty-title">Nothing traced yet</div>
      <p>
        In review mode, <strong>Ctrl+Click</strong> a line to see why the parser
        attributed it the way it did.
      </p>
    </div>
  {:else if !tracedLineRecord}
    <div class="trace-empty-state">
      <div class="trace-empty-title">Line not found</div>
      <p>This line is no longer in the current chapter.</p>
    </div>
  {:else}
    <div class="trace-header">
      <div class="trace-line-ref">
        <span class="trace-line-number">Line {tracedLineRecord.id}</span>
        {#if tracedLineRecord.characterName}
          <span class="trace-line-char">{tracedLineRecord.characterName}</span>
        {/if}
        <button
          class="trace-copy-btn"
          class:copied={copyState === 'copied'}
          type="button"
          onclick={copyFullTrace}
          title="Copy the full decision trace (line + attribution JSON) to the clipboard"
        >
          {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy trace'}
        </button>
      </div>
      <div class="trace-line-snippet">“{snippet(tracedLineRecord.text)}”</div>
    </div>

    {#if !hasTrace}
      <div class="trace-empty-state">
        <div class="trace-empty-title">No decision trace</div>
        <p>
          No decision trace is recorded for this line — it may predate tracing or
          be plain narration.
        </p>
      </div>
    {:else}
      <!-- 1. Plain-English explanation -->
      <section class="trace-section">
        <h3 class="trace-section-title">Why this speaker</h3>
        <p class="trace-headline">{explanation.headline || 'No explanation recorded.'}</p>
        {#if explanation.detail}
          <p class="trace-detail">{explanation.detail}</p>
        {/if}
        {#if legacyRule}
          <p class="trace-audit-line">Legacy rule: {titleCaseRule(legacyRule)}</p>
        {/if}
      </section>

      <!-- 2. Ranked candidates -->
      <section class="trace-section">
        <h3 class="trace-section-title">Candidates</h3>
        {#if candidateRows.length === 0}
          <p class="trace-muted">No candidates recorded.</p>
        {:else}
          <ol class="trace-candidate-list">
            {#each candidateRows as candidate}
              <li class="trace-candidate">
                <div class="trace-candidate-head">
                  <span class="trace-candidate-name">{candidate.name}</span>
                  <span class="trace-candidate-conf">{formatPercent(candidate.confidence) ?? '—'}</span>
                </div>
                {#if candidate.reasons.length > 0}
                  <ul class="trace-candidate-reasons">
                    {#each candidate.reasons as reason}
                      <li class:penalty={reason.penalty}>{reason.phrase}</li>
                    {/each}
                  </ul>
                {/if}
              </li>
            {/each}
          </ol>
        {/if}
        {#if droppedNames.length > 0}
          <p class="trace-dropped">Closed-world filter dropped: {droppedNames.join(', ')}</p>
        {/if}
        {#if unscoredProposals.length > 0}
          <p class="trace-muted">Proposed by signals, never scored: {unscoredProposals.join(', ')}</p>
        {/if}
      </section>

      <!-- 3. Override story -->
      {#if explanation.override || jevNote()}
        <section class="trace-section">
          <h3 class="trace-section-title">Overrides &amp; verification</h3>
          {#if explanation.override}
            <p class="trace-override">{explanation.override}</p>
          {/if}
          {#if jevNote()}
            <p class="trace-jev">{jevNote()}</p>
          {/if}
        </section>
      {/if}

      <!-- 4. Raw signals -->
      {#if signalRows.length > 0}
        <details class="trace-signals">
          <summary>Raw signals ({signalRows.length})</summary>
          <div class="trace-signal-list">
            {#each signalRows as signal}
              <div class="trace-signal-row">
                <span class="trace-signal-key">{signal.label}</span>
                <span class="trace-signal-value">{signal.value}</span>
              </div>
            {/each}
          </div>
        </details>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .trace-panel {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 12px;
    overflow-y: auto;
    height: 100%;
  }

  .trace-empty-state {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 24px 12px;
    color: #94a3b8;
    font-size: 13px;
    line-height: 1.5;
  }

  .trace-empty-title {
    font-size: 13px;
    font-weight: 700;
    color: #e2e8f0;
  }

  .trace-header {
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
    padding-bottom: 10px;
  }

  .trace-line-ref {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .trace-line-number {
    font-size: 12px;
    font-weight: 700;
    color: #93c5fd;
  }

  .trace-line-char {
    font-size: 12px;
    font-weight: 600;
    color: #e2e8f0;
    padding: 1px 8px;
    border-radius: 999px;
    background: rgba(59, 130, 246, 0.16);
    border: 1px solid rgba(96, 165, 250, 0.22);
  }

  .trace-copy-btn {
    margin-left: auto;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.12);
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 999px;
    cursor: pointer;
  }

  .trace-copy-btn:hover {
    color: #f8fafc;
    border-color: rgba(148, 163, 184, 0.45);
  }

  .trace-copy-btn.copied {
    color: #86efac;
    border-color: rgba(134, 239, 172, 0.4);
  }

  .trace-line-snippet {
    margin-top: 6px;
    font-size: 13px;
    color: #cbd5e1;
    line-height: 1.45;
  }

  .trace-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .trace-section-title {
    margin: 0;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #93c5fd;
  }

  .trace-headline {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: #f8fafc;
    line-height: 1.45;
  }

  .trace-detail,
  .trace-audit-line {
    margin: 0;
    font-size: 12px;
    color: #cbd5e1;
    line-height: 1.5;
  }

  .trace-audit-line {
    color: #94a3b8;
  }

  .trace-candidate-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .trace-candidate {
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(30, 41, 59, 0.35);
  }

  .trace-candidate-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .trace-candidate-name {
    font-size: 13px;
    font-weight: 600;
    color: #f8fafc;
  }

  .trace-candidate-conf {
    font-size: 12px;
    color: #93c5fd;
  }

  .trace-candidate-reasons {
    margin: 6px 0 0;
    padding-left: 14px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .trace-candidate-reasons li {
    font-size: 12px;
    line-height: 1.45;
    color: #e2e8f0;
  }

  .trace-candidate-reasons li.penalty {
    color: #fca5a5;
  }

  .trace-override {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: #fbbf24;
  }

  .trace-jev {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: #a5b4fc;
  }

  .trace-muted {
    margin: 0;
    font-size: 12px;
    color: #94a3b8;
  }

  .trace-dropped {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: #fbbf24;
  }

  .trace-signals {
    font-size: 12px;
  }

  .trace-signals summary {
    cursor: pointer;
    color: #94a3b8;
    font-size: 12px;
  }

  .trace-signal-list {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .trace-signal-row {
    display: grid;
    grid-template-columns: minmax(130px, auto) minmax(0, 1fr);
    gap: 8px;
  }

  .trace-signal-key {
    color: #94a3b8;
  }

  .trace-signal-value {
    color: #e2e8f0;
    word-break: break-word;
  }
</style>
