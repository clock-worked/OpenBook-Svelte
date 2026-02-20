<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { ChevronDown, Check } from 'lucide-svelte';

  type DropdownItem = { value: string; label: string };

  export let items: DropdownItem[] | null = null; // if null, use <slot name="panel">
  export let selected: string | null = null;
  export let align: 'left' | 'right' = 'right';
  export let disabled: boolean = false;
  export let minWidth: number = 200;
  export let title: string = '';
  export let showCaret: boolean = true;
  // Anchored/controlled mode
  export let externalOpen: boolean | null = null; // if not null, component is controlled
  export let anchorX: number | null = null; // viewport x for anchored mode
  export let anchorY: number | null = null; // viewport y for anchored mode
  export let anchorOffsetX: number = -18; // fine-tune anchored position (left)
  export let anchorOffsetY: number = 6; // fine-tune anchored position (up)

  const dispatch = createEventDispatcher<{ select: { value: string }, requestClose: {} }>();

  let open = false;
  let triggerEl: HTMLButtonElement | null = null;
  let panelEl: HTMLDivElement | null = null;
  let openUp = false;

  let opened: boolean = false;
  $: opened = externalOpen !== null ? !!externalOpen : open;
  function setOpen(v: boolean) {
    if (externalOpen !== null) {
      if (!v) dispatch('requestClose');
    } else {
      open = v;
    }
  }
  function toggle() { if (!disabled) setOpen(!opened); }
  function close() { setOpen(false); if (!anchorX && !anchorY) triggerEl?.focus(); }
  function handleKey(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
  }

  onMount(() => {
    const onDocKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onDocKey);
    return () => document.removeEventListener('keydown', onDocKey);
  });

  function computeFlip() {
    try {
      const btnRect = triggerEl?.getBoundingClientRect();
      const panelRect = panelEl?.getBoundingClientRect();
      const y = anchorY != null ? anchorY : (btnRect?.bottom ?? 0);
      const below = Math.max(0, window.innerHeight - y);
      const need = (panelRect?.height ?? 200) + 12;
      const above = anchorY != null ? (anchorY ?? 0) : (btnRect?.top ?? 0);
      openUp = need > below && above > below;
    } catch {}
  }

  $: if (opened) {
    // compute after mount
    setTimeout(computeFlip, 0);
  }

  let isAnchored: boolean = false;
  let gapPx: number = 36;
  let panelStyle: string = '';
  $: isAnchored = (externalOpen !== null && anchorX != null && anchorY != null);
  $: gapPx = isAnchored ? 12 : 36;
  $: panelStyle = `min-width:${minWidth}px; ${isAnchored ? '' : (align === 'right' ? 'right:0;' : 'left:0;')} ${opened ? (openUp ? `bottom:${gapPx}px;top:auto;` : `top:${gapPx}px;`) : ''}`;
</script>

<div class="dd-wrap" class:anchored={isAnchored} style={isAnchored ? `left:${(anchorX ?? 0) + anchorOffsetX}px;top:${(anchorY ?? 0) + anchorOffsetY}px;` : ''}>
  <button
    bind:this={triggerEl}
    class="dd-button"
    class:hidden={isAnchored}
    on:click={toggle}
    aria-haspopup="listbox"
    aria-expanded={opened}
    {title}
    disabled={disabled}
  >
    <span class="dd-trigger"><slot /></span>
    {#if showCaret}
      <span class="chevron {opened ? 'rotated' : ''}">
        <ChevronDown size={14} />
      </span>
    {/if}
  </button>

  {#if opened}
    <div
      class="dd-backdrop"
      role="button"
      tabindex="0"
      aria-label="Close menu"
      on:click={close}
      on:keydown={handleKey}
    ></div>
    <div
      class="dd-panel"
      bind:this={panelEl}
      class:left={align === 'left'}
      class:up={openUp}
      role={items ? 'listbox' : 'dialog'}
      style={panelStyle}
    >
      {#if items}
        {#each items as it}
          <button
            class="dd-item"
            role="option"
            aria-selected={selected === it.value}
            on:click={() => { dispatch('select', { value: it.value }); close(); }}
          >
            {#if selected === it.value}
              <Check size={14} />
            {:else}
              <span class="dd-check-spacer"></span>
            {/if}
            <span>{it.label}</span>
          </button>
        {/each}
      {:else}
        <slot name="panel" {close} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .dd-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border: 1px solid #dadce0;
    background: #ffffff;
    border-radius: 8px;
    cursor: pointer;
    color: #3c4043;
    transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }

  .dd-button:hover:not(:disabled) { background:#f8f9fa; border-color:#c6c6c6; }
  .dd-button:active:not(:disabled) { background:#f1f3f4; box-shadow: inset 0 1px 2px rgba(0,0,0,0.06); }
  .dd-button:disabled { opacity:0.6; cursor:not-allowed; }

  .dd-backdrop { position:fixed; inset:0; background:transparent; z-index: 15; }
  .dd-panel {
    position: absolute;
    top: 36px;
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.12);
    padding: 6px;
    z-index: 20;
  }
  .dd-panel::before {
    content: '';
    position: absolute;
    top: -6px;
    width: 10px; height: 10px;
    background: #ffffff;
    border-left: 1px solid #e0e0e0;
    border-top: 1px solid #e0e0e0;
    transform: rotate(45deg);
    box-shadow: -2px -2px 2px rgba(0,0,0,0.02);
    right: 16px;
  }
  :global(.dd-panel.left)::before { left: 16px; right: auto; }
  .dd-panel.up { top: auto; bottom: 36px; }
  .dd-panel.up::before { top: auto; bottom: -6px; transform: rotate(225deg); }

  .dd-item {
    display:flex; align-items:center; gap:10px; width:100%;
    border:none; background:transparent; text-align:left;
    padding:8px 10px; border-radius:8px; cursor:pointer; color:#202124;
  }
  .dd-item:hover { background:#f5f7f8; }
  .dd-check-spacer { display:inline-block; width:14px; height:14px; }

  .dd-wrap.anchored {
    position: fixed;
  }

  .dd-wrap:not(.anchored) {
    position: relative;
  }

  .dd-wrap {
    display: inline-block;
  }

  .dd-button.hidden {
    display: none;
  }

  .chevron {
    transition: transform .2s ease;
  }

  .chevron.rotated {
    transform: rotate(180deg);
  }
</style>


