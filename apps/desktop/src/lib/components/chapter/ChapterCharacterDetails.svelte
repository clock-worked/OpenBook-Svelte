<script lang="ts">
  import { Star } from 'lucide-svelte';

  export let selectedName: string;
  export let aliasItems: Array<{ name: string; isPrimary: boolean; count: number }> = [];
  export let color: string = '#ccc';
  export let onDetach: (aliasName: string) => void;
  export let onSetPrimary: (aliasName: string) => void;
</script>

<style>
  .details-panel {
    border: 1px solid #eee;
    border-radius: 6px;
    padding: 10px;
  }

  .details-title {
    font-weight: 600;
    font-size: 13px;
    color: #6b7280;
    margin-bottom: 6px;
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
    color: #6b7280;
    font-size: 16px;
    line-height: 1;
  }

  .alias-count {
    font-size: 11px;
    color: #222;
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid rgba(0, 0, 0, 0.1);
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
  }

  .alias-action {
    border: none;
    background: transparent;
    cursor: pointer;
    color: #6b7280;
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
  <div class="details-title">Alias Cluster</div>
  <ul class="alias-list">
    {#each aliasItems as alias, index (index)}
      <li class="alias-item">
        <span class="alias-bullet">*</span>
        <span class="alias-count" style={`background:${color}`}>{alias.count}</span>
        <span class="alias-name">{alias.name}</span>
        {#if alias.isPrimary}
          <span class="alias-star" title="Display name">
            <Star size={14} color="#f59e0b" />
          </span>
        {:else}
          <button
            class="alias-action alias-primary-btn"
            title={`Set ${alias.name} as display name`}
            aria-label={`Set ${alias.name} as display name`}
            on:click={() => onSetPrimary(alias.name)}
          >
            <Star size={14} color="#9ca3af" />
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
