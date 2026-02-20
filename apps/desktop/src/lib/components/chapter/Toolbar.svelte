<script lang="ts">
  import type { ScriptJson } from "$lib/types";
  import { writable, derived, get } from "svelte/store";
  import {
    currentChapter,
    currentScript,
    bookRoot,
    bookRootHandle,
    chapters,
  } from "$lib/stores/bookState";
  import { conflictCursor, toolMode } from "$lib/stores/selection";
  import { estimatedCostPerChar, defaultNarrator } from "$lib/stores/settings";
  import {
    getScriptPath,
    readScript,
    scanChapters,
    selectBookDirectory,
    triggerStatsUpdate,
  } from "$lib/services/fs";
  import { runParserForChapter } from "$lib/services/parser";
  import {
    RefreshCw,
    ChevronUp,
    ChevronDown,
    FolderOpen,
    FileText,
    Merge,
    PenSquare,
    Music,
  } from "lucide-svelte";
  import { forceRefreshBookCharacters } from "$lib/stores/bookCharacters";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";

  const conflicts = derived(
    currentScript,
    (scr) => {
      if (!scr) return 0;
      return scr.lines.filter((l) => l.isConflict || !l.chosenSpeaker).length;
    },
    0,
  );
  const busy = writable<boolean>(false);
  const folderBusy = writable<boolean>(false);
  const lastError = writable<string | null>(null);

  function prev() {
    jump(-1);
  }
  function next() {
    jump(1);
  }

  async function jump(dir: 1 | -1) {
    const ch: any = get(currentChapter);
    if (!ch || !ch.scriptPath) return;
    try {
      const data = (await readScript(ch.scriptPath)) as ScriptJson | null;
      if (!data) {
        conflictCursor.set(null);
        return;
      }
      const ids = data.lines
        .filter((l) => l.isConflict || !l.chosenSpeaker)
        .map((l) => l.id);
      if (!ids.length) {
        conflictCursor.set(null);
        return;
      }
      const cur = get(conflictCursor);
      if (cur == null) {
        conflictCursor.set(ids[0]);
        return;
      }
      const idx = ids.indexOf(cur);
      let nextIdx = idx + dir;
      if (nextIdx < 0) nextIdx = ids.length - 1;
      if (nextIdx >= ids.length) nextIdx = 0;
      conflictCursor.set(ids[nextIdx]);
    } catch {}
  }
  async function regenerate() {
    const ch: any = get(currentChapter);
    const root = get(bookRoot);
    if (!ch || !root) return;
    busy.set(true);
    lastError.set(null);
    try {
      const narrator = get(defaultNarrator);
      console.debug("[Regenerate] Starting parser", {
        input: ch.path,
        narrator,
      });
      const result = await runParserForChapter({ input: ch.path, narrator });
      if (!result.ok) {
        console.error("[Regenerate] Parser failed", result.error);
        lastError.set(result.error || "Unknown parser error");
        return;
      }
      // Prefer the in-folder script path we just wrote
      const scriptPath =
        result.ok && result.scriptPath
          ? result.scriptPath
          : getScriptPath(root, ch.title);
      console.debug("[Regenerate] Wrote script to", {
        scriptPath,
        counts: {
          numLines: result.numLines,
          numConflicts: result.numConflicts,
        },
      });
      currentChapter.set({ ...ch, scriptPath });
      // Rescan chapters to refresh TOC and statuses
      try {
        const rootHandle = get(bookRootHandle);
        if (rootHandle) {
          const list = await scanChapters(rootHandle);
          chapters.set(list);
          const updated = list.find((c) => c.title === ch.title);
          if (updated) currentChapter.set(updated);
        }
      } catch {}
      // Trigger backend to recalculate character stats from all dialogue files
      try {
        await triggerStatsUpdate();
        // Reload central characters.json to get updated stats
        await forceRefreshBookCharacters();
      } catch (err) {
        console.warn("[Regenerate] Failed to update character stats:", err);
      }
      // Refresh characters list for this chapter (derived from bookCharacters)
      // The characters store will automatically update when bookCharacters changes
      // Conflict count will update automatically via currentScript
    } catch (e) {
      console.error("[Regenerate] Unexpected error", e);
      lastError.set(String(e));
    } finally {
      busy.set(false);
    }
  }

  async function pickFolder() {
    folderBusy.set(true);
    try {
      const dirHandle = await selectBookDirectory();
      if (!dirHandle) return;
      const previousTitle = get(currentChapter)?.title ?? null;
      chapters.set([]);
      currentChapter.set(null);
      currentScript.set(null);
      conflictCursor.set(null);
      lastError.set(null);
      bookRootHandle.set(dirHandle);
      bookRoot.set(dirHandle.name);
      try {
        const list = await scanChapters(dirHandle);
        chapters.set(list);
        if (previousTitle) {
          const next = list.find((c) => c.title === previousTitle) ?? null;
          if (next) currentChapter.set(next);
        }
      } catch (err) {
        console.error(
          "[Toolbar] Failed to reload chapters after folder change",
          err,
        );
      }
    } catch (err) {
      console.error("[Toolbar] Failed to pick folder", err);
    } finally {
      folderBusy.set(false);
    }
  }
