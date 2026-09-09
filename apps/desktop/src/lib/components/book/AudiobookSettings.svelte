<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import PillList from '$lib/components/common/PillList.svelte';
  import type { AudiobookSettings, ParserHints } from '$lib/types';
  
  export let audiobookSettings: AudiobookSettings;
  export let parserHints: ParserHints;
  export let availableCharacters: string[] = [];
  export let getCharacterColor: (name: string) => string | null = () => null;
  export let voiceSamplesPath: string = '';
  export let onVoiceSamplesPathChange: (path: string) => void = () => {};
  export let onPickVoiceSamplesFolder: () => void = () => {};
    const defaultHeuristics = {
      protagonistFirstPersonTag: true,
      narratorIdentity: true,
      coreference: true,
      explicitTags: true,
      tagContinuation: true,
      contiguousDialogue: true,
      carryAcrossShortNarration: true,
      suggestAlternatives: true,
      firstPersonOverride: true,
      narratorFallback: true,
      vocativeGuard: true,
    };

    $: if (!parserHints.heuristics) {
      parserHints.heuristics = { ...defaultHeuristics };
    }

    $: if (!parserHints.povMode) {
      parserHints.povMode = 'first_person';
    }

    let protagonistNamesText = '';
    $: protagonistNamesText = (parserHints.protagonistNames && parserHints.protagonistNames.length > 0)
      ? parserHints.protagonistNames.join(', ')
      : (parserHints.protagonistName || '');

    function handleProtagonistNamesChange() {
      const names = protagonistNamesText
        .split(',')
        .map(n => n.trim())
        .filter(n => n.length > 0);
      parserHints.protagonistNames = names;
      parserHints.protagonistName = names[0] || '';
      notifyUpdate();
    }
  
  const dispatch = createEventDispatcher<{
    update: void;
  }>();
  
  function notifyUpdate() {
    dispatch('update');
  }
  
  function handleNarratorAdd(event: CustomEvent<string>) {
    if (audiobookSettings.allowMultipleNarrators) {
      audiobookSettings.narrators = [...(audiobookSettings.narrators || []), event.detail];
    } else {
      audiobookSettings.narrators = [event.detail];
    }
    notifyUpdate();
  }
  
  function handleNarratorRemove(event: CustomEvent<string>) {
    audiobookSettings.narrators = (audiobookSettings.narrators || []).filter(n => n !== event.detail);
    notifyUpdate();
  }
  
  function handleProtagonistAdd(event: CustomEvent<string>) {
    audiobookSettings.protagonist = event.detail;
    notifyUpdate();
  }
  
  function handleProtagonistRemove() {
    audiobookSettings.protagonist = null;
    notifyUpdate();
  }
  
  function handleMainCharacterAdd(event: CustomEvent<string>) {
    audiobookSettings.mainCharacters = [...(audiobookSettings.mainCharacters || []), event.detail];
    notifyUpdate();
  }
  
  function handleMainCharacterRemove(event: CustomEvent<string>) {
    audiobookSettings.mainCharacters = (audiobookSettings.mainCharacters || []).filter(c => c !== event.detail);
    notifyUpdate();
  }
  
  function handleSideCharacterAdd(event: CustomEvent<string>) {
    audiobookSettings.sideCharacters = [...(audiobookSettings.sideCharacters || []), event.detail];
    notifyUpdate();
  }
  
  function handleSideCharacterRemove(event: CustomEvent<string>) {
    audiobookSettings.sideCharacters = (audiobookSettings.sideCharacters || []).filter(c => c !== event.detail);
    notifyUpdate();
  }
  
  function handleBlocklistAdd(event: CustomEvent<string>) {
    const name = event.detail.trim();
    if (name && !(parserHints.manualBlockList || []).includes(name)) {
      parserHints.manualBlockList = [...(parserHints.manualBlockList || []), name];
      notifyUpdate();
    }
  }
  
  function handleBlocklistRemove(event: CustomEvent<string>) {
    parserHints.manualBlockList = (parserHints.manualBlockList || []).filter(n => n !== event.detail);
    notifyUpdate();
  }
</script>

