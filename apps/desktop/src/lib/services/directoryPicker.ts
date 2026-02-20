import { getAbsolutePathFromHandle } from '$lib/services/fs';

export async function pickDirectoryAbsolutePath(mode: FileSystemPermissionMode = 'read'): Promise<string | null> {
    if (!window.showDirectoryPicker) {
        throw new Error('Folder picker is not supported in this browser');
    }

    const handle = await window.showDirectoryPicker({ mode });
    const absolutePath = await getAbsolutePathFromHandle(handle);
    return absolutePath || null;
}
