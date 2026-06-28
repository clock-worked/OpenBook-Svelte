<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Voice } from '$lib/types';
  import { Play, Tags } from 'lucide-svelte';
  import PillList from '../common/PillList.svelte';

  export let voice: Voice;
  export let characters: string[] = [];

  const dispatch = createEventDispatcher<{
    edit: Voice;
    preview: string;
  }>();

  $: voiceTags = voice.metadata.tags || [];

  function handleEdit() {
    dispatch('edit', voice);
  }

  function handlePreview() {
    dispatch('preview', voice.id);
  }
</script>

<div class="speaker-card">
  <div class="card-head">
    <div class="speaker-heading">
      <h3 class="speaker-name" title={voice.displayName}>{voice.displayName}</h3>
      <p class="speaker-file" title={voice.providerVoiceId}>{voice.providerVoiceId}</p>
    </div>
    <button class="icon-btn" on:click={handlePreview} title="Preview voice">
      <Play size={14} />
    </button>
  </div>

  <div class="meta-block">
    <span class="meta-label">Tags</span>
    {#if voiceTags.length > 0}
      <div class="pill-wrap">
        <PillList selected={voiceTags} readonly={true} />
      </div>
    {:else}
      <p class="empty-copy">No tags yet.</p>
    {/if}
  </div>

  <div class="meta-block">
    <span class="meta-label">Characters</span>
    {#if characters.length > 0}
      <div class="pill-wrap">
        <PillList selected={characters} readonly={true} />
      </div>
    {:else}
      <p class="empty-copy">Unassigned</p>
    {/if}
  </div>

  <div class="card-actions">
    <button class="edit-btn" on:click={handleEdit} title="Edit name and tags">
      <Tags size={14} />
      <span>Edit tags</span>
    </button>
  </div>
</div>

<style>
  .speaker-card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px;
    background: var(--app-surface-raised);
    border: 1px solid var(--app-border);
    border-radius: 10px;
    box-shadow: var(--app-shadow-sm);
  }

  .card-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
  }

  .speaker-heading {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .speaker-name {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--app-text);
    line-height: 1.35;
    word-break: break-word;
  }

  .speaker-file {
    margin: 0;
    font-size: 11px;
    color: var(--app-text-muted);
    line-height: 1.35;
    word-break: break-word;
  }

  .icon-btn,
  .edit-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border: 1px solid var(--app-border);
    border-radius: 8px;
    background: var(--app-surface-hover);
    color: var(--app-text);
    cursor: pointer;
    transition: background 0.16s ease, border-color 0.16s ease;
  }

  .icon-btn {
    width: 30px;
    height: 30px;
    flex: 0 0 auto;
  }

  .edit-btn {
    width: 100%;
    padding: 7px 10px;
    font-size: 12px;
    font-weight: 600;
  }

  .icon-btn:hover,
  .edit-btn:hover {
    background: var(--app-surface-active);
    border-color: var(--app-border-strong);
  }

  .meta-block {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .meta-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--app-text-muted);
  }

  .pill-wrap :global(.pill-list-container) {
    gap: 5px;
  }

  .pill-wrap :global(.pill) {
    padding: 3px 8px;
    font-size: 11px;
    border-radius: 999px;
    background: var(--app-surface-hover);
    border-color: var(--app-border);
  }

  .empty-copy {
    margin: 0;
    font-size: 12px;
    color: var(--app-text-subtle);
  }

  .card-actions {
    margin-top: auto;
  }
</style>