<style>
  .settings-section {
    background: var(--app-surface-raised);
    border: 1px solid var(--app-border);
    border-radius: 12px;
    padding: 24px;
    box-shadow: var(--app-shadow-sm);
  }
  
  .section-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--app-text);
    margin: 0 0 20px 0;
    padding-bottom: 12px;
    border-bottom: 2px solid var(--app-border);
  }
  
  .subsection-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--app-text);
    margin: 24px 0 16px 0;
  }
  
  .subsection-title:first-of-type {
    margin-top: 0;
  }
  
  .settings-group {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  
  .form-column {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  
  .form-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  
  .field-label {
    font-size: 14px;
    font-weight: 500;
    color: var(--app-text);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  .checkbox-label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    color: var(--app-text-muted);
    cursor: pointer;
    user-select: none;
    padding: 8px 0;
  }
  
  .checkbox-label input[type="checkbox"] {
    width: 16px;
    height: 16px;
    cursor: pointer;
    accent-color: var(--app-primary);
  }
  
  .checkbox-label:hover {
    color: var(--app-text);
  }
  
  input[type="text"] {
    padding: 8px 12px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    font-size: 14px;
    background: var(--app-surface-raised);
    color: var(--app-text);
    transition: all 0.15s ease;
  }

  select {
    padding: 8px 12px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    font-size: 14px;
    background: var(--app-surface-raised);
    color: var(--app-text);
    transition: all 0.15s ease;
  }

  select:focus {
    outline: none;
    border-color: var(--app-primary);
    box-shadow: var(--app-focus-ring);
  }
  
  input[type="text"]:focus {
    outline: none;
    border-color: var(--app-primary);
    box-shadow: var(--app-focus-ring);
  }
  
  .help-text {
    font-size: 12px;
    color: var(--app-text-muted);
    font-style: italic;
  }
  
  .divider {
    height: 1px;
    background: var(--app-border-subtle);
    margin: 8px 0;
  }

  .inline-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .inline-row input[type="text"] {
    flex: 1;
  }

  .browse-btn {
    padding: 8px 12px;
    border: 1px solid var(--app-border);
    border-radius: 6px;
    background: var(--app-surface-raised);
    color: var(--app-text);
    font-size: 13px;
    cursor: pointer;
  }

  .browse-btn:hover {
    background: var(--app-surface-hover);
  }
</style>

<div class="settings-section">
  <h3 class="section-title">Audiobook Settings</h3>
  
  <div class="settings-group">
    <div class="form-row">
      <label for="voice-samples-path" class="field-label">Voice Samples Directory</label>
      <div class="inline-row">
        <input
          id="voice-samples-path"
          type="text"
          value={voiceSamplesPath}
          on:change={(e) => onVoiceSamplesPathChange((e.target as HTMLInputElement).value)}
          placeholder="Path to VibeVoice sample files"
        />
        <button class="browse-btn" on:click={onPickVoiceSamplesFolder}>Browse</button>
      </div>
      <span class="help-text">Used for VibeVoice local sample file selection and persisted in settings.</span>
    </div>

    <div class="form-column">
      <div class="field-label">
        Narrator
        <label class="checkbox-label" style="margin-left: auto;">
          <input type="checkbox" bind:checked={audiobookSettings.allowMultipleNarrators} on:change={notifyUpdate}>
          <span>Allow multiple</span>
        </label>
      </div>
      <PillList
        available={availableCharacters}
        selected={audiobookSettings.narrators || []}
        maxPills={audiobookSettings.allowMultipleNarrators ? undefined : 1}
        placeholder="Search narrators..."
        addButtonLabel="Add narrator"
        getColorForItem={getCharacterColor}
        on:add={handleNarratorAdd}
        on:remove={handleNarratorRemove}
      />
    </div>

    <label class="checkbox-label">
      <input type="checkbox" bind:checked={audiobookSettings.narratorSameAsProtagonist} on:change={notifyUpdate}>
      <span>Narrator same as protagonist</span>
    </label>

    <div class="divider"></div>

    <div class="form-column">
      <div class="field-label">Protagonist</div>
      <PillList
        available={availableCharacters}
        selected={audiobookSettings.protagonist ? [audiobookSettings.protagonist] : []}
        maxPills={1}
        placeholder="Search protagonist..."
        addButtonLabel="Set protagonist"
        getColorForItem={getCharacterColor}
        on:add={handleProtagonistAdd}
        on:remove={handleProtagonistRemove}
      />
    </div>

    <div class="form-column">
      <div class="field-label">Main Characters</div>
      <PillList
        available={availableCharacters}
        selected={audiobookSettings.mainCharacters || []}
        placeholder="Search main characters..."
        addButtonLabel="Add character"
        getColorForItem={getCharacterColor}
        on:add={handleMainCharacterAdd}
        on:remove={handleMainCharacterRemove}
      />
    </div>

    <div class="form-column">
      <div class="field-label">Side Characters</div>
      <PillList
        available={availableCharacters}
        selected={audiobookSettings.sideCharacters || []}
        placeholder="Search side characters..."
        addButtonLabel="Add character"
        getColorForItem={getCharacterColor}
        on:add={handleSideCharacterAdd}
        on:remove={handleSideCharacterRemove}
      />
    </div>
  </div>

  <h3 class="subsection-title">Parser Hints</h3>
  <div class="settings-group">
    <div class="form-row">
      <label for="parser-backend" class="field-label">Attribution Backend</label>
      <select id="parser-backend" bind:value={parserHints.parserBackend} on:change={notifyUpdate}>
        <option value="legacy">OpenBook rules</option>
        <option value="modernbooknlp">ModernBookNLP joint model</option>
      </select>
      <span class="help-text">ModernBookNLP keeps OpenBook dialogue spans and replaces aligned speaker labels</span>
    </div>

    <div class="form-row">
      <label for="pov-mode" class="field-label">POV Mode</label>
      <select id="pov-mode" bind:value={parserHints.povMode} on:change={notifyUpdate}>
        <option value="first_person">First person (I said, I asked)</option>
        <option value="third_person">Third person (he said, Sam said)</option>
      </select>
      <span class="help-text">Controls first-person overrides and narrator fallback</span>
    </div>

    <div class="form-row">
      <label for="protagonist-names" class="field-label">Protagonist Names</label>
      <input 
        id="protagonist-names" 
        type="text" 
        bind:value={protagonistNamesText}
        on:change={handleProtagonistNamesChange}
        placeholder="e.g., Catherine, Cat"
      >
      <span class="help-text">Comma-separated names used for first-person attribution</span>
    </div>

    <label class="checkbox-label" title="Adds new verbs to the speech verb list when tags like 'Sam laughed' follow quotes.">
      <input type="checkbox" bind:checked={parserHints.learnVerbs} on:change={notifyUpdate}>
      <span>Learn new speech verbs from tags</span>
    </label>

    <div class="divider"></div>

    <div class="form-column">
      <div class="field-label">Heuristic Passes</div>
      <label class="checkbox-label" title="Matches 'I said' tags to the protagonist when in first-person mode.">
        <input type="checkbox" bind:checked={parserHints.heuristics.protagonistFirstPersonTag} on:change={notifyUpdate}>
        <span>Protagonist first-person tag</span>
      </label>
      <label class="checkbox-label" title="Handles 'I/we said' tags as narrator identity when present.">
        <input type="checkbox" bind:checked={parserHints.heuristics.narratorIdentity} on:change={notifyUpdate}>
        <span>Narrator identity</span>
      </label>
      <label class="checkbox-label" title="Resolves he/she/they tags using coreference clusters.">
        <input type="checkbox" bind:checked={parserHints.heuristics.coreference} on:change={notifyUpdate}>
        <span>Coreference</span>
      </label>
      <label class="checkbox-label" title="Finds explicit tags like 'Sam said' before/after quotes.">
        <input type="checkbox" bind:checked={parserHints.heuristics.explicitTags} on:change={notifyUpdate}>
        <span>Explicit tags</span>
      </label>
      <label class="checkbox-label" title="Keeps the same speaker across quote-tag-quote sequences.">
        <input type="checkbox" bind:checked={parserHints.heuristics.tagContinuation} on:change={notifyUpdate}>
        <span>Tag continuation</span>
      </label>
      <label class="checkbox-label" title="Assumes back-to-back quotes are from the same speaker.">
        <input type="checkbox" bind:checked={parserHints.heuristics.contiguousDialogue} on:change={notifyUpdate}>
        <span>Contiguous dialogue</span>
      </label>
      <label class="checkbox-label" title="Carries speaker across short narration when no conflicting tag exists.">
        <input type="checkbox" bind:checked={parserHints.heuristics.carryAcrossShortNarration} on:change={notifyUpdate}>
        <span>Carry across short narration</span>
      </label>
      <label class="checkbox-label" title="Suggests alternate speakers when uncertain.">
        <input type="checkbox" bind:checked={parserHints.heuristics.suggestAlternatives} on:change={notifyUpdate}>
        <span>Suggest alternatives</span>
      </label>
      <label class="checkbox-label" title="Overrides to protagonist when first-person pronouns appear in dialogue.">
        <input type="checkbox" bind:checked={parserHints.heuristics.firstPersonOverride} on:change={notifyUpdate}>
        <span>First-person override</span>
      </label>
      <label class="checkbox-label" title="Avoids attributing to narrator/unknown in first-person mode.">
        <input type="checkbox" bind:checked={parserHints.heuristics.narratorFallback} on:change={notifyUpdate}>
        <span>Narrator fallback</span>
      </label>
      <label class="checkbox-label" title="Prevents assigning dialogue to the vocatively addressed name.">
        <input type="checkbox" bind:checked={parserHints.heuristics.vocativeGuard} on:change={notifyUpdate}>
        <span>Vocative guard</span>
      </label>
    </div>
  </div>

  <h3 class="subsection-title">Character Blocklist</h3>
  <div class="settings-group">
    <div class="form-column">
      <div class="field-label">Blocked Characters</div>
      <PillList
        available={[]}
        selected={parserHints.manualBlockList || []}
        placeholder="Enter character name..."
        addButtonLabel="Add to blocklist"
        allowDirectEntry={true}
        on:add={handleBlocklistAdd}
        on:remove={handleBlocklistRemove}
      />
      <span class="help-text">Characters in this list will be ignored during parsing</span>
    </div>
  </div>
</div>

