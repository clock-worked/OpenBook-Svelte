<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { get } from 'svelte/store';
  import { bookRoot, chapters, audioRoot, bookRootAbsolutePath, voiceSamplesRoot } from '$lib/stores/bookState';
  import { scanChapters, getRootDirHandle, readSettings, writeSettings, readSpeakerBlocklist, resolveBookRootPath, setBackendAudioRoot, triggerStatsUpdate } from '$lib/services/fs';
  import { pickDirectoryAbsolutePath } from '$lib/services/directoryPicker';
  import { initRouteProjectContext } from '$lib/services/projectSession';
  import { getProjectInitFailurePolicy } from '$lib/services/routePolicy';
  import { audiobookSettings, parserHints } from '$lib/stores/settings';
  import {
    bookCharacters,
    removeBookCharacters,
    setBookCharacterColor,
    setBookCharacterProvider,
    setBookCharacterVoiceId,
    setBookCharacterVoiceMeta,
    refreshCharacterManifestStats,
    forceRefreshBookCharacters,
    syncAssignmentsFromVoicesJson,
  } from '$lib/stores/bookCharacters';
  import { rgbaToOpaqueHex, opaqueHexToRgba, colorForCharacter } from '$lib/stores/characters';
  import Toolbar from '$lib/components/chapter/Toolbar.svelte';
  import ColorPicker from '$lib/components/common/ColorPicker.svelte';
  import Dropdown from '$lib/components/common/Dropdown.svelte';
  import AudiobookSettings from '$lib/components/book/AudiobookSettings.svelte';
  import SpeakerManager from '$lib/components/book/SpeakerManager.svelte';
  import CharacterContextViewer from '$lib/components/book/CharacterContextViewer.svelte';
  import { ArrowUpDown, ChevronDown, ChevronRight, Loader2, Play, RefreshCw } from 'lucide-svelte';
  import type { Character, CharacterManifestStats } from '$lib/types';
  import { 
    voices, 
    saveVoicesData, 
    discoverVoices, 
    loadVoices,
    syncVoicesFromSamples,
    assignVoiceToCharacter,
    unassignVoiceFromCharacter,
    getCharacterAssignment,
    getAssignedVoice,
    deduplicateVoices
  } from '$lib/stores/speakers';

  let selectedSort: 'alpha' | 'lines' | 'chapter' = 'lines';
  let expanded: Record<string, boolean> = {};
  let chapterExpanded: Record<string, boolean> = {};
  let manifestLoading: Record<string, boolean> = {};
  let manifestError: Record<string, string | null> = {};
  let previewAudio: HTMLAudioElement | null = null;
  let refreshingCounts = false;
  let pruningZeroLineCharacters = false;

  async function loadSettings() {
    const root = get(bookRoot);
    if (!root) return;
    
    try {
      const settings = await readSettings(root);
      if (settings) {
        // Load audiobook settings
        if (settings.audiobookSettings) {
          audiobookSettings.set({
            narrators: settings.audiobookSettings.narrators || [],
            allowMultipleNarrators: settings.audiobookSettings.allowMultipleNarrators || false,
            narratorSameAsProtagonist: settings.audiobookSettings.narratorSameAsProtagonist ?? true,
            protagonist: settings.audiobookSettings.protagonist || null,
            mainCharacters: settings.audiobookSettings.mainCharacters || [],
            sideCharacters: settings.audiobookSettings.sideCharacters || [],
            defaultAccent: settings.audiobookSettings.defaultAccent || 'en-GB',
            speakingRate: settings.audiobookSettings.speakingRate || 1.0,
          });
        }
        
        // Load parser hints
        if (settings.parserHints) {
          const defaults = get(parserHints);
          const storedHints = settings.parserHints;
          const protagonistNames = (storedHints.protagonistNames && storedHints.protagonistNames.length > 0)
            ? storedHints.protagonistNames
            : (storedHints.protagonistName ? [storedHints.protagonistName] : (defaults.protagonistNames || []));
          parserHints.set({
            ...defaults,
            ...storedHints,
            protagonistNames,
            povMode: storedHints.povMode || defaults.povMode || 'first_person',
            learnVerbs: storedHints.learnVerbs ?? defaults.learnVerbs ?? false,
            heuristics: {
              ...(defaults.heuristics || {}),
              ...(storedHints.heuristics || {}),
            },
            manualBlockList: storedHints.manualBlockList || [],
          });
        }

        if (typeof settings.voiceSamplesRoot === 'string') {
          voiceSamplesRoot.set(settings.voiceSamplesRoot);
        }
      } else {
        // No settings.json exists, try to migrate from old speaker_blocklist.json
        console.log('No settings.json found, checking for legacy speaker_blocklist.json...');
        const legacyBlocklist = await readSpeakerBlocklist(root);
        
        if (legacyBlocklist?.blocked_speakers) {
          console.log('Migrating legacy blocklist:', legacyBlocklist.blocked_speakers);
          // Migrate old format to new format
          parserHints.update(hints => ({
            ...hints,
            manualBlockList: legacyBlocklist.blocked_speakers || [],
          }));
          
          // Auto-populate narrator with "Narrator"
          audiobookSettings.update(s => ({
            ...s,
            narrators: ['Narrator'],
          }));
          
          // Save the migrated settings
          await saveSettings();
          console.log('Migration complete, saved to settings.json');
        } else {
          // No settings at all, initialize with defaults
          console.log('No settings found, initializing with defaults');
          audiobookSettings.update(s => ({
            ...s,
            narrators: ['Narrator'],
          }));
        }
      }
    } catch (error) {
      console.error('Error loading settings:', error);
      // Auto-populate narrator with "Narrator" on error
      audiobookSettings.update(s => ({
        ...s,
        narrators: ['Narrator'],
      }));
    }
  }

  async function saveSettings() {
    const root = get(bookRoot);
    if (!root) return;
    
    const settings = {
      audiobookSettings: get(audiobookSettings),
      parserHints: get(parserHints),
      voiceSamplesRoot: get(voiceSamplesRoot),
    };
    
    try {
      await writeSettings(root, settings);
    } catch (error) {
      console.error('Error saving settings:', error);
    }
  }

  async function ensureAudioRoot() {
    const currentAudioRoot = get(audioRoot);
    const absoluteRoot = get(bookRootAbsolutePath);
    const handle = getRootDirHandle();
    const resolvedPath = handle ? await resolveBookRootPath(handle) : null;
    const nextAudioRoot = currentAudioRoot || absoluteRoot || resolvedPath;

    if (resolvedPath && resolvedPath !== absoluteRoot) {
      bookRootAbsolutePath.set(resolvedPath);
    }

    if (nextAudioRoot && nextAudioRoot !== currentAudioRoot) {
      audioRoot.set(nextAudioRoot);
      console.log('[book route] Audio root defaulted to book root:', nextAudioRoot);
    }

    if (nextAudioRoot) {
      const synced = await setBackendAudioRoot(nextAudioRoot);
      if (!synced) {
        console.warn('[book route] Failed to set audio root on backend');
      }
    }
  }

  onMount(async () => {
    const context = await initRouteProjectContext({
      allowDevModeWithoutHandle: false,
      requestPermission: true,
      touchLastAccessed: true,
    });

    if (!context.ok || !context.rootHandle) {
      if (!context.ok && 'reason' in context && context.reason === 'permission_denied') {
        console.error('Permission denied for stored project');
      } else if (!context.ok && 'reason' in context && context.reason === 'error') {
        console.error('Error loading stored project:', context.error);
      }
      const reason = (!context.ok && 'reason' in context) ? context.reason : 'error';
      const policy = getProjectInitFailurePolicy(reason);
      setTimeout(() => goto('/'), policy.delayMs);
      return;
    }

    const rootHandle = context.rootHandle;
    
    // Now scan chapters
    if (rootHandle) {
      try {
        chapters.set(await scanChapters(rootHandle));
      } catch {
        // ignore chapter loading errors
      }
    }

    // Load settings after setting up the book root
    await loadSettings();

    // Default audio root to book root if unset
    await ensureAudioRoot();
    
    // Auto-load voices.json and discover from manifests
    try {
      console.log('[book route] Auto-loading voices...');
      await loadVoices();

      const samplesRoot = get(voiceSamplesRoot);
      if (samplesRoot) {
        console.log('[book route] Auto-syncing sample-backed voices...');
        await syncVoicesFromSamples(samplesRoot);
      }
      
      console.log('[book route] Auto-discovering voices from manifests...');
      const discoveredCount = await discoverVoices();
      if (discoveredCount > 0) {
        console.log(`[book route] Discovered ${discoveredCount} new voices`);
      }
      
      console.log('[book route] Syncing voice assignments to characters...');
      await syncAssignmentsFromVoicesJson();
    } catch (err) {
      console.error('[book route] Error loading voices:', err);
    }
  });
  
  onDestroy(() => {
    previewAudio?.pause();
    previewAudio = null;
  });

  $: sortedCharacters = (() => {
    const items = $bookCharacters.characters.map((c, idx) => ({ character: c, idx }));
    if (selectedSort === 'alpha') items.sort((a, b) => a.character.name.localeCompare(b.character.name));
    if (selectedSort === 'lines') items.sort((a, b) => (b.character.count || 0) - (a.character.count || 0));
    return items;
  })();

  $: chapterGroups = (() => {
    if (selectedSort !== 'chapter') return [];
    
    // Group characters by firstAppearance
    const groups = new Map<string, typeof sortedCharacters>();
    
    for (const item of sortedCharacters) {
      const chapter = item.character.firstAppearance || 'Unknown';
      if (!groups.has(chapter)) {
        groups.set(chapter, []);
      }
      groups.get(chapter)!.push(item);
    }
    
    // Sort characters within each group by totalLines (descending)
    for (const items of groups.values()) {
      items.sort((a, b) => (b.character.count || 0) - (a.character.count || 0));
    }
    
    // Convert to array and sort by chapter order
    const result = Array.from(groups.entries()).map(([chapter, items]) => ({
      chapter,
      items,
      totalCharacters: items.length
    }));
    
    // Sort chapters naturally (00-Prologue, 01-Chapter-1, etc.)
    result.sort((a, b) => a.chapter.localeCompare(b.chapter, undefined, { numeric: true }));
    
    return result;
  })();

  function formatChapterName(chapterKey: string): string {
    // Convert "00-Prologue" to "Prologue"
    // Convert "01-Chapter-1-Knife" to "Chapter 1: Knife"
    const parts = chapterKey.split('-');
    
    if (parts[1] === 'Prologue' || parts[1] === 'Epilogue') {
      return parts[1];
    }
    
    // For chapters like "01-Chapter-1-Knife"
    if (parts[1] === 'Chapter' && parts[2]) {
      const chapterNum = parts[2];
      const title = parts.slice(3).join(' ');
      return title ? `Chapter ${chapterNum}: ${title}` : `Chapter ${chapterNum}`;
    }
    
    // Fallback
    return chapterKey;
  }

  function toggleChapterExpanded(chapter: string) {
    chapterExpanded = { ...chapterExpanded, [chapter]: !chapterExpanded[chapter] };
  }
  
  // Create a reactive color map that updates when bookCharacters changes
  // Falls back to hash-based default colors if no color is assigned
  $: colorMap = new Map(
    $bookCharacters.characters.map(c => [
      c.name,
      c.color ? rgbaToOpaqueHex(c.color, 1.0) : rgbaToOpaqueHex(colorForCharacter(c.name), 1.0)
    ])
  );

  // List of available character names for PillList components
  $: availableCharacters = $bookCharacters.characters.map(c => c.name);
  $: zeroLineCharacterNames = $bookCharacters.characters
    .filter((character) => getCharacterLineCount(character) === 0)
    .map((character) => character.name);

  function getCharacterLineCount(character: Character): number {
    return Math.max(0, character.stats?.totalLines ?? character.count ?? 0);
  }

  function toggleExpanded(name: string) {
    const next = !expanded[name];
    expanded = { ...expanded, [name]: next };
    if (next) {
      const char = $bookCharacters.characters.find((c) => c.name === name);
      if (char && !char.manifestStats && !manifestLoading[name]) {
        loadManifest(name, char.count, false);
      }
    }
  }

  function formatCoverage(stats?: CharacterManifestStats | null): string {
    if (!stats) return 'Not loaded';
    if (stats.totalLines && stats.totalLines > 0 && stats.coverageRatio !== undefined) {
      const pct = Math.round(stats.coverageRatio * 1000) / 10; // one decimal place
      return `${pct}% (${stats.clipCount}/${stats.totalLines})`;
    }
    return `${stats.clipCount} clips`;
  }

  function voiceCountSummary(stats?: CharacterManifestStats | null): Array<{ voiceId: string; count: number }> {
    if (!stats?.voiceIds) return [];
    return Object.entries(stats.voiceIds)
      .map(([voiceId, count]) => ({ voiceId, count }))
      .sort((a, b) => b.count - a.count);
  }

  async function loadManifest(name: string, totalLines: number | undefined, force = false) {
    manifestLoading = { ...manifestLoading, [name]: true };
    manifestError = { ...manifestError, [name]: null };
    try {
      const stats = await refreshCharacterManifestStats(name, { totalLines, force });
      if (!stats) {
        manifestError = { ...manifestError, [name]: 'Manifest not found or empty.' };
        return;
      }
      
      // Auto-assign voice if manifest has a primaryVoiceId that matches a voice in the store
      if (stats.primaryVoiceId) {
        await autoAssignVoiceFromManifest(name, stats.primaryVoiceId);
      }
    } catch (err) {
      manifestError = {
        ...manifestError,
        [name]: err instanceof Error ? err.message : 'Failed to load manifest.',
      };
    } finally {
      manifestLoading = { ...manifestLoading, [name]: false };
    }
  }

  async function autoAssignVoiceFromManifest(characterName: string, manifestVoiceId: string, force = false) {
    const char = $bookCharacters.characters.find(c => c.name === characterName);
    if (!char) return;
    
    // Only auto-assign if character doesn't already have a voice assigned (unless force is true)
    if (force || !char.provider || !char.voiceId) {
      // Look for a voice that matches the manifest voice ID
      // The manifest voice ID could be just the provider voice ID (e.g., "kNS2rxxquHK0xi0lmF1f")
      const matchingVoice = $voices.voices.find(v => 
        v.providerVoiceId === manifestVoiceId || 
        v.id.endsWith(`-${manifestVoiceId}`)
      );
      
      if (matchingVoice) {
        console.log(`Auto-assigning voice "${matchingVoice.displayName}" to character "${characterName}"`);
        await assignVoice(characterName, matchingVoice.id);
      }
    }
  }

  async function previewVoice(name: string) {
    const char = $bookCharacters.characters.find((c) => c.name === name);
    const url = char?.voiceMeta?.previewUrl;
    if (!url) {
      console.warn('No preview available for', name);
      return;
    }
    try {
      previewAudio?.pause();
      previewAudio = new Audio(url);
      await previewAudio.play();
    } catch (err) {
      console.error('Failed to play preview:', err);
    }
  }

  async function assignVoice(characterName: string, voiceId: string) {
    // Find character by name to get its ID
    const character = $bookCharacters.characters.find(c => c.name === characterName);
    if (!character || !character.id) {
      console.error(`Character not found or missing ID for ${characterName}`);
      return;
    }
    const characterId = character.id;
    
    if (!voiceId) {
      // Clear voice assignment from voices.json
      await unassignVoiceFromCharacter(characterId);
      
      // Also clear from character data for backward compatibility
      await setBookCharacterProvider(characterName, null);
      await setBookCharacterVoiceId(characterName, '');
      await setBookCharacterVoiceMeta(characterName, null);
      return;
    }
    
    const voice = $voices.voices.find(v => v.id === voiceId);
    if (!voice) return;
    
    try {
      // Create assignment in voices.json (authoritative source)
      await assignVoiceToCharacter(characterId, voiceId);
      
      // Also update character data for backward compatibility
      await setBookCharacterProvider(characterName, voice.provider);
      await setBookCharacterVoiceId(characterName, voice.providerVoiceId);
      
      // Set voice metadata
      await setBookCharacterVoiceMeta(characterName, {
        name: voice.displayName,
        previewUrl: voice.previewUrl || null,
        provider: voice.provider,
        fetchedAt: new Date().toISOString()
      });
      
      console.log(`[book route] Assigned ${voice.displayName} to ${characterName}`);
    } catch (err) {
      console.error('Failed to assign voice:', err);
    }
  }

  async function refreshCharacterCounts() {
    refreshingCounts = true;
    try {
      await triggerStatsUpdate();
      
      // Then reload from disk (picks up the backend's changes)
      await forceRefreshBookCharacters();
      console.log('Character data reloaded from disk');
    } catch (error) {
      console.error('Error refreshing character counts:', error);
    } finally {
      refreshingCounts = false;
    }
  }

  async function removeZeroLineCharacters() {
    if (zeroLineCharacterNames.length === 0 || pruningZeroLineCharacters) return;

    const count = zeroLineCharacterNames.length;
    const confirmed = confirm(
      `Remove ${count} character${count === 1 ? '' : 's'} with 0 assigned lines from the book?`,
    );
    if (!confirmed) return;

    pruningZeroLineCharacters = true;
    try {
      const namesToRemove = new Set(zeroLineCharacterNames);
      const currentSettings = get(audiobookSettings);

      audiobookSettings.set({
        ...currentSettings,
        narrators: (currentSettings.narrators || []).filter((name) => !namesToRemove.has(name)),
        protagonist: currentSettings.protagonist && namesToRemove.has(currentSettings.protagonist)
          ? null
          : currentSettings.protagonist,
        mainCharacters: (currentSettings.mainCharacters || []).filter((name) => !namesToRemove.has(name)),
        sideCharacters: (currentSettings.sideCharacters || []).filter((name) => !namesToRemove.has(name)),
      });

      await removeBookCharacters(zeroLineCharacterNames);

      expanded = Object.fromEntries(
        Object.entries(expanded).filter(([name]) => !namesToRemove.has(name)),
      );
      manifestLoading = Object.fromEntries(
        Object.entries(manifestLoading).filter(([name]) => !namesToRemove.has(name)),
      );
      manifestError = Object.fromEntries(
        Object.entries(manifestError).filter(([name]) => !namesToRemove.has(name)),
      );

      await saveSettings();
    } catch (error) {
      console.error('Error removing zero-line characters:', error);
    } finally {
      pruningZeroLineCharacters = false;
    }
  }

  async function handleVoicesUpdate(event: CustomEvent<typeof $voices.voices>) {
    // Update voices in the store
    voices.update(v => ({
      ...v,
      voices: event.detail
    }));
    await saveVoicesData();
  }

  async function handleDeduplicateVoices() {
    if (!confirm('This will remove duplicate voices with the same provider voice ID and update all assignments. Continue?')) {
      return;
    }
    
    try {
      const result = await deduplicateVoices();
      alert(`Deduplication complete!\n\nRemoved ${result.removed} duplicate voice(s).\nUpdated ${result.updated} assignment(s).`);
      
      // Reload voices to reflect changes in UI
      await loadVoices();
    } catch (error) {
      console.error('Error deduplicating voices:', error);
      alert('Error deduplicating voices. See console for details.');
    }
  }

  async function handleDiscoverVoices(): Promise<number> {
    const count = await discoverVoices();
    
    // After discovering voices, try to auto-assign them to characters with manifest data
    if (count > 0) {
      await autoAssignAllVoices();
    }
    
    return count;
  }

  async function autoAssignAllVoices() {
    const characters = $bookCharacters.characters;
    
    for (const char of characters) {
      // Skip if already assigned
      if (char.provider && char.voiceId) continue;
      
      // Check manifest stats for primary voice ID
      if (char.manifestStats?.primaryVoiceId) {
        await autoAssignVoiceFromManifest(char.name, char.manifestStats.primaryVoiceId);
      }
    }
  }

  function getAssignedVoiceId(character: typeof $bookCharacters.characters[0]): string {
    // Use character ID directly - no need to map from name
    const characterId = character.id;
    
    if (characterId) {
      // Check voices.json assignments (authoritative source)
      const assignment = $voices.assignments.find(a => a.characterId === characterId);
      if (assignment) {
        return assignment.voiceId;
      }
    }
    
    // Fallback to legacy character fields for backward compatibility
    if (!character.provider || !character.voiceId) return '';
    const voice = $voices.voices.find(
      v => v.provider === character.provider && v.providerVoiceId === character.voiceId
    );
    return voice?.id || '';
  }

  function getGenderColor(gender: string | undefined): string {
    if (!gender) return '#8b5cf6'; // purple for unknown
    const g = gender.toLowerCase();
    if (g === 'male' || g === 'm') return '#3b82f6'; // blue
    if (g === 'female' || g === 'f') return '#ec4899'; // pink
    return '#8b5cf6'; // purple for unknown
  }

  function getGenderShort(gender: string | undefined): string {
    if (!gender) return 'U';
    const g = gender.toLowerCase();
    if (g === 'male' || g === 'm') return 'M';
    if (g === 'female' || g === 'f') return 'F';
    return 'U';
  }

  async function handlePickVoiceSamplesFolder() {
    try {
      const absolutePath = await pickDirectoryAbsolutePath('read');
      if (!absolutePath) {
        alert('Could not read absolute path. Enter the path manually.');
        return;
      }
      voiceSamplesRoot.set(absolutePath);
      await syncVoicesFromSamples(absolutePath);
      await syncAssignmentsFromVoicesJson();
    } catch (err) {
      if (err instanceof Error && err.message.includes('not supported')) {
        alert('Folder picker is not supported in this browser. Enter the path manually.');
        return;
      }
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      console.error('Failed to select samples folder:', err);
    }
  }

  async function handleVoiceSamplesPathChange(path: string | null) {
    voiceSamplesRoot.set(path);
    if (path) {
      try {
        const result = await syncVoicesFromSamples(path);
        if (result.imported === 0) {
          console.warn('[book route] No compatible voice samples found in selected folder');
        }
      } finally {
        await syncAssignmentsFromVoicesJson();
      }
    }
    await saveSettings();
  }

  async function handleVoiceSamplesRootPersist() {
    await saveSettings();
  }
