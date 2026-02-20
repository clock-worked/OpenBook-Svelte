<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { Volume2 } from 'lucide-svelte';

  export let lineId: number;
  export let characterName: string;
  export let x: number;
  export let y: number;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    confirm: {};
    close: {};
  }>();

  function handleConfirm() {
    dispatch('confirm');
  }

  function handleClose() {
    dispatch('close');
  }

  function handleBackdropClick() {
    dispatch('close');
  }
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="backdrop" on:click={handleBackdropClick}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div 
      class="generate-prompt" 
      style="left: {x}px; top: {y}px; transform: translateX(-50%);"
      on:click|stopPropagation
    >
      <div class="message">
        No audio for <strong>{characterName}</strong> (Line {lineId})
      </div>
      <button 
        class="confirm-btn"
        on:click={handleConfirm}
        title="Generate audio for this line"
      >
        <Volume2 size={16} />
        <span>Generate Audio</span>
      </button>
      <button 
        class="cancel-btn"
        on:click={handleClose}
        title="Cancel"
      >
        Cancel
      </button>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 999;
    animation: fadeIn 0.15s ease;
  }

  @keyframes fadeIn {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }

  .generate-prompt {
    position: fixed;
    background: white;
    border: 1px solid #dadce0;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.2);
    padding: 16px;
    z-index: 1000;
    min-width: 280px;
    animation: slideDown 0.2s ease;
  }

  @keyframes slideDown {
    from {
      opacity: 0;
      transform: translateX(-50%) translateY(-8px);
    }
    to {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
  }

  .message {
    font-size: 14px;
    color: #5f6368;
    margin-bottom: 12px;
    line-height: 1.4;
  }

  .message strong {
    color: #202124;
    font-weight: 600;
  }

  .confirm-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 100%;
    padding: 10px 16px;
    border: none;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    transition: all 0.2s ease;
    margin-bottom: 8px;
  }

  .confirm-btn:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
  }

  .confirm-btn:active {
    transform: translateY(0);
  }

  .cancel-btn {
    width: 100%;
    padding: 8px 16px;
    border: 1px solid #dadce0;
    background: white;
    color: #5f6368;
    border-radius: 8px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.2s ease;
  }

  .cancel-btn:hover {
    background: #f8f9fa;
    border-color: #5f6368;
    color: #202124;
  }
</style>


