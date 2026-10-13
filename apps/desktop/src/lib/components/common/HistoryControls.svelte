<script lang="ts">
  import { Undo2, Redo2 } from 'lucide-svelte';
  import { history } from '$lib/services/history';
</script>

<div class="history-controls" role="group" aria-label="Edit history">
  <button
    class="icon-btn"
    disabled={!$history.canUndo || $history.busy}
    title={$history.undoLabel ? `Undo: ${$history.undoLabel} (Ctrl+Z)` : 'Undo (Ctrl+Z)'}
    aria-label="Undo"
    on:click={() => history.undo()}
  >
    <Undo2 size={17} />
  </button>
  <button
    class="icon-btn"
    disabled={!$history.canRedo || $history.busy}
    title={$history.redoLabel ? `Redo: ${$history.redoLabel} (Ctrl+Shift+Z or Ctrl+Y)` : 'Redo (Ctrl+Shift+Z or Ctrl+Y)'}
    aria-label="Redo"
    on:click={() => history.redo()}
  >
    <Redo2 size={17} />
  </button>
</div>

<style>
  .history-controls { display: flex; gap: 4px; align-items: center; margin-left: auto; }
  .icon-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
    border: none;
    background: transparent;
    border-radius: 6px;
    cursor: pointer;
    color: var(--app-text-muted);
    transition: all 0.2s ease;
  }
  .icon-btn:hover:not(:disabled) {
    background: var(--app-surface-hover);
    color: var(--app-primary);
  }
  .icon-btn:active:not(:disabled) {
    background: var(--app-surface-active);
    transform: scale(0.95);
  }
  .icon-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
</style>