</script>

<div class="book-page">
  <Toolbar>
    <div class="sort-controls">
      <Dropdown
        items={[
          { value: 'alpha', label: 'Alphabetical' },
          { value: 'lines', label: 'Line count' },
          { value: 'chapter', label: 'Chapter' }
        ]}
        selected={selectedSort}
        on:select={(e) => selectedSort = e.detail.value as typeof selectedSort}
        title="Change sort"
      >
        <ArrowUpDown size={16} />
        <span>Sort</span>
      </Dropdown>
      <button class="toolbar-btn" on:click={refreshCharacterCounts} disabled={refreshingCounts} title="Refresh character line counts">
        {#if refreshingCounts}
          <Loader2 size={16} class="spin" />
          <span>Refreshing…</span>
        {:else}
          <RefreshCw size={16} />
          <span>Refresh</span>
        {/if}
      </button>
      <button
        class="toolbar-btn"
        on:click={removeZeroLineCharacters}
        disabled={pruningZeroLineCharacters || zeroLineCharacterNames.length === 0}
        title="Remove characters with 0 assigned lines"
      >
        {#if pruningZeroLineCharacters}
          <Loader2 size={16} class="spin" />
          <span>Removing…</span>
        {:else}
          <span>Remove Empty{zeroLineCharacterNames.length > 0 ? ` (${zeroLineCharacterNames.length})` : ''}</span>
        {/if}
      </button>
    </div>
    <div class="audio-root-controls">
      <span class="toolbar-label">Audio:</span>
      <input 
        type="text" 
        class="audio-path-input" 
        value={$audioRoot || ''} 
        on:change={(e) => audioRoot.set((e.target as HTMLInputElement).value)}
        placeholder="Audio directory path"
        title="Path to audio files directory"
      />
    </div>
  </Toolbar>

  <div class="book-content">
    <div class="main-panel">
      <div class="characters-and-voices-panel">
        <div class="characters-section">
          <h3 class="section-title">Characters</h3>
      <div class="characters-list">
        {#if selectedSort === 'chapter'}
          {#each chapterGroups as group (group.chapter)}
            <div class="chapter-group">
              <button class="chapter-header" on:click={() => toggleChapterExpanded(group.chapter)}>
                <div class="chapter-caret">
                  {#if chapterExpanded[group.chapter]}
                    <ChevronDown size={18} />
                  {:else}
                    <ChevronRight size={18} />
                  {/if}
                </div>
                <div class="chapter-info">
                  <span class="chapter-name">{formatChapterName(group.chapter)}</span>
                  <span class="chapter-count">{group.totalCharacters} character{group.totalCharacters === 1 ? '' : 's'}</span>
                </div>
              </button>
              {#if chapterExpanded[group.chapter]}
                <div class="chapter-characters">
                  {#each group.items as it (it.character.name)}
                    <div class="character-card">
                      <div class="character-header">
                        <button class="caret-btn" on:click={() => toggleExpanded(it.character.name)} aria-label={`Toggle ${it.character.name}`}>
                          {#if expanded[it.character.name]}
                            <ChevronDown size={16} />
                          {:else}
                            <ChevronRight size={16} />
                          {/if}
                        </button>
                        <div class="color-picker-wrapper">
                          <Dropdown showCaret={false} align="left" minWidth={180} title="Change color">
                            <span class="line-count-badge" style={`background:${colorMap.get(it.character.name) || '#ccc'}`}>
                              {it.character.count || 0}
                            </span>
                            <div slot="panel" let:close>
                              <ColorPicker
                                value={colorMap.get(it.character.name) || null}
                                alpha={1.0}
                                on:select={async (e) => {
                                  const original = e.detail.color ? opaqueHexToRgba(e.detail.color, 1.0) : null;
                                  await setBookCharacterColor(it.character.name, original);
                                  close();
                                }}
                              />
                            </div>
                          </Dropdown>
                        </div>
                        <div class="header-meta">
                          <div class="character-name-with-badge">
                            <span class="character-name">{it.character.name}</span>
                            {#if it.character.gender}
                              <div class="gender-badge-small" style="background-color: {getGenderColor(it.character.gender)}">
                                {getGenderShort(it.character.gender)}
                              </div>
                            {/if}
                          </div>
                          <span class="character-stats">
                            {it.character.count || 0} lines
                            {#if it.character.chapterCount}
                              · Appears in {it.character.chapterCount} chapter{it.character.chapterCount === 1 ? '' : 's'}
                            {/if}
                          </span>
                        </div>
                        <div class="inline-voice-controls">
                          <select
                            class="inline-voice-select"
                            value={getAssignedVoiceId(it.character)}
                            on:change={(e) => assignVoice(it.character.name, (e.target as HTMLSelectElement).value)}
                            title="Select voice"
                          >
                            <option value="">-- Select voice --</option>
                            {#each $voices.voices as voice}
                              <option value={voice.id}>
                                {voice.displayName} ({voice.provider})
                              </option>
                            {/each}
                          </select>
                        </div>
                      </div>
                      {#if expanded[it.character.name]}
                        <div class="character-body">
                          <div class="character-section">
                            <div class="section-header">
                              <span>Character Information</span>
                            </div>
                            <div class="section-content">
                              {#if it.character.gender}
                                <div class="info-row">
                                  <span class="info-label">Gender:</span>
                                  <div class="gender-badge-inline" style="background-color: {getGenderColor(it.character.gender)}">
                                    {it.character.gender}
                                  </div>
                                </div>
                              {/if}
                              
                              {#if it.character.race}
                                <div class="info-row">
                                  <span class="info-label">Race:</span>
                                  <span class="info-value">{it.character.race}</span>
                                </div>
                              {/if}
                              
                              {#if it.character.aliases && it.character.aliases.length > 0}
                                <div class="info-row">
                                  <span class="info-label">Aliases:</span>
                                  <span class="info-value">{it.character.aliases.join(', ')}</span>
                                </div>
                              {/if}
                              
                              <CharacterContextViewer
                                characterName={it.character.name}
                                characterId={it.character.id || null}
                                firstAppearanceChapter={it.character.firstAppearance}
                              />
                            </div>
                          </div>
                          <div class="character-section">
                            <div class="section-header">
                              <span>Voice Assignment</span>
                            </div>
                            <div class="section-content">
                              <label class="field-label" for={`voice-${it.character.name}`}>Select Voice</label>
                              <select
                                id={`voice-${it.character.name}`}
                                class="provider-select"
                                value={getAssignedVoiceId(it.character)}
                                on:change={(e) => assignVoice(it.character.name, (e.target as HTMLSelectElement).value)}
                              >
                                <option value="">-- Select voice --</option>
                                {#each $voices.voices as voice}
                                  <option value={voice.id}>
                                    {voice.displayName} ({voice.provider})
                                  </option>
                                {/each}
                              </select>
                              {#if it.character.voiceMeta?.name}
                                <div class="voice-meta">
                                  <span class="meta-name">{it.character.voiceMeta.name}</span>
                                  {#if it.character.voiceMeta.previewUrl}
                                    <button class="ghost-btn" on:click={() => previewVoice(it.character.name)}>
                                      <Play size={14} />
                                      <span>Preview</span>
                                    </button>
                                  {/if}
                                  {#if it.character.voiceMeta.fetchedAt}
                                    <span class="meta-updated">Updated {new Date(it.character.voiceMeta.fetchedAt).toLocaleString()}</span>
                                  {/if}
                                </div>
                              {/if}
                            </div>
                          </div>
                        </div>
                      {/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        {:else}
          {#each sortedCharacters as it (it.character.name)}
            <div class="character-card">
              <div class="character-header">
                <button class="caret-btn" on:click={() => toggleExpanded(it.character.name)} aria-label={`Toggle ${it.character.name}`}>
                  {#if expanded[it.character.name]}
                    <ChevronDown size={16} />
                  {:else}
                    <ChevronRight size={16} />
                  {/if}
                </button>
                <div class="color-picker-wrapper">
                  <Dropdown showCaret={false} align="left" minWidth={180} title="Change color">
                    <span class="line-count-badge" style={`background:${colorMap.get(it.character.name) || '#ccc'}`}>
                      {it.character.count || 0}
                    </span>
                    <div slot="panel" let:close>
                      <ColorPicker
                        value={colorMap.get(it.character.name) || null}
                        alpha={1.0}
                        on:select={async (e) => {
                          const original = e.detail.color ? opaqueHexToRgba(e.detail.color, 1.0) : null;
                          await setBookCharacterColor(it.character.name, original);
                          close();
                        }}
                      />
                    </div>
                  </Dropdown>
                </div>
                <div class="header-meta">
                  <div class="character-name-with-badge">
                    <span class="character-name">{it.character.name}</span>
                    {#if it.character.gender}
                      <div class="gender-badge-small" style="background-color: {getGenderColor(it.character.gender)}">
                        {getGenderShort(it.character.gender)}
                      </div>
                    {/if}
                  </div>
                  <span class="character-stats">
                    {it.character.count || 0} lines
                    {#if it.character.chapterCount}
                      · Appears in {it.character.chapterCount} chapter{it.character.chapterCount === 1 ? '' : 's'}
                    {/if}
                  </span>
                </div>
                <div class="inline-voice-controls">
                  <select
                    class="inline-voice-select"
                    value={getAssignedVoiceId(it.character)}
                    on:change={(e) => assignVoice(it.character.name, (e.target as HTMLSelectElement).value)}
                    title="Select voice"
                  >
                    <option value="">-- Select voice --</option>
                    {#each $voices.voices as voice}
                      <option value={voice.id}>
                        {voice.displayName} ({voice.provider})
                      </option>
                    {/each}
                  </select>
                </div>
              </div>
              {#if expanded[it.character.name]}
                <div class="character-body">
                  <div class="character-section">
                    <div class="section-header">
                      <span>Character Information</span>
                    </div>
                    <div class="section-content">
                      {#if it.character.gender}
                        <div class="info-row">
                          <span class="info-label">Gender:</span>
                          <div class="gender-badge-inline" style="background-color: {getGenderColor(it.character.gender)}">
                            {it.character.gender}
                          </div>
                        </div>
                      {/if}
                      
                      {#if it.character.race}
                        <div class="info-row">
                          <span class="info-label">Race:</span>
                          <span class="info-value">{it.character.race}</span>
                        </div>
                      {/if}
                      
                      {#if it.character.aliases && it.character.aliases.length > 0}
                        <div class="info-row">
                          <span class="info-label">Aliases:</span>
                          <span class="info-value">{it.character.aliases.join(', ')}</span>
                        </div>
                      {/if}
                      
                      <CharacterContextViewer
                        characterName={it.character.name}
                        characterId={it.character.id || null}
                        firstAppearanceChapter={it.character.firstAppearance}
                      />
                    </div>
                  </div>
                  <div class="character-section">
                    <div class="section-header">
                      <span>Voice Assignment</span>
                    </div>
                    <div class="section-content">
                      <label class="field-label" for={`voice-${it.character.name}`}>Select Voice</label>
                      <select
                        id={`voice-${it.character.name}`}
                        class="provider-select"
                        value={getAssignedVoiceId(it.character)}
                        on:change={(e) => assignVoice(it.character.name, (e.target as HTMLSelectElement).value)}
                      >
                        <option value="">-- Select voice --</option>
                        {#each $voices.voices as voice}
                          <option value={voice.id}>
                            {voice.displayName} ({voice.provider})
                          </option>
                        {/each}
                      </select>
                      {#if it.character.voiceMeta?.name}
                        <div class="voice-meta">
                          <span class="meta-name">{it.character.voiceMeta.name}</span>
                          {#if it.character.voiceMeta.previewUrl}
                            <button class="ghost-btn" on:click={() => previewVoice(it.character.name)}>
                              <Play size={14} />
                              <span>Preview</span>
                            </button>
                          {/if}
                          {#if it.character.voiceMeta.fetchedAt}
                            <span class="meta-updated">Updated {new Date(it.character.voiceMeta.fetchedAt).toLocaleString()}</span>
                          {/if}
                        </div>
                      {/if}
                    </div>
                  </div>
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
        </div>

        <div class="speakers-section">
          <SpeakerManager 
            voices={$voices.voices}
            assignments={$voices.assignments}
            characters={$bookCharacters.characters}
            voiceSamplesPath={$voiceSamplesRoot || ''}
            on:samplesrootchange={(event) => {
              void handleVoiceSamplesPathChange(event.detail || null);
            }}
            on:update={handleVoicesUpdate}
            on:deduplicate={handleDeduplicateVoices}
          />
        </div>
      </div>
    </div>

    <div class="sidebar-panel">
      <AudiobookSettings
      audiobookSettings={$audiobookSettings}
      parserHints={$parserHints}
      {availableCharacters}
      getCharacterColor={(name) => {
        const char = $bookCharacters.characters.find(c => c.name === name);
        return char?.color || null;
      }}
      voiceSamplesPath={$voiceSamplesRoot || ''}
      onVoiceSamplesPathChange={(path) => {
        void handleVoiceSamplesPathChange(path || null);
      }}
      onPickVoiceSamplesFolder={async () => {
        await handlePickVoiceSamplesFolder();
        await saveSettings();
      }}
      on:update={saveSettings}
    />
    </div>
  </div>
</div>

<style>
  .book-page {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  .book-content {
    display: flex;
    flex: 1;
    overflow: auto;
    padding: 12px;
    gap: 16px;
  }

  .main-panel {
    flex: 3;
    overflow-y: auto;
  }

  .characters-and-voices-panel {
    display: flex;
    gap: 16px;
    height: 100%;
  }

  .sidebar-panel {
    flex: 1;
    min-width: 350px;
    max-width: 450px;
    overflow-y: auto;
  }

  .characters-section {
    flex: 0.8;
    min-width: 300px;
    overflow-y: auto;
  }

  .speakers-section {
    flex: 1.2;
    min-width: 500px;
    overflow-y: auto;
  }

  .section-title {
    margin-top: 0;
  }

  .characters-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .chapter-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .chapter-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    background:  #ffffff;
    border: 1px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
    width: 100%;
    text-align: left;
  }

  .chapter-header:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  }

  .chapter-caret {
    display: flex;
    align-items: center;
    justify-content: center;
    color: #000000;
    flex-shrink: 0;
  }

  .chapter-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
  }

  .chapter-name {
    font-size: 15px;
    font-weight: 600;
    color: #000000;
    letter-spacing: 0.01em;
  }

  .chapter-count {
    font-size: 12px;
    color: rgba(0, 0, 0, 0.85);
  }

  .chapter-characters {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-left: 16px;
  }

  .character-card {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background: #fff;
    box-shadow: 0 2px 4px rgba(15, 23, 42, 0.04);
    overflow: hidden;
  }

  .character-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid #f3f4f6;
  }

  .caret-btn {
    width: 24px;
    height: 24px;
    border: none;
    background: transparent;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: #6b7280;
  }

  .line-count-badge {
    font-size: 12px;
    font-weight: 600;
    color: #222;
    padding: 3px 8px;
    border-radius: 8px;
    white-space: nowrap;
    flex-shrink: 0;
    cursor: pointer;
    border: none;
    text-align: center;
    display: inline-block;
    transition: opacity 0.15s ease;
  }

  .color-picker-wrapper :global(.dd-button) {
    padding: 0;
    border: none;
    background: transparent;
    border-radius: 0;
  }

  .color-picker-wrapper :global(.dd-button:hover) {
    background: transparent;
  }

  .color-picker-wrapper :global(.dd-button:active) {
    background: transparent;
    box-shadow: none;
  }

  .line-count-badge:hover {
    opacity: 0.85;
  }

  .header-meta {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .character-name {
    font-weight: 600;
    font-size: 15px;
    color: #1f2937;
  }

  .character-stats {
    font-size: 12px;
    color: #6b7280;
  }

  .character-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    background: #f9fafb;
  }

  .character-section {
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-weight: 600;
    color: #1f2937;
  }

  .section-content {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .ghost-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 6px;
    border: none;
    cursor: pointer;
    font-size: 13px;
    padding: 6px 10px;
    transition: background-color 0.15s ease;
    background: transparent;
    color: #2563eb;
  }

  .ghost-btn:hover {
    background: rgba(37, 99, 235, 0.08);
  }

  .ghost-btn:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .field-label {
    font-size: 12px;
    font-weight: 600;
    color: #4b5563;
  }

  .provider-select {
    width: 100%;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid #d1d5db;
    font-size: 13px;
    color: #111827;
    background: #fff;
  }

  .provider-select:focus {
    outline: none;
    border-color: #2563eb;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
  }

  .voice-meta {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 12px;
    color: #374151;
  }

  .meta-name {
    font-weight: 600;
  }

  .meta-updated {
    color: #6b7280;
  }

  :global(.spin) {
    animation: spin 1s linear infinite;
  }

  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }

  .sort-controls {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .toolbar-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 6px;
    border: 1px solid #e5e7eb;
    background: #fff;
    cursor: pointer;
    font-size: 14px;
    color: #1f2937;
    transition: background-color 0.15s ease;
  }

  .toolbar-btn:hover:not(:disabled) {
    background: #f9fafb;
  }

  .toolbar-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .audio-root-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }

  .toolbar-label {
    font-size: 13px;
    color: #6b7280;
    font-weight: 500;
  }

  .audio-path-input {
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid #d1d5db;
    font-size: 12px;
    color: #111827;
    background: #fff;
    font-family: 'Courier New', monospace;
    min-width: 350px;
  }

  .audio-path-input:focus {
    outline: none;
    border-color: #2563eb;
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
  }

  .audio-path-input::placeholder {
    color: #9ca3af;
    font-style: italic;
  }

  .inline-voice-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    min-width: 250px;
  }

  .inline-voice-select {
    flex: 1;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid #d1d5db;
    font-size: 12px;
    color: #111827;
    background: #fff;
    cursor: pointer;
    min-width: 200px;
  }

  .inline-voice-select:focus {
    outline: none;
    border-color: #2563eb;
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
  }

  .character-name-with-badge {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .gender-badge-small {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    color: white;
    font-size: 11px;
    font-weight: 700;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    flex-shrink: 0;
  }

  .gender-badge-inline {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 3px 10px;
    border-radius: 12px;
    color: white;
    font-size: 12px;
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
  }

  .info-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
  }

  .info-label {
    font-size: 13px;
    font-weight: 600;
    color: #4b5563;
    min-width: 80px;
  }

  .info-value {
    font-size: 13px;
    color: #111827;
  }

</style>


