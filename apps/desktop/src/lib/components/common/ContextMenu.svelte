<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let items: { 
    label: string; 
    icon?: any; 
    action: string;
    color?: string;
  }[] = [];
  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{ 
    action: { action: string },
    close: {}
  }>();

  function handleAction(action: string) {
    dispatch('action', { action });
    dispatch('close');
  }

  function handleBackdropClick() {
    dispatch('close');
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      dispatch('close');
    }
  }
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="backdrop" on:click={handleBackdropClick}></div>
  
  <div 
    class="context-menu" 
    style="left: {x}px; top: {y}px;"
    role="menu"
    tabindex="0"
    on:keydown={handleKeydown}
  >
    {#each items as item}
      <button
        class="menu-item"
        class:colored={item.color}
        style={item.color ? `color: ${item.color};` : ''}
        role="menuitem"
        on:click={() => handleAction(item.action)}
      >
        {#if item.icon}
          <svelte:component this={item.icon} size={16} />
        {/if}
        <span class="label">{item.label}</span>
      </button>
    {/each}
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: transparent;
    z-index: 999;
  }

  .context-menu {
    position: fixed;
    background: white;
    border: 1px solid #dadce0;
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
    padding: 4px;
    z-index: 1000;
    min-width: 200px;
    animation: fadeIn 0.15s ease;
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
      transform: scale(0.95);
    }
    to {
      opacity: 1;
      transform: scale(1);
    }
  }

  .menu-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    border: none;
    background: white;
    color: #202124;
    text-align: left;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: background 0.15s ease;
    border-radius: 4px;
  }

  .menu-item:hover {
    background: #f8f9fa;
  }

  .menu-item:active {
    background: #e8eaed;
  }

  .label {
    flex: 1;
  }

  .menu-item :global(svg) {
    flex-shrink: 0;
  }
</style>


