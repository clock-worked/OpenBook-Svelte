<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import { get } from 'svelte/store';
  import type { Voice, TtsProvider, Character } from '$lib/types';
  import SpeakerCard from './SpeakerCard.svelte';
  import { Plus, X, Play, RefreshCw } from 'lucide-svelte';
  import { getCharacterNamesByIds } from '$lib/stores/characters';
  import { voiceSamplesRoot } from '$lib/stores/bookState';
  import { getVoiceSamplePreviewUrl, listVoiceSamples } from '$lib/services/vibevoice';

  export let voices: Voice[] = [];
  export let assignments: Array<{ characterId: string; voiceId: string }> = [];
  export let characters: Character[] = [];

  let voiceRows: Array<{ voice: Voice; uiKey: string }> = [];

  const dispatch = createEventDispatcher<{
    update: Voice[];
    deduplicate: void;
  }>();

  let showModal = false;
  let editingId: string | null = null;
  let formData: Partial<Voice> = {
    displayName: '',
    provider: 'vibevoice_local',
    providerVoiceId: '',
    notes: '',
    previewUrl: null,
    metadata: {
      gender: 'U',
      imageUrl: null,
    },
  };

  const PROVIDER_OPTIONS: Array<{ value: TtsProvider; label: string }> = [
    { value: 'vibevoice_local', label: 'VibeVoice (Local)' },
  ];
  const AGE_OPTIONS = [
    { value: 'child', label: 'Child' },
    { value: 'adult', label: 'Adult' },
    { value: 'old', label: 'Old' },
  ];
  const ACCENT_OPTIONS = [
    { value: 'american', label: 'American' },
    { value: 'british', label: 'British' },
    { value: 'japanese', label: 'Japanese' },
    { value: 'french', label: 'French' },
    { value: 'southern', label: 'Southern' },
    { value: 'middle_east', label: 'Middle East' },
    { value: 'other', label: 'Other' },
  ];
  const STYLE_OPTIONS = [
    { value: 'normal', label: 'Normal' },
    { value: 'monotone', label: 'Monotone' },
    { value: 'deep', label: 'Deep' },
    { value: 'slow', label: 'Slow' },
    { value: 'gruffly', label: 'Gruffly' },
    { value: 'other', label: 'Other' },
  ];
  let voiceSamples: string[] = [];
  let voiceSamplesError: string | null = null;
  let isLoadingSamples = false;
  let lastSamplesRoot: string | null = null;
  let previewAudio: HTMLAudioElement | null = null;

  function getStyleFromTags(tags?: string[]): string {
    const styleTag = tags?.find((tag) => tag.startsWith('style:'));
    if (!styleTag) return 'normal';
    return styleTag.slice('style:'.length) || 'normal';
  }

  function setStyleTag(tags: string[] | undefined, style: string): string[] {
    const sanitized = (tags || []).filter((tag) => !tag.startsWith('style:'));
    sanitized.push(`style:${style}`);
    return sanitized;
  }

  onDestroy(() => {
    previewAudio?.pause();
    previewAudio = null;
  });

  async function loadVoiceSamples(force: boolean = false) {
    const root = get(voiceSamplesRoot);
    if (!root) {
      voiceSamples = [];
      voiceSamplesError = 'Select a samples folder to list files.';
      return;
    }

    if (!force && root === lastSamplesRoot && voiceSamples.length > 0) return;

    isLoadingSamples = true;
    voiceSamplesError = null;
    try {
      voiceSamples = await listVoiceSamples(root);
      lastSamplesRoot = root;
      if (voiceSamples.length === 0) {
        voiceSamplesError = 'No sample files found in the selected folder.';
      }
    } catch (err) {
      voiceSamplesError = err instanceof Error ? err.message : 'Failed to load samples.';
      voiceSamples = [];
    } finally {
      isLoadingSamples = false;
    }
  }

  $: if (showModal && formData.provider === 'vibevoice_local') {
    void loadVoiceSamples();
  }

  async function refreshVoiceSamples() {
    lastSamplesRoot = null;
    await loadVoiceSamples(true);
  }

  $: voiceRows = voices.map((voice, index) => ({
    voice,
    uiKey: `${voice.id}::${index}`,
  }));

  function openCreateModal() {
    editingId = null;
    formData = {
      displayName: '',
      provider: 'vibevoice_local',
      providerVoiceId: '',
      notes: '',
      previewUrl: null,
      metadata: {
        gender: 'U',
        ageRange: 'adult',
        accent: 'american',
        tags: ['style:normal'],
        imageUrl: null,
      },
    };
    showModal = true;
  }
  
  function handleDeduplicate() {
    dispatch('deduplicate');
  }

  function openEditModal(event: CustomEvent<Voice>) {
    const v = event.detail;
    editingId = v.id;
    formData = {
      ...v,
      provider: 'vibevoice_local',
      metadata: {
        ...v.metadata,
        ageRange: v.metadata?.ageRange || 'adult',
        accent: v.metadata?.accent || 'american',
        tags: setStyleTag(v.metadata?.tags, getStyleFromTags(v.metadata?.tags)),
      }
    };
    
    showModal = true;
  }
  
  // Get the names of characters using a specific voice
  function getCharacterNames(voiceId: string): string[] {
    const characterIds = assignments
      .filter(a => a.voiceId === voiceId)
      .map(a => a.characterId);
    
    return getCharacterNamesByIds(characterIds, characters);
  }

  function closeModal() {
    showModal = false;
    editingId = null;
  }

  function resolvePreviewUrl(voice: Voice): string | null {
    const explicitPreview = typeof voice.previewUrl === 'string' ? voice.previewUrl.trim() : '';
    if (explicitPreview.length > 0) return explicitPreview;

    if (voice.provider !== 'vibevoice_local' || !voice.providerVoiceId?.trim()) {
      return null;
    }

    const root = get(voiceSamplesRoot);
    if (!root) return null;
    return getVoiceSamplePreviewUrl(root, voice.providerVoiceId.trim());
  }

  async function playPreview(url: string) {
    previewAudio?.pause();
    previewAudio = new Audio(url);
    await previewAudio.play();
  }

  function generateId(): string {
    const provider = formData.provider || 'vibevoice_local';
    const voiceId = formData.providerVoiceId || 'unknown';
    return `${provider}-${voiceId}`;
  }

  function handleSubmit() {
    if (!formData.displayName?.trim()) {
      alert('Voice name is required');
      return;
    }

    if (!formData.providerVoiceId?.trim()) {
      alert('Provider Voice ID is required');
      return;
    }

    const metadata = formData.metadata || {};
    const normalizedStyle = getStyleFromTags(metadata.tags);
    const normalizedTags = setStyleTag(metadata.tags, normalizedStyle);

    const selectedSamplesRoot = get(voiceSamplesRoot);
    const selectedProviderVoiceId = formData.providerVoiceId?.trim() || '';
    const autoPreviewUrl =
      formData.provider === 'vibevoice_local' && selectedProviderVoiceId && selectedSamplesRoot
        ? getVoiceSamplePreviewUrl(selectedSamplesRoot, selectedProviderVoiceId)
        : null;

    if (editingId) {
      // Update existing voice
      voices = voices.map(v => 
        v.id === editingId 
          ? {
              ...formData,
              id: editingId,
              providerVoiceId: selectedProviderVoiceId,
              previewUrl: autoPreviewUrl || formData.previewUrl || null,
              metadata: {
                ...metadata,
                ageRange: metadata.ageRange || 'adult',
                accent: metadata.accent || 'american',
                tags: normalizedTags,
              },
            } as Voice
          : v
      );
    } else {
      // Create new voice
      const newVoice: Voice = {
        id: generateId(),
        displayName: formData.displayName!,
        provider: formData.provider || 'vibevoice_local',
        providerVoiceId: selectedProviderVoiceId,
        notes: formData.notes || '',
        previewUrl: autoPreviewUrl || formData.previewUrl || null,
        metadata: {
          gender: metadata.gender || 'U',
          ageRange: metadata.ageRange || 'adult',
          accent: metadata.accent || 'american',
          tags: normalizedTags,
          imageUrl: metadata.imageUrl || null,
        },
      };
      voices = [...voices, newVoice];
    }

    dispatch('update', voices);
    closeModal();
  }

  function handleDelete(event: CustomEvent<string>) {
    const id = event.detail;
    if (confirm('Are you sure you want to delete this voice?')) {
      voices = voices.filter(v => v.id !== id);
      dispatch('update', voices);
    }
  }

  function handlePreview(event: CustomEvent<string>) {
    const id = event.detail;
    const voice = voices.find(v => v.id === id);
    if (voice) {
      const previewUrl = resolvePreviewUrl(voice);
      if (!previewUrl) {
        alert('No preview available for this voice');
        return;
      }

      playPreview(previewUrl).catch(err => console.error('Preview error:', err));
    }
  }

  function handleModalPreview() {
    if (formData.provider !== 'vibevoice_local' || !formData.providerVoiceId?.trim()) {
      return;
    }

    const root = get(voiceSamplesRoot);
    if (!root) {
      alert('Select a samples folder to enable preview.');
      return;
    }

    const previewUrl = getVoiceSamplePreviewUrl(root, formData.providerVoiceId.trim());
    playPreview(previewUrl).catch(err => console.error('Preview error:', err));
  }

