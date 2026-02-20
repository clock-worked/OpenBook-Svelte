import type { ChapterStatus } from '$lib/stores/bookState';
import {
    listChaptersFromBackend,
    scanChapters,
    setBackendAudioRoot,
    setBackendBookRoot,
    syncBookRootFromHandle,
} from '$lib/services/fs';

export async function loadProjectFromHandle(opts: {
    handle: FileSystemDirectoryHandle;
    audioRootPath?: string | null;
}): Promise<{ resolvedBookRoot: string | null; chapters: ChapterStatus[] }> {
    const rootSync = await syncBookRootFromHandle({
        handle: opts.handle,
        audioRootPath: opts.audioRootPath ?? null,
    });

    const chapters = await scanChapters(opts.handle);
    return {
        resolvedBookRoot: rootSync.resolvedBookRoot,
        chapters,
    };
}

export async function loadProjectFromAbsolutePath(opts: {
    rootPath: string;
    audioRootPath?: string | null;
}): Promise<{ bookRootSynced: boolean; audioRootSynced: boolean; chapters: ChapterStatus[] }> {
    const bookRootSynced = await setBackendBookRoot(opts.rootPath);
    const audioRootSynced = opts.audioRootPath ? await setBackendAudioRoot(opts.audioRootPath) : false;
    const chapters = await listChaptersFromBackend();

    return {
        bookRootSynced,
        audioRootSynced,
        chapters,
    };
}
