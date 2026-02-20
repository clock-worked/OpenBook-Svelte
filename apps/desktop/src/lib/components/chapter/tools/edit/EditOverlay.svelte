<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';

  export let text: string;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    save: { text: string };
    cancel: {};
  }>();

  let textarea: HTMLTextAreaElement | null = null;
  let editingText = text;

  $: editingText = text;

  onMount(() => {
    if (visible && textarea) {
      textarea.focus();
      textarea.select();
    }
  });

  $: if (visible && textarea) {
    setTimeout(() => {
      textarea?.focus();
      textarea?.select();
    }, 0);
  }

  function handleSave() {
    dispatch('save', { text: editingText });
  }

  function handleCancel() {
    dispatch('cancel');
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      handleCancel();
    }
  }

  function handleBackdropClick() {
    handleCancel();
  }
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="overlay" on:click={handleBackdropClick}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="dialog" on:click|stopPropagation>
      <h3>Edit Line</h3>
      <textarea
        bind:this={textarea}
        bind:value={editingText}
        class="textarea"
        on:keydown={handleKeydown}
        rows="5"
      ></textarea>
      <div class="actions">
        <button class="cancel-btn" on:click={handleCancel}>Cancel (Esc)</button>
        <button class="save-btn" on:click={handleSave}>Save (Enter)</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 2000;
    animation: fadeIn 0.2s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  .dialog {
    background: #ffffff;
    border-radius: 12px;
    padding: 24px;
    width: 90%;
    max-width: 600px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
    animation: slideUp 0.2s ease;
  }

  @keyframes slideUp {
    from { 
      opacity: 0;
      transform: translateY(20px);
    }
    to { 
      opacity: 1;
      transform: translateY(0);
    }
  }

  .dialog h3 {
    margin: 0 0 16px 0;
    font-size: 18px;
    font-weight: 600;
    color: #202124;
  }

  .textarea {
    width: 100%;
    min-height: 120px;
    padding: 12px;
    border: 2px solid #dadce0;
    border-radius: 8px;
    background: #ffffff;
    font-family: inherit;
    font-size: 14px;
    line-height: 1.5;
    resize: vertical;
    outline: none;
    transition: border-color 0.2s ease;
  }

  .textarea:focus {
    border-color: #1a73e8;
  }

  .actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
    margin-top: 16px;
  }

  .cancel-btn, .save-btn {
    padding: 8px 20px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .cancel-btn {
    background: #f1f3f4;
    color: #5f6368;
  }

  .cancel-btn:hover {
    background: #e8eaed;
  }

  .save-btn {
    background: #1a73e8;
    color: #ffffff;
  }

  .save-btn:hover {
    background: #1557b0;
  }

  .save-btn:active {
    background: #0d47a1;
  }
</style>


