<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let text: string;
  export let visible: boolean = false;

  const dispatch = createEventDispatcher<{
    confirm: { text: string };
    close: {};
  }>();

  let textarea: HTMLTextAreaElement | null = null;
  let readText = text;

  $: readText = text;

  $: if (visible && textarea) {
    setTimeout(() => {
      textarea?.focus();
    }, 0);
  }

  function handleConfirm() {
    const trimmed = readText.trim();
    if (!trimmed) return;
    dispatch('confirm', { text: trimmed });
  }

  function handleClose() {
    dispatch('close');
  }

  // Plain Enter inserts a newline (dialogue lines can be multi-line);
  // Ctrl+Enter confirms.
  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleConfirm();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      handleClose();
    }
  }

  function handleBackdropClick() {
    handleClose();
  }
</script>

{#if visible}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="overlay" on:click={handleBackdropClick}>
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="dialog" on:click|stopPropagation>
      <h3>Read As</h3>
      <p class="hint">
        Edit how this line is read aloud &mdash; e.g. <code>41/41</code> &rarr;
        <code>41 of 41</code>. The line text itself is not changed.
      </p>
      <textarea
        bind:this={textarea}
        bind:value={readText}
        class="textarea"
        on:keydown={handleKeydown}
        rows="5"
      ></textarea>
      <div class="actions">
        <button class="cancel-btn" on:click={handleClose}>Cancel (Esc)</button>
        <button class="save-btn" on:click={handleConfirm}>Generate Audio (Ctrl+Enter)</button>
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
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 12px;
    padding: 24px;
    width: 90%;
    max-width: 600px;
    box-shadow: var(--app-shadow-lg);
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
    margin: 0 0 8px 0;
    font-size: 18px;
    font-weight: 600;
    color: var(--app-text);
  }

  .hint {
    margin: 0 0 16px 0;
    font-size: 13px;
    color: var(--app-text-muted);
    line-height: 1.5;
  }

  .hint code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    background: var(--app-surface-raised);
    border: 1px solid var(--app-border);
    border-radius: 4px;
    padding: 1px 4px;
  }

  .textarea {
    width: 100%;
    min-height: 120px;
    padding: 12px;
    border: 2px solid var(--app-border);
    border-radius: 8px;
    background: var(--app-surface-raised);
    color: var(--app-text);
    font-family: inherit;
    font-size: 14px;
    line-height: 1.5;
    resize: vertical;
    outline: none;
    transition: border-color 0.2s ease;
  }

  .textarea:focus {
    border-color: var(--app-primary);
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
    background: var(--app-surface-hover);
    color: var(--app-text-muted);
  }

  .cancel-btn:hover {
    background: var(--app-surface-active);
  }

  .save-btn {
    background: var(--app-primary);
    color: var(--app-text-inverse);
  }

  .save-btn:hover {
    background: var(--app-primary-hover);
  }

  .save-btn:active {
    background: var(--app-primary-active);
  }
</style>
