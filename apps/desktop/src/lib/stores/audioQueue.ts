import { get, writable } from 'svelte/store';
import type { PipelineProgressRecord } from '$lib/services/audioPipelineProgress';
import type { PlannedQueueJob } from '$lib/services/audioQueuePlanning';
import {
    applyQueueEvent,
    emptyQueueState,
    isCancelRequested,
    nextQueuedJob,
    restoreQueueState,
    serializeQueueState,
    type AudioQueueChapterOverview,
    type QueueEvent,
    type QueueJob,
    type QueueJobOutcome,
    type QueueState,
} from '$lib/services/audioQueueJob';

export type {
    AudioQueueChapterOverview,
    QueueJob,
    QueueJobOutcome,
    QueueJobStatus,
    QueueState,
} from '$lib/services/audioQueueJob';

/**
 * The audio queue: one persisted store of `QueueJob`s. Every change is a
 * transition of the job model (`services/audioQueueJob.ts`); a transition
 * the model rejects leaves the queue untouched and returns false.
 */

const AUDIO_QUEUE_STORAGE_KEY = 'openbook.audioQueue.v2';
/** Pre-candidate-8 key; read once so a queue survives the upgrade. */
const LEGACY_AUDIO_QUEUE_STORAGE_KEY = 'openbook.audioQueue.v1';

function isBrowser(): boolean {
    return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function loadInitialState(): QueueState {
    if (!isBrowser()) return emptyQueueState();
    try {
        const raw = window.localStorage.getItem(AUDIO_QUEUE_STORAGE_KEY)
            ?? window.localStorage.getItem(LEGACY_AUDIO_QUEUE_STORAGE_KEY);
        return restoreQueueState(raw, Date.now());
    } catch {
        return emptyQueueState();
    }
}

function persistState(state: QueueState): void {
    if (!isBrowser()) return;
    try {
        window.localStorage.setItem(AUDIO_QUEUE_STORAGE_KEY, serializeQueueState(state));
        window.localStorage.removeItem(LEGACY_AUDIO_QUEUE_STORAGE_KEY);
    } catch {
        // Ignore storage failures.
    }
}

const audioQueueStore = writable<QueueState>(loadInitialState());

audioQueueStore.subscribe((state) => {
    persistState(state);
});

function dispatch(event: QueueEvent): boolean {
    const result = applyQueueEvent(get(audioQueueStore), event);
    if (!result.accepted) {
        console.warn(`[audioQueue] rejected ${event.type}: ${result.reason}`);
        return false;
    }
    audioQueueStore.set(result.state);
    return true;
}

export const audioQueue = {
    subscribe: audioQueueStore.subscribe,

    /** Adds queued jobs (a new run when nothing is active). */
    enqueue(jobs: QueueJob[], opts: { chapterOverview?: AudioQueueChapterOverview[] } = {}): boolean {
        return dispatch({ type: 'enqueue', jobs, chapterOverview: opts.chapterOverview, at: Date.now() });
    },

    /** queued → running with its resolved plan; false if another job runs. */
    start(id: string, plan: PlannedQueueJob): boolean {
        return dispatch({ type: 'start', id, plan, at: Date.now() });
    },

    /** Finished-line count and/or the live pipeline record (running only). */
    progress(id: string, update: { linesDone?: number; record?: PipelineProgressRecord | null }): boolean {
        return dispatch({ type: 'progress', id, ...update });
    },

    finish(id: string, outcome: QueueJobOutcome): boolean {
        return dispatch({ type: 'finish', id, outcome, at: Date.now() });
    },

    /** Cancels one job, or every active job when no id is given. */
    cancel(id?: string): boolean {
        return dispatch({ type: 'cancel', id, at: Date.now() });
    },

    /** Removes a queued job. */
    remove(id: string): boolean {
        return dispatch({ type: 'remove', id });
    },

    clearFinished(): void {
        dispatch({ type: 'clearFinished' });
    },

    /** The next queued job, if any. */
    next(): QueueJob | null {
        return nextQueuedJob(get(audioQueueStore));
    },

    isCancelRequested(id: string): boolean {
        return isCancelRequested(get(audioQueueStore), id);
    },
};
