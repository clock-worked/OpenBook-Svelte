<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { appTheme } from '$lib/stores/settings';
  import '$lib/theme/app.css';
  import { history } from '$lib/services/history';
  import { bookRootAbsolutePath, bookRootHandle, bookRoot, voiceSamplesRoot } from '$lib/stores/bookState';
  import { bookRootPathOverride, audiobookSettings, parserHints, defaultAudiobookSettings, defaultParserHints } from '$lib/stores/settings';
  import type { AudiobookSettings, ParserHints } from '$lib/types';
  import { readEditableJson } from '$lib/services/fs';
  import HistoryControls from '$lib/components/common/HistoryControls.svelte';
  import { page } from '$app/stores';
  import { forceRefreshBookCharacters } from '$lib/stores/bookCharacters';
  import { loadVoices } from '$lib/stores/speakers';
  import { clearRawCharacterFileDocs } from '$lib/services/characterFileMapping';
  import { get } from 'svelte/store';

  let unsubscribe: (() => void) | null = null;

  onMount(() => {
    history.activate();
    const updateScope = () => history.setScope(
      get(bookRootHandle) ?? get(bookRootAbsolutePath) ?? get(bookRootPathOverride) ?? get(bookRoot),
    );
    const scopeSubscriptions = [
      bookRootHandle.subscribe(updateScope),
      bookRootAbsolutePath.subscribe(updateScope),
      bookRootPathOverride.subscribe(updateScope),
      bookRoot.subscribe(updateScope),
    ];
    const unsubscribeRestore = history.onRestore(async (keys) => {
      if (get(bookRoot)) {
        if (keys.includes('voices.json')) await loadVoices();
        if (keys.some((key) => key.includes('/') || key === 'characters.json')) {
          clearRawCharacterFileDocs();
          await forceRefreshBookCharacters();
        }
        if (keys.includes('settings.json')) {
          const settings = await readEditableJson<{
            audiobookSettings?: AudiobookSettings;
            parserHints?: ParserHints;
            voiceSamplesRoot?: string | null;
          }>('settings.json');
          audiobookSettings.set({
            ...structuredClone(defaultAudiobookSettings), narrators: ['Narrator'], ...settings?.audiobookSettings,
          });
          parserHints.set({
            ...structuredClone(defaultParserHints), ...settings?.parserHints,
            heuristics: { ...defaultParserHints.heuristics, ...settings?.parserHints?.heuristics },
          });
          voiceSamplesRoot.set(settings?.voiceSamplesRoot ?? null);
        }
      }
    }, -1);
    unsubscribe = appTheme.subscribe((theme) => {
      document.documentElement.dataset.theme = theme;
    });

    return () => {
      unsubscribe?.();
      unsubscribe = null;
      scopeSubscriptions.forEach((stop) => stop());
      unsubscribeRestore();
    };
  });

  onDestroy(() => {
    unsubscribe?.();
  });

  function handleHistoryShortcut(event: KeyboardEvent) {
    if ($history.busy) {
      event.preventDefault();
      return;
    }
    if (event.defaultPrevented || event.altKey || !(event.ctrlKey || event.metaKey)) return;
    const target = event.target;
    if (target instanceof HTMLElement &&
      (target.closest('input, textarea, select') || target.isContentEditable)) return;
    const key = event.key.toLowerCase();
    const undo = key === 'z' && !event.shiftKey;
    const redo = (key === 'z' && event.shiftKey) || (key === 'y' && !event.shiftKey);
    if (!undo && !redo) return;
    event.preventDefault();
    if ($history.busy) return;
    void (undo ? history.undo() : history.redo());
  }
</script>

<svelte:window on:keydown|capture={handleHistoryShortcut} />
<slot />
{#if $page.route.id === '/'}
  <div class="home-history"><HistoryControls /></div>
{/if}
{#if $history.busy}
  <div class="history-busy" aria-hidden="true"></div>
{/if}
{#if $history.error}
  <div class="history-error" role="alert">{$history.error}</div>
{/if}

<style>
  .home-history { position: fixed; top: 16px; right: 16px; z-index: 100; }
  .history-busy { position: fixed; inset: 0; z-index: 10000; cursor: progress; }
  .history-error {
    position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
    z-index: 10001; max-width: min(700px, 90vw); padding: 12px 16px;
    background: var(--app-surface); color: var(--app-text); border: 1px solid var(--app-danger);
    border-radius: 8px; box-shadow: 0 4px 20px #0003;
  }
</style>
