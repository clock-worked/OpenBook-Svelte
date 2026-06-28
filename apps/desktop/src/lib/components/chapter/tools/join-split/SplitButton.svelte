<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let lineId: number;
  export let position: number;
  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    split: { lineId: number; position: number };
  }>();

  function handleSplit() {
    dispatch('split', { lineId, position });
  }
</script>

{#if visible}
  <div 
    class="split-button" 
    style="left: {x}px; top: {y}px; transform: translateX(-50%);"
  >
    <button 
      class="btn"
      on:click={handleSplit}
      title="Split line at cursor"
    >
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M4 10H16M10 4L10 16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>
      <span>Split</span>
    </button>
  </div>
{/if}

<style>
  .split-button {
    position: fixed;
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 10px;
    padding: 6px;
    box-shadow: var(--app-shadow-md);
    z-index: 1000;
  }

  .split-button::before {
    content: '';
    position: absolute;
    top: -6px;
    left: 50%;
    transform: translateX(-50%) rotate(45deg);
    width: 10px;
    height: 10px;
    background: var(--app-surface);
    border-left: 1px solid var(--app-border);
    border-top: 1px solid var(--app-border);
    box-shadow: -2px -2px 2px rgba(0, 0, 0, 0.02);
  }

  .btn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border: none;
    background: var(--app-primary);
    color: var(--app-text-inverse);
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: background 0.2s ease;
  }

  .btn:hover {
    background: var(--app-primary-hover);
  }

  .btn:active {
    background: var(--app-primary-active);
  }

  .btn svg {
    flex-shrink: 0;
  }

</style>


