<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let buttons: {
    icon: any;
    action: string;
    title: string;
    disabled?: boolean;
  }[] = [];
  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{ 
    action: { action: string },
    close: {}
  }>();

  let isHoveringToolbar = false;
  let closeTimeout: any = null;

  function handleAction(action: string) {
    dispatch('action', { action });
  }

  function handleMouseEnter() {
    isHoveringToolbar = true;
    if (closeTimeout) {
      clearTimeout(closeTimeout);
      closeTimeout = null;
    }
  }

  function handleMouseLeave() {
    isHoveringToolbar = false;
    // Add a delay before closing
    closeTimeout = setTimeout(() => {
      if (!isHoveringToolbar) {
        dispatch('close');
      }
    }, 300);
  }
</script>

{#if visible}
  <div
    class="button-toolbar"
    style="left: {x}px; top: {y}px; transform: translate(-50%, 12px);"
    role="toolbar"
    tabindex="0"
    on:mouseenter={handleMouseEnter}
    on:mouseleave={handleMouseLeave}
  >
    {#each buttons as button}
      <button
        class="toolbar-btn"
        title={button.title}
        disabled={button.disabled}
        on:click={() => handleAction(button.action)}
      >
        <svelte:component this={button.icon} size={20} />
      </button>
    {/each}
  </div>
{/if}

<style>
  .button-toolbar {
    position: fixed;
    display: flex;
    gap: 4px;
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 10px;
    padding: 6px;
    box-shadow: var(--app-shadow-md);
    z-index: 1000;
    animation: slideDown 0.2s ease;
  }

  @keyframes slideDown {
    from {
      opacity: 0;
      transform: translate(-50%, 0);
    }
    to {
      opacity: 1;
      transform: translate(-50%, 12px);
    }
  }

  .button-toolbar::before {
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

  .toolbar-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 6px 10px;
    border: none;
    background: transparent;
    border-radius: 8px;
    cursor: pointer;
    color: var(--app-text-muted);
    transition: background 0.2s ease;
  }

  .toolbar-btn:hover:not(:disabled) {
    background: var(--app-surface-hover);
  }

  .toolbar-btn:active:not(:disabled) {
    background: var(--app-surface-active);
  }

  .toolbar-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .toolbar-btn :global(svg) {
    flex-shrink: 0;
  }
</style>