</script>

<div class="toolbar">
  <div class="nav-controls">
    <div class="view-tabs">
      <button
        class="tab-btn"
        class:active={$page.route.id?.includes("chapter")}
        on:click={() => goto("/chapter")}>Chapter</button
      >
      <button
        class="tab-btn"
        class:active={$page.route.id?.includes("book")}
        on:click={() => goto("/book")}>Book</button
      >
      <button
        class="tab-btn"
        class:active={$page.route.id?.includes("settings")}
        on:click={() => goto("/settings")}>Settings</button
      >
    </div>
    <button
      class="folder-btn"
      on:click={pickFolder}
      disabled={$folderBusy}
      title="Change book folder"
      aria-label="Change book folder"
    >
      <FolderOpen size={18} class={$folderBusy ? "spinning" : ""} />
    </button>
  </div>
  {#if !$page.route.id?.includes("book")}
    <div class="conflict-controls">
      <button
        class="mode-btn"
        class:active={$toolMode === "review"}
        on:click={() => toolMode.set("review")}
        title="Review & Assign Characters"
        disabled={$folderBusy}
      >
        <FileText size={18} />
      </button>
      <button
        class="mode-btn"
        class:active={$toolMode === "join-split"}
        on:click={() => toolMode.set("join-split")}
        title="Join & Split Dialogue"
        disabled={$folderBusy}
      >
        <Merge size={18} />
      </button>
      <button
        class="mode-btn"
        class:active={$toolMode === "edit"}
        on:click={() => toolMode.set("edit")}
        title="Edit Text (BROKEN)"
        disabled={$folderBusy}
      >
        <PenSquare size={18} />
      </button>
      <button
        class="mode-btn"
        class:active={$toolMode === "audio"}
        on:click={() => toolMode.set("audio")}
        title="Audio Tools"
        disabled={$folderBusy}
      >
        <Music size={18} />
      </button>

      {#if $toolMode === "review"}
        <div class="divider"></div>
        <button
          class="reload-btn"
          on:click={regenerate}
          disabled={$busy || $folderBusy}
          title={$currentChapter?.parsed
            ? "Regenerate Dialogue"
            : "Generate Dialogue"}
          style="padding-left: 12px; padding-right: 12px;"
        >
          <RefreshCw size={16} class={$busy ? "spinning" : ""} />
          <span style="margin-left: 8px; font-weight: 500; font-size: 14px;">
            {$currentChapter?.parsed ? "Regenerate" : "Generate"}
          </span>
        </button>
      {/if}

      {#if $conflicts > 0}
        {#if $toolMode !== "review"}
          <div class="divider"></div>
          <button
            class="reload-btn"
            on:click={regenerate}
            disabled={$busy || $folderBusy}
            title="Reload and regenerate"
          >
            <RefreshCw size={18} class={$busy ? "spinning" : ""} />
          </button>
        {/if}
        <span
          class="conflict-text"
          style="margin-left: {$toolMode === 'review' ? '8px' : '0'}"
          >{$conflicts} conflicts remaining</span
        >
        <button
          class="icon-btn"
          on:click={prev}
          title="Previous conflict"
          disabled={$folderBusy}
        >
          <ChevronUp size={18} />
        </button>
        <button
          class="icon-btn"
          on:click={next}
          title="Next conflict"
          disabled={$folderBusy}
        >
          <ChevronDown size={18} />
        </button>
      {/if}
    </div>
  {/if}
  <div class="toolbar-slot"><slot /></div>
  <span class="cost-text"
    >Est. cost: ${(
      $conflicts *
      80 /* approx chars/line */ *
      $estimatedCostPerChar
    ).toFixed(2)}</span
  >
</div>

{#if $lastError}
  <div class="error-box">
    <strong>Parser error:</strong>
    <div class="error-text">{$lastError}</div>
  </div>
{/if}

<style>
  .toolbar {
    position: sticky;
    top: 0;
    z-index: 10;
    background: #ffffff;
    border-bottom: 1px solid #e0e0e0;
    padding: 12px 16px;
    display: flex;
    gap: 16px;
    align-items: center;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
  }

  .nav-controls {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .conflict-controls {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: 16px;
    align-items: center;
  }

  .folder-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
    border: none;
    background: transparent;
    border-radius: 6px;
    cursor: pointer;
    color: #5f6368;
    transition: all 0.2s ease;
  }

  .folder-btn:hover:not(:disabled) {
    background: #f1f3f4;
    color: #1a73e8;
  }

  .folder-btn:active:not(:disabled) {
    background: #e8eaed;
    transform: scale(0.95);
  }

  .folder-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .reload-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
    border: none;
    background: transparent;
    border-radius: 6px;
    cursor: pointer;
    color: #5f6368;
    transition: all 0.2s ease;
  }

  .reload-btn:hover:not(:disabled) {
    background: #f1f3f4;
    color: #1a73e8;
  }

  .reload-btn:active:not(:disabled) {
    background: #e8eaed;
    transform: scale(0.95);
  }

  .reload-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .icon-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 6px;
    border: 1px solid #dadce0;
    background: #ffffff;
    border-radius: 6px;
    cursor: pointer;
    color: #5f6368;
    transition: all 0.2s ease;
  }

  .icon-btn:hover {
    background: #f8f9fa;
    border-color: #1a73e8;
    color: #1a73e8;
  }

  .icon-btn:active {
    background: #e8eaed;
    transform: scale(0.95);
  }

  .icon-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .conflict-text {
    font-style: italic;
    color: #3c4043;
    font-size: 14px;
    font-weight: 500;
  }

  .cost-text {
    margin-left: auto;
    font-style: italic;
    color: #5f6368;
    font-size: 13px;
  }

  .error-box {
    background: #fef7f7;
    color: #b00020;
    border: 1px solid #f5c2c7;
    border-left: 4px solid #b00020;
    margin: 12px 16px;
    padding: 12px;
    border-radius: 6px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }

  .error-box strong {
    display: block;
    margin-bottom: 8px;
  }

  .error-text {
    white-space: pre-wrap;
  }

  .view-tabs {
    display: flex;
    gap: 4px;
    background: #e0e0e0;
    padding: 4px;
    border-radius: 8px;
  }

  .tab-btn {
    border: none;
    background: transparent;
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 500;
    color: #333;
    transition:
      background 0.2s ease,
      color 0.2s ease;
  }

  .tab-btn:hover {
    background: #f0f0f0;
  }

  .tab-btn.active {
    background: #ffffff;
    color: #000;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }

  .toolbar-slot {
    display: contents; /* Allows slot content to integrate into flex layout */
  }

  .mode-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
    border: none;
    background: transparent;
    border-radius: 6px;
    cursor: pointer;
    color: #5f6368;
    transition: all 0.2s ease;
  }

  .mode-btn:hover:not(:disabled) {
    background: #f1f3f4;
    color: #1a73e8;
  }

  .mode-btn:active:not(:disabled) {
    background: #e8eaed;
    transform: scale(0.95);
  }

  .mode-btn.active {
    background: #e8f0fe;
    color: #1a73e8;
  }

  .mode-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .divider {
    width: 1px;
    height: 24px;
    background: #dadce0;
    margin: 0 4px;
  }

  /* Spinning animation for reload icon */
  :global(.spinning) {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from {
      transform: rotate(0deg);
    }
    to {
      transform: rotate(360deg);
    }
  }
</style>
