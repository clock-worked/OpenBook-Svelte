<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { tick } from 'svelte';
  import { Check } from 'lucide-svelte';

  export let items: { value: string; label: string }[] = [];
  export let selected: string | null = null;
  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const VIEWPORT_MARGIN = 8;
  const ANCHOR_GAP = 8;

  let menuEl: HTMLDivElement | null = null;
  let menuTop = 0;
  let openUpward = false;

  const dispatch = createEventDispatcher<{ 
    select: { value: string },
    close: {}
  }>();

  function handleSelect(value: string) {
    dispatch('select', { value });
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

  async function updateMenuPosition() {
    if (!visible) return;
    await tick();
    if (!menuEl) return;

    const menuHeight = menuEl.offsetHeight;
    const spaceBelow = window.innerHeight - y - VIEWPORT_MARGIN;
    const shouldOpenUpward = menuHeight > spaceBelow;

    openUpward = shouldOpenUpward;

    if (shouldOpenUpward) {
      menuTop = Math.max(VIEWPORT_MARGIN, y - menuHeight - ANCHOR_GAP);
    } else {
      menuTop = y;
    }
  }

  $: if (visible) {
    void updateMenuPosition();
  }
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="backdrop" on:click={handleBackdropClick}></div>
  
  <div 
    bind:this={menuEl}
    class="character-menu" 
    class:open-upward={openUpward}
    style="left: {x}px; top: {menuTop}px;"
    role="menu"
    tabindex="0"
    on:keydown={handleKeydown}
  >
    {#each items as item}
      <button
        class="menu-item"
        role="menuitem"
        on:click={() => handleSelect(item.value)}
      >
        {#if selected === item.value}
          <Check size={14} />
        {:else}
          <span class="check-spacer"></span>
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
    z-index: 15;
  }

  .character-menu {
    position: fixed;
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
    padding: 6px;
    z-index: 20;
    min-width: 200px;
    max-height: 400px;
    overflow-y: auto;
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

  .character-menu::before {
    content: '';
    position: absolute;
    top: -6px;
    left: 16px;
    width: 10px;
    height: 10px;
    background: #ffffff;
    border-left: 1px solid #e0e0e0;
    border-top: 1px solid #e0e0e0;
    transform: rotate(45deg);
    box-shadow: -2px -2px 2px rgba(0, 0, 0, 0.02);
  }

  .character-menu.open-upward::before {
    top: auto;
    bottom: -6px;
    border-left: none;
    border-top: none;
    border-right: 1px solid #e0e0e0;
    border-bottom: 1px solid #e0e0e0;
    box-shadow: 2px 2px 2px rgba(0, 0, 0, 0.02);
  }

  .menu-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    border: none;
    background: transparent;
    text-align: left;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    color: #202124;
    transition: background 0.15s ease;
  }

  .menu-item:hover {
    background: #f5f7f8;
  }

  .check-spacer {
    display: inline-block;
    width: 14px;
    height: 14px;
  }

  .label {
    flex: 1;
  }
</style>


