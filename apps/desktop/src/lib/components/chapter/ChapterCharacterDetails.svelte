<script lang="ts">
  import type { Gender } from '$lib/types';
  import { Star } from 'lucide-svelte';

  export let selectedName: string;
  export let gender: Gender = 'Unknown';
  export let aliasItems: Array<{ name: string; isPrimary: boolean; count: number }> = [];
  export let color: string = 'var(--app-primary-soft)';
  export let onDetach: (aliasName: string) => void;
  export let onSetPrimary: (aliasName: string) => void;
  export let onSetGender: (gender: Gender) => void;

  const genderOptions: Gender[] = ['Male', 'Female', 'Unknown'];
</script>

<style>
  .details-panel {
    border: 1px solid var(--app-border-subtle);
    background: var(--app-surface-subtle);
    border-radius: 6px;
    padding: 10px;
  }

  .details-title {
    font-weight: 600;
    font-size: 13px;
    color: var(--app-text-muted);
    margin-bottom: 6px;
  }

  .gender-row {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }

  .gender-btn {
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-text-muted);
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 120ms ease, background-color 120ms ease, color 120ms ease;
  }

  .gender-btn.active {
    color: var(--app-text);
    border-color: transparent;
  }

  .gender-btn.male.active {
    background: rgba(168, 218, 220, 0.24);
  }

  .gender-btn.female.active {
    background: rgba(255, 193, 204, 0.24);
  }

  .gender-btn.unknown.active {
    background: var(--app-primary-soft);
  }

  .alias-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .alias-item {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .alias-bullet {
    color: var(--app-text-muted);
    font-size: 16px;
    line-height: 1;
  }

  .alias-count {
    font-size: 11px;
    color: var(--app-text);
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid var(--app-border-subtle);
    min-width: 26px;
    text-align: center;
  }

  .alias-name {
    flex: 1;
  }

  .alias-star {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--app-warning);
  }

  .alias-action {
    border: none;
    background: transparent;
    cursor: pointer;
    color: var(--app-text-muted);
    font-size: 12px;
    opacity: 0;
    pointer-events: none;
  }

  .alias-primary-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .alias-item:hover .alias-action {
    opacity: 1;
    pointer-events: auto;
  }
</style>

<div class="details-panel">
  <div class="details-title">Gender</div>
  <div class="gender-row">
    {#each genderOptions as option}
      <button
        class="gender-btn"
        class:active={gender === option}
        class:male={option === 'Male'}
        class:female={option === 'Female'}
        class:unknown={option === 'Unknown'}
        type="button"
        on:click={() => onSetGender(option)}
      >
        {option}
      </button>
    {/each}
  </div>

  <div class="details-title">Alias Cluster</div>
  <ul class="alias-list">
    {#each aliasItems as alias, index (index)}
      <li class="alias-item">
        <span class="alias-bullet">*</span>
        <span class="alias-count" style={`background:${color}`}>{alias.count}</span>
    <span class="alias-name">{alias.name}</span>
        {#if alias.isPrimary}
          <span class="alias-star" title="Display name">
            <Star size={14} />
          </span>
        {:else}
          <button
            class="alias-action alias-primary-btn"
            title={`Set ${alias.name} as display name`}
            aria-label={`Set ${alias.name} as display name`}
            on:click={() => onSetPrimary(alias.name)}
          >
            <Star size={14} />
          </button>
          <button
            class="alias-action alias-detach"
            title={`Delete alias ${alias.name} from ${selectedName}`}
            on:click={() => onDetach(alias.name)}
          >
            Delete Alias
          </button>
        {/if}
      </li>
    {/each}
  </ul>
</div>
