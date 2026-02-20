<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Voice } from '$lib/types';
  import { Play, Trash2, Edit2 } from 'lucide-svelte';
  import PillList from '../common/PillList.svelte';

  export let voice: Voice;
  export let editable: boolean = true;
  export let characters: string[] = [];

  const dispatch = createEventDispatcher<{
    edit: Voice;
    delete: string;
    preview: string;
  }>();

  function handleEdit() {
    dispatch('edit', voice);
  }

  function handleDelete() {
    dispatch('delete', voice.id);
  }

  function handlePreview() {
    dispatch('preview', voice.id);
  }

  function getGenderColor(gender: 'M' | 'F' | 'U'): string {
    switch (gender) {
      case 'M': return '#3b82f6'; // blue
      case 'F': return '#ec4899'; // pink
      case 'U': return '#8b5cf6'; // purple
    }
  }
</script>

<div class="speaker-card">
  <div class="card-body">
    <h3 class="speaker-name">{voice.displayName}</h3>
    
    <div class="speaker-stats">
      <div class="stat-row">
        <span class="stat-label">VOICE ID</span>
        <span class="stat-value voice-id">{voice.providerVoiceId || 'N/A'}</span>
      </div>

      {#if characters.length > 0}
        <div class="characters-section">
          <span class="stat-label">CHARACTERS</span>
          <div class="characters-pills">
            <PillList selected={characters} readonly={true} />
          </div>
        </div>
      {/if}
      
      {#if voice.metadata.totalClips}
        <div class="stat-row">
          <span class="stat-label">CLIPS</span>
          <span class="stat-value">{voice.metadata.totalClips}</span>
        </div>
      {/if}
      
      {#if voice.metadata.gender}
        <div class="stat-row">
          <span class="stat-label">GENDER</span>
          <div class="gender-badge" style="background-color: {getGenderColor(voice.metadata.gender)}">
            {voice.metadata.gender}
          </div>
        </div>
      {/if}
    </div>

    {#if voice.notes}
      <div class="speaker-notes">
        <p>{voice.notes}</p>
      </div>
    {/if}

    {#if editable}
      <div class="card-actions">
        <button class="action-btn preview-btn" on:click={handlePreview} title="Preview voice">
          <Play size={14} />
          <span>Preview</span>
        </button>
        <button class="action-btn edit-btn" on:click={handleEdit} title="Edit speaker">
          <Edit2 size={14} />
          <span>Edit</span>
        </button>
        <button class="action-btn delete-btn" on:click={handleDelete} title="Delete speaker">
          <Trash2 size={14} />
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .speaker-card {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-radius: 16px;
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2), 0 2px 4px rgba(0, 0, 0, 0.1);
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    width: 280px;
    min-height: 380px;
    display: flex;
    flex-direction: column;
  }

  .speaker-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 12px 24px rgba(0, 0, 0, 0.3), 0 4px 8px rgba(0, 0, 0, 0.15);
  }

  .card-body {
    background: #ffffff;
    padding: 20px;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .speaker-name {
    font-size: 22px;
    font-weight: 700;
    color: #1f2937;
    text-align: center;
    margin: 0;
    padding-bottom: 12px;
    border-bottom: 2px solid #e5e7eb;
    letter-spacing: 0.5px;
  }

  .speaker-stats {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    background: #f9fafb;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
  }

  .characters-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 12px;
    background: #f9fafb;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
  }

  .characters-pills {
    display: flex;
    flex-wrap: wrap;
  }

  .stat-label {
    font-size: 11px;
    font-weight: 700;
    color: #6b7280;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }

  .stat-value {
    font-size: 14px;
    font-weight: 600;
    color: #1f2937;
  }

  .stat-value.voice-id {
    font-family: 'Courier New', monospace;
    font-size: 12px;
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .gender-badge {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    color: white;
    font-size: 13px;
    font-weight: 700;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  }

  .speaker-notes {
    padding: 10px 12px;
    background: #fef3c7;
    border-left: 3px solid #f59e0b;
    border-radius: 6px;
    flex: 1;
  }

  .speaker-notes p {
    margin: 0;
    font-size: 13px;
    color: #78350f;
    line-height: 1.5;
  }

  .card-actions {
    display: flex;
    gap: 8px;
    margin-top: auto;
    padding-top: 8px;
    border-top: 1px solid #e5e7eb;
  }

  .action-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    padding: 8px 10px;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .preview-btn {
    background: #3b82f6;
    color: white;
  }

  .preview-btn:hover {
    background: #2563eb;
    transform: translateY(-1px);
  }

  .edit-btn {
    background: #10b981;
    color: white;
  }

  .edit-btn:hover {
    background: #059669;
    transform: translateY(-1px);
  }

  .delete-btn {
    background: #ef4444;
    color: white;
    flex: 0 0 auto;
    min-width: 40px;
  }

  .delete-btn:hover {
    background: #dc2626;
    transform: translateY(-1px);
  }

  .action-btn:active {
    transform: translateY(0);
  }
</style>

