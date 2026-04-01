export type ApiErrorType = 'network' | 'validation' | 'server';

export class ApiClientError extends Error {
    readonly type: ApiErrorType;
    readonly status?: number;
    readonly details?: unknown;

    constructor(message: string, type: ApiErrorType, options?: { status?: number; details?: unknown }) {
        super(message);
        this.name = 'ApiClientError';
        this.type = type;
        this.status = options?.status;
        this.details = options?.details;
    }
}

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8010';
const configuredApiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();

export const API_BASE_URL = (configuredApiBaseUrl && configuredApiBaseUrl.length > 0
    ? configuredApiBaseUrl
    : DEFAULT_API_BASE_URL
).replace(/\/$/, '');

export const API_ENDPOINTS = {
    parse: '/api/parse',
    setBookRoot: '/api/set-book-root',
    setAudioRoot: '/api/set-audio-root',
    listChapters: '/api/list-chapters',
    updateCharacterStats: '/api/update-character-stats',
    scanVoiceManifests: '/api/scan-voice-manifests',
    listVoiceSamples: '/api/list-voice-samples',
    saveVoiceSampleMetadata: '/api/save-voice-sample-metadata',
    voiceSamplePreview: (filename: string, samplesRoot: string) =>
        `/api/voice-sample?filename=${encodeURIComponent(filename)}&samples_root=${encodeURIComponent(samplesRoot)}`,
    readFileAbsolute: '/api/read_file_absolute',
    generateAudioLine: '/api/generate_audio_line',
    generateVibeVoiceLine: '/api/vibevoice/line',
    generateVibeVoiceCharacter: '/api/vibevoice/character',
    generateVibeVoiceChapter: '/api/vibevoice/chapter',
    deleteCharacterAudio: '/api/delete_character_audio',
    deleteAudioLine: '/api/delete_audio_line',
    reconcileAudioManifest: '/api/reconcile-audio-manifest',
    audioLine: (chapterTitle: string, characterName: string, lineId: number) =>
        `/api/audio/${encodeURIComponent(chapterTitle)}/${encodeURIComponent(characterName)}/${lineId}`,
    save: '/api/save',
} as const;

export function toApiUrl(endpointPath: string): string {
    if (/^https?:\/\//i.test(endpointPath)) return endpointPath;
    return `${API_BASE_URL}${endpointPath.startsWith('/') ? '' : '/'}${endpointPath}`;
}

function classifyStatus(status: number): ApiErrorType {
    if (status >= 400 && status < 500) return 'validation';
    return 'server';
}

async function readErrorPayload(response: Response): Promise<{ message: string; details?: unknown }> {
    const contentType = response.headers.get('content-type') || '';

    if (contentType.includes('application/json')) {
        try {
            const payload = await response.json();
            const message = payload?.detail || payload?.error || payload?.message || `HTTP ${response.status}`;
            return { message: String(message), details: payload };
        } catch {
            return { message: `HTTP ${response.status}` };
        }
    }

    try {
        const text = await response.text();
        return {
            message: text || `HTTP ${response.status}`,
            details: text || undefined,
        };
    } catch {
        return { message: `HTTP ${response.status}` };
    }
}

export function toApiClientError(error: unknown): ApiClientError {
    if (error instanceof ApiClientError) {
        return error;
    }

    if (error instanceof TypeError) {
        return new ApiClientError(error.message || 'Network request failed', 'network');
    }

    if (error instanceof Error) {
        return new ApiClientError(error.message, 'server');
    }

    return new ApiClientError(String(error), 'server');
}

export async function apiFetch(endpointPath: string, init?: RequestInit): Promise<Response> {
    try {
        return await fetch(toApiUrl(endpointPath), init);
    } catch (error) {
        throw toApiClientError(error);
    }
}

export async function apiRequestJson<T>(endpointPath: string, init?: RequestInit): Promise<T> {
    const response = await apiFetch(endpointPath, init);
    if (!response.ok) {
        const { message, details } = await readErrorPayload(response);
        throw new ApiClientError(message, classifyStatus(response.status), {
            status: response.status,
            details,
        });
    }
    return response.json() as Promise<T>;
}

export async function apiRequestVoid(endpointPath: string, init?: RequestInit): Promise<void> {
    const response = await apiFetch(endpointPath, init);
    if (!response.ok) {
        const { message, details } = await readErrorPayload(response);
        throw new ApiClientError(message, classifyStatus(response.status), {
            status: response.status,
            details,
        });
    }
}

function jsonPostInit(payload?: unknown, init?: RequestInit): RequestInit {
    const headers = new Headers(init?.headers);
    if (payload !== undefined && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
    }

    return {
        ...init,
        method: 'POST',
        headers,
        ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
    };
}

export async function apiPostJson<TResponse, TPayload = unknown>(
    endpointPath: string,
    payload?: TPayload,
    init?: RequestInit,
): Promise<TResponse> {
    return apiRequestJson<TResponse>(endpointPath, jsonPostInit(payload, init));
}

export async function apiPostVoid<TPayload = unknown>(
    endpointPath: string,
    payload?: TPayload,
    init?: RequestInit,
): Promise<void> {
    return apiRequestVoid(endpointPath, jsonPostInit(payload, init));
}