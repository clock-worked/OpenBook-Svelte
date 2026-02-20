import { get, writable } from 'svelte/store';

export type AudioQueueStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';

export interface AudioQueueItem {
    id: string;
    characterId: string;
    characterName: string;
    chapterTitle: string;
    generationMode?: 'missing' | 'full';
    total: number;
    current: number;
    totalCharacters: number;
    processedCharacters: number;
    status: AudioQueueStatus;
    createdAt: number;
    startedAt?: number;
    finishedAt?: number;
    errors?: string[];
    cancelRequested?: boolean;
}

export interface AudioQueueChapterOverview {
    chapterTitle: string;
    processableTotal: number;
    missingVoiceTotal: number;
}

interface AudioQueueState {
    items: AudioQueueItem[];
    runStartedAt: number | null;
    chapterOverview: Record<string, AudioQueueChapterOverview>;
}

const AUDIO_QUEUE_STORAGE_KEY = 'openbook.audioQueue.v1';

function isBrowser(): boolean {
    return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function toFiniteInt(value: unknown, fallback = 0): number {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return fallback;
    return Math.max(0, Math.floor(numeric));
}

function normalizeStatus(value: unknown): AudioQueueStatus {
    return value === 'queued' || value === 'running' || value === 'completed' || value === 'failed' || value === 'canceled'
        ? value
        : 'queued';
}

function normalizeItem(value: unknown): AudioQueueItem | null {
    if (!value || typeof value !== 'object') return null;
    const raw = value as Record<string, unknown>;
    const id = String(raw.id ?? '').trim();
    const characterId = String(raw.characterId ?? '').trim();
    const chapterTitle = String(raw.chapterTitle ?? '').trim();
    if (!id || !characterId || !chapterTitle) return null;

    const status = normalizeStatus(raw.status);
    const normalizedStatus: AudioQueueStatus = status === 'running' ? 'queued' : status;

    return {
        id,
        characterId,
        characterName: String(raw.characterName ?? characterId),
        chapterTitle,
        generationMode: raw.generationMode === 'full' ? 'full' : 'missing',
        total: toFiniteInt(raw.total),
        current: normalizedStatus === 'queued' ? 0 : toFiniteInt(raw.current),
        totalCharacters: toFiniteInt(raw.totalCharacters),
        processedCharacters: normalizedStatus === 'queued' ? 0 : toFiniteInt(raw.processedCharacters),
        status: normalizedStatus,
        createdAt: toFiniteInt(raw.createdAt, Date.now()),
        startedAt: normalizedStatus === 'queued' ? undefined : (Number.isFinite(Number(raw.startedAt)) ? Number(raw.startedAt) : undefined),
        finishedAt: Number.isFinite(Number(raw.finishedAt)) ? Number(raw.finishedAt) : undefined,
        errors: Array.isArray(raw.errors) ? raw.errors.map((entry) => String(entry)) : undefined,
        cancelRequested: Boolean(raw.cancelRequested),
    };
}

function loadInitialState(): AudioQueueState {
    if (!isBrowser()) {
        return {
            items: [],
            runStartedAt: null,
            chapterOverview: {},
        };
    }

    try {
        const raw = window.localStorage.getItem(AUDIO_QUEUE_STORAGE_KEY);
        if (!raw) throw new Error('missing');

        const parsed = JSON.parse(raw) as Partial<AudioQueueState>;
        const normalizedItems = Array.isArray(parsed.items)
            ? parsed.items.map((item) => normalizeItem(item)).filter((item): item is AudioQueueItem => Boolean(item))
            : [];
        const hasActive = normalizedItems.some((item) => item.status === 'queued' || item.status === 'running');

        const chapterOverviewEntries = Object.entries(parsed.chapterOverview ?? {})
            .map(([title, overview]) => {
                const source = (overview ?? {}) as Partial<AudioQueueChapterOverview>;
                const normalizedTitle = String(source.chapterTitle ?? title ?? '').trim();
                if (!normalizedTitle) return null;
                return [
                    normalizedTitle,
                    {
                        chapterTitle: normalizedTitle,
                        processableTotal: toFiniteInt(source.processableTotal),
                        missingVoiceTotal: toFiniteInt(source.missingVoiceTotal),
                    } satisfies AudioQueueChapterOverview,
                ] as const;
            })
            .filter((entry): entry is readonly [string, AudioQueueChapterOverview] => Boolean(entry));

        return {
            items: normalizedItems,
            runStartedAt: hasActive ? toFiniteInt(parsed.runStartedAt, Date.now()) : null,
            chapterOverview: Object.fromEntries(chapterOverviewEntries),
        };
    } catch {
        return {
            items: [],
            runStartedAt: null,
            chapterOverview: {},
        };
    }
}

function persistState(state: AudioQueueState): void {
    if (!isBrowser()) return;
    try {
        window.localStorage.setItem(AUDIO_QUEUE_STORAGE_KEY, JSON.stringify(state));
    } catch {
        // Ignore storage failures.
    }
}

const audioQueueStore = writable<AudioQueueState>(loadInitialState());

audioQueueStore.subscribe((state) => {
    persistState(state);
});

export const audioQueue = {
    subscribe: audioQueueStore.subscribe,
};

export function enqueueAudioJob(item: Omit<AudioQueueItem, 'current' | 'processedCharacters' | 'status' | 'createdAt'>): void {
    audioQueueStore.update((state) => ({
        ...(() => {
            const hasActiveWork = state.items.some((entry) => entry.status === 'queued' || entry.status === 'running');
            if (hasActiveWork) return state;

            return {
                ...state,
                items: state.items.filter((entry) => entry.status === 'queued' || entry.status === 'running'),
                runStartedAt: Date.now(),
                chapterOverview: {},
            };
        })(),
        items: [
            ...(state.items.some((entry) => entry.status === 'queued' || entry.status === 'running')
                ? state.items
                : state.items.filter((entry) => entry.status === 'queued' || entry.status === 'running')),
            {
                ...item,
                generationMode: item.generationMode === 'full' ? 'full' : 'missing',
                current: 0,
                processedCharacters: 0,
                status: 'queued',
                createdAt: Date.now(),
            },
        ],
    }));
}

export function setQueueChapterOverview(chapterTitle: string, processableTotal: number, missingVoiceTotal: number): void {
    audioQueueStore.update((state) => {
        const normalizedTitle = String(chapterTitle || '').trim();
        if (!normalizedTitle) return state;

        return {
            ...state,
            chapterOverview: {
                ...state.chapterOverview,
                [normalizedTitle]: {
                    chapterTitle: normalizedTitle,
                    processableTotal: Math.max(0, Math.floor(Number(processableTotal) || 0)),
                    missingVoiceTotal: Math.max(0, Math.floor(Number(missingVoiceTotal) || 0)),
                },
            },
        };
    });
}

export function getNextQueuedJob(): AudioQueueItem | null {
    const state = get(audioQueueStore);
    return state.items.find((item) => item.status === 'queued') || null;
}

export function markJobRunning(id: string): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    status: 'running',
                    startedAt: Date.now(),
                    current: 0,
                    processedCharacters: 0,
                }
                : item
        ),
    }));
}

