<script lang="ts">
  import { audioState, pause, resume, seek, next, previous, nextSpeaker, previousSpeaker, formatTime } from '$lib/stores/audio';
  import { getAudioPathIfExists } from '$lib/services/audio';
  import { Play, Pause, SkipBack, SkipForward } from 'lucide-svelte';
  
  $: isPlaying = $audioState.isPlaying;
  $: isPaused = $audioState.isPaused;
  $: chapterTitle = $audioState.currentChapterTitle || '';
  $: isActive = isPlaying || isPaused;
  $: currentTime = $audioState.currentTime || 0;
  $: duration = $audioState.duration || 0;
  $: progressPercent = duration > 0 ? Math.max(0, Math.min(100, (currentTime / duration) * 100)) : 0;

  function handlePlayPause() {
    if (!isActive) return;
    if (isPlaying) {
      pause();
    } else if (isPaused) {
      resume();
    }
  }

  function handleSeek(event: MouseEvent) {
    // Seek is disabled - we show 00:00 always
    // This could be used for total duration seeking in the future
  }

  async function handleNext() {
    if (!isActive) return;
    await next((lineId, charName) => {
      return getAudioPathIfExists(chapterTitle, charName, lineId).then(path => path);
    });
  }

  async function handlePrevious() {
    if (!isActive) return;
    await previous((lineId, charName) => {
      return getAudioPathIfExists(chapterTitle, charName, lineId).then(path => path);
    });
  }

  async function handleNextSpeaker() {
    if (!isActive) return;
    await nextSpeaker((lineId, charName) => {
      return getAudioPathIfExists(chapterTitle, charName, lineId).then(path => path);
    });
  }

  async function handlePreviousSpeaker() {
    if (!isActive) return;
    await previousSpeaker((lineId, charName) => {
      return getAudioPathIfExists(chapterTitle, charName, lineId).then(path => path);
    });
  }

</script>

<div class="playback-bar">
  <div class="playback-content">
    <div class="controls">
      <button class="skip-btn" on:click={handlePrevious} disabled={!isActive} title="Previous line" aria-label="Previous line">
        <SkipBack size={18} />
      </button>
      
      <button class="speaker-btn" on:click={handlePreviousSpeaker} disabled={!isActive} title="Previous speaker" aria-label="Previous speaker">
        <img 
          src="/images/tdesign--user-arrow-left-filled.png" 
          alt="Previous speaker"
          on:error={(e) => console.error('Failed to load previous speaker icon:', e)}
          on:load={() => console.log('Previous speaker icon loaded')}
        />
      </button>
      
      <button class="play-pause-btn" on:click={handlePlayPause} disabled={!isActive} title={isPlaying ? 'Pause' : 'Play'} aria-label={isPlaying ? 'Pause' : 'Play'}>
        {#if isPlaying}
          <Pause size={20} color="white" />
        {:else}
          <Play size={20} color="white" />
        {/if}
      </button>
      
      <button class="speaker-btn" on:click={handleNextSpeaker} disabled={!isActive} title="Next speaker" aria-label="Next speaker">
        <img 
          src="/images/tdesign--user-arrow-right-filled.png" 
          alt="Next speaker"
          on:error={(e) => console.error('Failed to load next speaker icon:', e)}
          on:load={() => console.log('Next speaker icon loaded')}
        />
      </button>
      
      <button class="skip-btn" on:click={handleNext} disabled={!isActive} title="Next line" aria-label="Next line">
        <SkipForward size={18} />
      </button>
    </div>
  </div>
</div>

<style>
  .playback-bar {
    width: 100%;
    height: 60px;
    background: #ffffff;
    border-top: 1px solid #e5e7eb;
    box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 16px;
    flex-shrink: 0;
  }

  .playback-content {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
  }

  .controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .play-pause-btn {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: #1f2937;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    flex-shrink: 0;
  }

  .play-pause-btn:hover {
    background: #111827;
    transform: scale(1.05);
  }

  .play-pause-btn:active {
    transform: scale(0.95);
  }

  .play-pause-btn:disabled {
    background: #9ca3af;
    cursor: not-allowed;
    opacity: 0.6;
  }

  .play-pause-btn:disabled:hover {
    transform: none;
  }

  .skip-btn {
    width: 32px;
    height: 32px;
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    color: #6b7280;
    transition: all 0.2s ease;
    flex-shrink: 0;
  }

  .skip-btn:hover {
    background: rgba(0, 0, 0, 0.05);
    color: #1f2937;
  }

  .skip-btn:active {
    background: rgba(0, 0, 0, 0.1);
  }

  .skip-btn:disabled {
    color: #d1d5db;
    cursor: not-allowed;
    opacity: 0.5;
  }

  .skip-btn:disabled:hover {
    background: transparent;
    color: #d1d5db;
  }

  .speaker-btn {
    width: 32px;
    height: 32px;
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    color: #6b7280;
    transition: all 0.2s ease;
    flex-shrink: 0;
    padding: 0;
  }

  .speaker-btn img {
    width: 18px;
    height: 18px;
    object-fit: contain;
    display: block;
    /* Convert to gray color matching skip buttons (#6b7280) */
    filter: brightness(0) saturate(100%) invert(42%) sepia(5%) saturate(502%) hue-rotate(177deg) brightness(96%) contrast(89%);
    opacity: 1;
  }
  
  .speaker-btn:hover img {
    /* Convert to darker gray on hover (#1f2937) */
    filter: brightness(0) saturate(100%) invert(13%) sepia(5%) saturate(1016%) hue-rotate(177deg) brightness(96%) contrast(87%);
  }
  
  .speaker-btn:disabled img {
    /* Convert to light gray when disabled (#d1d5db) */
    filter: brightness(0) saturate(100%) invert(84%) sepia(4%) saturate(313%) hue-rotate(177deg) brightness(90%) contrast(89%);
    opacity: 0.5;
  }

  .speaker-btn:hover {
    background: rgba(0, 0, 0, 0.05);
    color: #1f2937;
  }

  .speaker-btn:active {
    background: rgba(0, 0, 0, 0.1);
  }

  .speaker-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .speaker-btn:disabled:hover {
    background: transparent;
  }

</style>