</script>

<div class="speaker-manager">
  <div class="manager-header">
    <h2 class="section-title">Voices</h2>
    <div class="header-actions">
      <button class="dedupe-btn" on:click={handleDeduplicate} title="Remove duplicate voices with the same provider voice ID">
        <RefreshCw size={18} />
        <span>Deduplicate</span>
      </button>
      <button class="add-speaker-btn" on:click={openCreateModal}>
        <Plus size={18} />
        <span>Add Voice</span>
      </button>
    </div>
  </div>

  <div class="speakers-grid">
    {#each voiceRows as row (row.uiKey)}
      <SpeakerCard
        voice={row.voice}
        characters={getCharacterNames(row.voice.id)}
        on:edit={openEditModal}
        on:delete={handleDelete}
        on:preview={handlePreview}
      />
    {/each}

    {#if voices.length === 0}
      <div class="empty-state">
        <p>No voices yet. Voices are automatically discovered from audio manifests. Click "Add Voice" to create one manually.</p>
      </div>
    {/if}
  </div>
</div>

{#if showModal}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="modal-backdrop" on:click={closeModal}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="modal-content" on:click|stopPropagation>
      <div class="modal-header">
        <h3>{editingId ? 'Edit Voice' : 'Create Voice'}</h3>
        <button class="close-btn" on:click={closeModal}>
          <X size={20} />
        </button>
      </div>

      <form class="modal-form" on:submit|preventDefault={handleSubmit}>
        <div class="form-group">
          <label for="display-name">Display Name *</label>
          <input
            id="display-name"
            type="text"
            bind:value={formData.displayName}
            placeholder="e.g., Black Knight Voice, Narrator"
            required
          />
        </div>

        <div class="form-group">
          <label for="provider">Provider *</label>
          <div class="provider-with-preview">
            <select id="provider" bind:value={formData.provider}>
              {#each PROVIDER_OPTIONS as option}
                <option value={option.value}>{option.label}</option>
              {/each}
            </select>
            <button type="button" class="preview-btn-inline" on:click={handleModalPreview} title="Play preview sample" disabled={!formData.providerVoiceId?.trim()}>
              <Play size={14} />
              <span>Preview</span>
            </button>
          </div>
        </div>

        {#if formData.provider === 'vibevoice_local'}
          <div class="form-group">
            <label for="vibevoice-sample">Voice Sample *</label>
            {#if voiceSamplesError}
              <span class="help-text">{voiceSamplesError}</span>
            {:else if $voiceSamplesRoot}
              <span class="help-text">Using samples from: {$voiceSamplesRoot}</span>
            {/if}
          </div>

          <div class="form-group">
            <div class="voice-select-with-preview">
              <select id="vibevoice-sample" bind:value={formData.providerVoiceId} required>
              <option value="">-- Select sample --</option>
              {#each voiceSamples as sample}
                <option value={sample}>{sample}</option>
              {/each}
              </select>
              <button type="button" class="preview-btn-inline" on:click={refreshVoiceSamples}>
                {isLoadingSamples ? 'Loading...' : 'Refresh'}
              </button>
            </div>
            {#if isLoadingSamples}
              <span class="help-text">Loading samples...</span>
            {:else if voiceSamples.length === 0 && $voiceSamplesRoot}
              <span class="help-text">No audio samples found in this folder.</span>
            {/if}
            <span class="help-text">Uses the selected file as the VibeVoice sample.</span>
          </div>
        {/if}

        <div class="form-group">
          <label for="gender">Gender</label>
          <div class="gender-selector">
            <label class="gender-option">
              <input type="radio" bind:group={formData.metadata.gender} value="M" />
              <span class="gender-circle male">M</span>
            </label>
            <label class="gender-option">
              <input type="radio" bind:group={formData.metadata.gender} value="F" />
              <span class="gender-circle female">F</span>
            </label>
            <label class="gender-option">
              <input type="radio" bind:group={formData.metadata.gender} value="U" />
              <span class="gender-circle unknown">U</span>
            </label>
          </div>
        </div>

        <div class="form-group">
          <label for="age-range">Age</label>
          <select id="age-range" bind:value={formData.metadata.ageRange}>
            {#each AGE_OPTIONS as option}
              <option value={option.value}>{option.label}</option>
            {/each}
          </select>
        </div>

        <div class="form-group">
          <label for="accent">Accent</label>
          <select id="accent" bind:value={formData.metadata.accent}>
            {#each ACCENT_OPTIONS as option}
              <option value={option.value}>{option.label}</option>
            {/each}
          </select>
        </div>

        <div class="form-group">
          <label for="style">Style</label>
          <select
            id="style"
            value={getStyleFromTags(formData.metadata.tags)}
            on:change={(e) => {
              const target = e.currentTarget as HTMLSelectElement;
              formData.metadata.tags = setStyleTag(formData.metadata.tags, target.value);
            }}
          >
            {#each STYLE_OPTIONS as option}
              <option value={option.value}>{option.label}</option>
            {/each}
          </select>
        </div>

        <div class="form-group">
          <label for="notes">Notes</label>
          <textarea
            id="notes"
            bind:value={formData.notes}
            placeholder="Add any notes about this voice..."
            rows="3"
          ></textarea>
        </div>

        <div class="form-group">
          <label for="image-url">Image URL (optional)</label>
          <input
            id="image-url"
            type="text"
            bind:value={formData.metadata.imageUrl}
            placeholder="https://..."
          />
          <span class="help-text">URL to character image (for future use)</span>
        </div>

        <div class="modal-actions">
          <button type="button" class="cancel-btn" on:click={closeModal}>
            Cancel
          </button>
          <button type="submit" class="submit-btn">
            {editingId ? 'Update' : 'Create'} Voice
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}

<style>
  .speaker-manager {
    padding: 12px;
  }

  .manager-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
  }

  .section-title {
    font-size: 18px;
    font-weight: 700;
    color: #1f2937;
    margin: 0;
  }

  .header-actions {
    display: flex;
    gap: 8px;
  }

  .dedupe-btn,
  .add-speaker-btn {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    color: white;
  }

  .add-speaker-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  }

  .dedupe-btn {
    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  }

  .dedupe-btn:hover,
  .add-speaker-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
  }

  .speakers-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 14px;
  }

  .empty-state {
    grid-column: 1 / -1;
    text-align: center;
    padding: 60px 20px;
    color: #6b7280;
    font-size: 16px;
  }

  /* Modal Styles */
  .modal-backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 20px;
  }

  .modal-content {
    background: white;
    border-radius: 12px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
    max-width: 500px;
    width: 100%;
    max-height: 90vh;
    overflow-y: auto;
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 24px;
    border-bottom: 1px solid #e5e7eb;
  }

  .modal-header h3 {
    font-size: 20px;
    font-weight: 700;
    color: #1f2937;
    margin: 0;
  }

  .close-btn {
    background: none;
    border: none;
    cursor: pointer;
    color: #6b7280;
    padding: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.2s ease;
  }

  .close-btn:hover {
    color: #1f2937;
  }

  .modal-form {
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .form-group label {
    font-size: 14px;
    font-weight: 600;
    color: #374151;
  }

  .form-group input[type="text"],
  .form-group select,
  .form-group textarea {
    padding: 10px 12px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    font-size: 14px;
    color: #111827;
    transition: border-color 0.15s ease;
  }

  .form-group input[type="text"]:focus,
  .form-group select:focus,
  .form-group textarea:focus {
    outline: none;
    border-color: #667eea;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
  }

  .form-group textarea {
    resize: vertical;
    font-family: inherit;
  }

  .help-text {
    font-size: 12px;
    color: #6b7280;
    font-style: italic;
  }

  .gender-selector {
    display: flex;
    gap: 16px;
  }

  .gender-option {
    cursor: pointer;
    display: flex;
    align-items: center;
  }

  .gender-option input[type="radio"] {
    display: none;
  }

  .gender-circle {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    font-size: 18px;
    font-weight: 700;
    color: white;
    border: 3px solid transparent;
    transition: all 0.2s ease;
  }

  .gender-circle.male {
    background: #3b82f6;
  }

  .gender-circle.female {
    background: #ec4899;
  }

  .gender-circle.unknown {
    background: #8b5cf6;
  }

  .gender-option input[type="radio"]:checked + .gender-circle {
    border-color: #1f2937;
    box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.2);
  }

  .modal-actions {
    display: flex;
    gap: 12px;
    padding-top: 12px;
    border-top: 1px solid #e5e7eb;
    margin-top: 8px;
  }

  .cancel-btn,
  .submit-btn {
    flex: 1;
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .cancel-btn {
    background: #f3f4f6;
    color: #1f2937;
  }

  .cancel-btn:hover {
    background: #e5e7eb;
  }

  .submit-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
  }

  .submit-btn:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
  }

  /* Voice Selector in Modal */
  .voice-select-with-preview,
  .provider-with-preview {
    display: flex;
    gap: 10px;
    align-items: center;
  }

  .voice-select-with-preview select,
  .provider-with-preview select {
    flex: 1;
  }

  .preview-btn-inline {
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    background: #3b82f6;
    color: white;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
  }

  .preview-btn-inline:disabled {
    opacity: 0.45;
    cursor: not-allowed;
    transform: none;
  }

  .preview-btn-inline:hover {
    background: #2563eb;
    transform: translateY(-1px);
  }

  .preview-btn-inline:active {
    transform: translateY(0);
  }
</style>