export function updateJobProgress(
    id: string,
    current: number,
    total: number,
    processedCharacters?: number,
    totalCharacters?: number
): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    current,
                    total,
                    processedCharacters: Math.max(0, Math.floor(processedCharacters ?? item.processedCharacters ?? 0)),
                    totalCharacters: Math.max(0, Math.floor(totalCharacters ?? item.totalCharacters ?? 0)),
                }
                : item
        ),
    }));
}

export function markJobCompleted(id: string): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    status: 'completed',
                    current: item.total,
                    processedCharacters: item.totalCharacters,
                    finishedAt: Date.now(),
                }
                : item
        ),
    }));
}

export function markJobFailed(id: string, errors: string[]): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    status: 'failed',
                    finishedAt: Date.now(),
                    errors,
                }
                : item
        ),
    }));
}

export function markJobCanceled(id: string): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    status: 'canceled',
                    finishedAt: Date.now(),
                }
                : item
        ),
    }));
}

export function removeQueuedJob(id: string): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.filter((item) => !(item.id === id && item.status === 'queued')),
    }));
}

export function clearFinishedJobs(): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.filter((item) => item.status === 'queued' || item.status === 'running'),
        runStartedAt: state.items.some((item) => item.status === 'queued' || item.status === 'running')
            ? state.runStartedAt
            : null,
        chapterOverview: state.items.some((item) => item.status === 'queued' || item.status === 'running')
            ? state.chapterOverview
            : {},
    }));
}

export function requeueRunningJobs(): void {
    audioQueueStore.update((state) => {
        const hadRunning = state.items.some((item) => item.status === 'running');
        if (!hadRunning) return state;

        return {
            ...state,
            items: state.items.map((item) =>
                item.status === 'running'
                    ? {
                        ...item,
                        status: 'queued',
                        current: 0,
                        processedCharacters: 0,
                        startedAt: undefined,
                        finishedAt: undefined,
                    }
                    : item
            ),
            runStartedAt: state.runStartedAt ?? Date.now(),
        };
    });
}

export function requestCancelJob(id: string): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) =>
            item.id === id
                ? {
                    ...item,
                    cancelRequested: true,
                }
                : item
        ),
    }));
}

export function cancelAllJobs(): void {
    audioQueueStore.update((state) => ({
        ...state,
        items: state.items.map((item) => {
            if (item.status === 'queued') {
                return {
                    ...item,
                    status: 'canceled',
                    cancelRequested: true,
                    finishedAt: Date.now(),
                };
            }
            if (item.status === 'running') {
                return {
                    ...item,
                    cancelRequested: true,
                };
            }
            return item;
        }),
    }));
}

export function isJobCanceled(id: string): boolean {
    const state = get(audioQueueStore);
    const item = state.items.find((entry) => entry.id === id);
    return Boolean(item?.cancelRequested);
}
