<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import { FolderOpen, Play, RefreshCw, Save, X } from 'lucide-svelte';
  import type { Character, Voice } from '$lib/types';
  import SpeakerCard from './SpeakerCard.svelte';
  import { getCharacterNamesByIds } from '$lib/stores/characters';
  import { pickDirectoryAbsolutePath } from '$lib/services/directoryPicker';
  import { normalizeVoiceTags } from '$lib/services/vibevoice';
  import { saveVoiceSampleMetadataForVoice } from '$lib/stores/speakers';

  export let voices: Voice[] = [];
  export let assignments: Array<{ characterId: string; voiceId: string }> = [];
  export let characters: Character[] = [];
  export let voiceSamplesPath: string = '';

  const dispatch = createEventDispatcher<{
    samplesrootchange: string | null;
  }>();

  let previewAudio: HTMLAudioElement | null = null;
  let isSyncing = false;
  let syncError: string | null = null;

  let showTagModal = false;
  let editingVoice: Voice | null = null;
  let editingDisplayName = '';
  let editingTags: string[] = [];
  let pendingTag = '';
  let isSavingTags = false;
  let tagError: string | null = null;

  $: visibleVoices = (() => {
    const uniqueVoices: Voice[] = [];
    const seenIds = new Set<string>();

    for (const voice of voices) {
      if (seenIds.has(voice.id)) {
        console.warn('[SpeakerManager] Skipping duplicate voice id during render:', voice.id);
        continue;
      }

      seenIds.add(voice.id);
      uniqueVoices.push(voice);
    }

    return uniqueVoices;
  })();

  onDestroy(() => {
    previewAudio?.pause();
    previewAudio = null;
  });

  function getCharacterNames(voiceId: string): string[] {
    const characterIds = assignments
      .filter((assignment) => assignment.voiceId === voiceId)
      .map((assignment) => assignment.characterId);

    return getCharacterNamesByIds(characterIds, characters);
  }

  function resolvePreviewUrl(voice: Voice): string | null {
    return typeof voice.previewUrl === 'string' && voice.previewUrl.trim().length > 0
      ? voice.previewUrl
      : null;
  }

  async function playPreview(voice: Voice) {
    const previewUrl = resolvePreviewUrl(voice);
    if (!previewUrl) {
      alert('No preview is available for this sample.');
      return;
    }

    previewAudio?.pause();
    previewAudio = new Audio(previewUrl);
    await previewAudio.play();
  }

  async function handlePreview(event: CustomEvent<string>) {
    const voice = voices.find((entry) => entry.id === event.detail);
    if (!voice) return;

    playPreview(voice).catch((err) => console.error('Preview error:', err));
  }

  function openTagModal(event: CustomEvent<Voice>) {
    editingVoice = event.detail;
    editingDisplayName = event.detail.displayName;
    editingTags = [...(event.detail.metadata.tags || [])];
    pendingTag = '';
    tagError = null;
    showTagModal = true;
  }

  function closeTagModal() {
    showTagModal = false;
    editingVoice = null;
    editingDisplayName = '';
    editingTags = [];
    pendingTag = '';
    tagError = null;
  }

  function addPendingTag() {
    if (!pendingTag.trim()) return;
    editingTags = normalizeVoiceTags([...editingTags, pendingTag]);
    pendingTag = '';
  }

  function removeTag(tag: string) {
    editingTags = editingTags.filter((entry) => entry !== tag);
  }

  function handlePendingTagKeydown(event: KeyboardEvent) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addPendingTag();
  }

  async function saveTags() {
    if (!editingVoice) return;

    const samplesRoot = voiceSamplesPath.trim();
    if (!samplesRoot) {
      tagError = 'Select a voice samples folder before editing tags.';
      return;
    }

    isSavingTags = true;
    tagError = null;
    try {
      const normalizedName = editingDisplayName.trim();
      if (!normalizedName) {
        tagError = 'Name is required.';
        isSavingTags = false;
        return;
      }

      await saveVoiceSampleMetadataForVoice(
        editingVoice.id,
        samplesRoot,
        normalizedName,
        editingTags,
      );
      closeTagModal();
    } catch (err) {
      tagError = err instanceof Error ? err.message : 'Failed to save tags.';
    } finally {
      isSavingTags = false;
    }
  }

  async function refreshImportedVoices() {
    const samplesRoot = voiceSamplesPath.trim();
    if (!samplesRoot) {
      syncError = 'Choose a samples folder to import voices.';
      return;
    }

    dispatch('samplesrootchange', samplesRoot);
  }

  async function chooseSamplesFolder() {
    try {
      const absolutePath = await pickDirectoryAbsolutePath('read');
      if (!absolutePath) return;

      dispatch('samplesrootchange', absolutePath);
    } catch (err) {
      if (err instanceof Error && err.message.includes('not supported')) {
        alert('Folder picker is not supported in this browser. Set the samples path in settings.');
        return;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      syncError = err instanceof Error ? err.message : 'Failed to select the samples folder.';
    }
  }
</script>

<div class="speaker-manager">
  <div class="manager-header">
    <div>
      <h2 class="section-title">Sample Voices</h2>
      <p class="section-copy">Each supported audio sample in the selected folder becomes a voice. Names and tags are stored in a JSON catalog inside that folder.</p>
    </div>

    <div class="header-actions">
      <button class="header-btn" on:click={chooseSamplesFolder}>
        <FolderOpen size={16} />
        <span>Choose Folder</span>
      </button>
      <button class="header-btn" on:click={refreshImportedVoices} disabled={isSyncing}>
        <span class:is-spinning={isSyncing} class="icon-wrap">
          <RefreshCw size={16} />
        </span>
        <span>{isSyncing ? 'Loading...' : 'Refresh'}</span>
      </button>
    </div>
  </div>

  <div class="samples-root">
    <span class="samples-root-label">Folder</span>
    <span class="samples-root-value">{voiceSamplesPath || 'No samples folder selected.'}</span>
  </div>

  {#if syncError}
    <p class="sync-error">{syncError}</p>
  {/if}

  <div class="speakers-grid">
    {#each visibleVoices as voice (voice.id)}
      <SpeakerCard
        {voice}
        characters={getCharacterNames(voice.id)}
        on:edit={openTagModal}
        on:preview={handlePreview}
      />
    {/each}

    {#if visibleVoices.length === 0}
      <div class="empty-state">
        <p>Choose a folder with voice samples to load them here.</p>
      </div>
    {/if}
  </div>
</div>

{#if showTagModal && editingVoice}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="modal-backdrop" on:click={closeTagModal}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="modal-content" on:click|stopPropagation>
      <div class="modal-header">
        <div>
          <h3>{editingVoice.displayName}</h3>
          <p>Edit the saved name and tags for this sample. Data is stored in the folder catalog JSON.</p>
        </div>
        <button class="close-btn" on:click={closeTagModal}>
          <X size={18} />
        </button>
      </div>

      <div class="modal-body">
        <div class="modal-row">
          <span class="modal-label">Sample file</span>
          <p class="modal-file">{editingVoice.providerVoiceId}</p>
        </div>

        <div class="modal-row">
          <label class="modal-label" for="voice-display-name">Name</label>
          <input
            id="voice-display-name"
            class="modal-input"
            type="text"
            bind:value={editingDisplayName}
            placeholder="Display name"
          />
        </div>

        <div class="modal-row">
          <span class="modal-label">Tags</span>
          <div class="tag-entry-row">
            <input
              class="modal-input"
              type="text"
              bind:value={pendingTag}
              placeholder="Add a tag and press Enter"
              on:keydown={handlePendingTagKeydown}
            />
            <button class="secondary-btn" on:click={addPendingTag} type="button">Add tag</button>
          </div>

          {#if editingTags.length > 0}
            <div class="tag-chip-list">
              {#each editingTags as tag (tag)}
                <button class="tag-chip" type="button" on:click={() => removeTag(tag)} title="Remove {tag}">
                  <span>{tag}</span>
                  <X size={12} />
                </button>
              {/each}
            </div>
          {:else}
            <p class="modal-help">No tags saved yet.</p>
          {/if}
        </div>

        {#if tagError}
          <p class="sync-error">{tagError}</p>
        {/if}
      </div>

      <div class="modal-actions">
        <button class="secondary-btn" on:click={() => playPreview(editingVoice!)}>
          <Play size={14} />
          <span>Preview</span>
        </button>
        <button class="secondary-btn" on:click={closeTagModal}>Cancel</button>
        <button class="primary-btn" on:click={saveTags} disabled={isSavingTags}>
          <Save size={14} />
          <span>{isSavingTags ? 'Saving...' : 'Save tags'}</span>
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .speaker-manager {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
  }

  .manager-header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-start;
  }

  .section-title {
    margin: 0;
    font-size: 19px;
    color: #152033;
  }

  .section-copy {
    margin: 4px 0 0;
    font-size: 13px;
    color: #637082;
    max-width: 520px;
  }

  .header-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .header-btn,
  .primary-btn,
  .secondary-btn,
  .close-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border-radius: 9px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.16s ease, border-color 0.16s ease;
  }

  .header-btn,
  .secondary-btn {
    padding: 9px 12px;
    border: 1px solid #d7dee7;
    background: #f4f7fb;
    color: #203551;
  }

  .header-btn:hover:not(:disabled),
  .secondary-btn:hover:not(:disabled),
  .close-btn:hover {
    background: #e8eef6;
    border-color: #c2cfdf;
  }

  .primary-btn {
    padding: 9px 12px;
    border: 1px solid #1c5fd1;
    background: #1f6feb;
    color: #ffffff;
  }

  .primary-btn:hover:not(:disabled) {
    background: #165dc9;
  }

  .header-btn:disabled,
  .primary-btn:disabled,
  .secondary-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .icon-wrap {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .samples-root {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 12px;
    background: #f8fafc;
    border: 1px solid #d7dee7;
    border-radius: 10px;
  }

  .samples-root-label,
  .modal-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #6a7688;
  }

  .samples-root-value {
    font-size: 13px;
    color: #203551;
    word-break: break-all;
  }

  .sync-error {
    margin: 0;
    color: #b42318;
    font-size: 12px;
  }

  .speakers-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
  }

  .empty-state {
    grid-column: 1 / -1;
    padding: 40px 16px;
    border: 1px dashed #c5d0de;
    border-radius: 12px;
    text-align: center;
    color: #637082;
    background: #fbfcfe;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background: rgba(10, 18, 28, 0.55);
    z-index: 1000;
  }

  .modal-content {
    width: min(560px, 100%);
    background: #ffffff;
    border-radius: 14px;
    box-shadow: 0 24px 60px rgba(10, 18, 28, 0.24);
    border: 1px solid #d7dee7;
  }

  .modal-header,
  .modal-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 16px 18px;
  }

  .modal-header {
    border-bottom: 1px solid #e2e8f0;
  }

  .modal-header h3 {
    margin: 0;
    font-size: 16px;
    color: #152033;
  }

  .modal-header p {
    margin: 4px 0 0;
    font-size: 12px;
    color: #6a7688;
  }

  .close-btn {
    width: 32px;
    height: 32px;
    border: 1px solid #d7dee7;
    background: #f4f7fb;
    color: #203551;
  }

  .modal-body {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px;
  }

  .modal-file,
  .modal-help {
    margin: 0;
    font-size: 12px;
    color: #6a7688;
  }

  .modal-input {
    width: 100%;
    padding: 9px 11px;
    border: 1px solid #d7dee7;
    border-radius: 9px;
    background: #ffffff;
    color: #152033;
    font: inherit;
    box-sizing: border-box;
  }

  .modal-input:focus {
    outline: none;
    border-color: #5d8fd8;
    box-shadow: 0 0 0 3px rgba(93, 143, 216, 0.16);
  }

  .modal-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .tag-entry-row {
    display: flex;
    gap: 8px;
  }

  .tag-entry-row .modal-input {
    flex: 1;
  }

  .tag-chip-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .tag-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 9px;
    border: 1px solid #d7dee7;
    border-radius: 999px;
    background: #edf3f9;
    color: #203551;
    font-size: 12px;
    cursor: pointer;
  }

  .tag-chip:hover {
    background: #e2ebf6;
  }

  .modal-actions {
    justify-content: flex-end;
    border-top: 1px solid #e2e8f0;
  }

  .is-spinning {
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }

    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 840px) {
    .manager-header {
      flex-direction: column;
      align-items: stretch;
    }

    .header-actions {
      width: 100%;
    }

    .header-btn {
      flex: 1;
    }

    .tag-entry-row {
      flex-direction: column;
    }
  }
</style>