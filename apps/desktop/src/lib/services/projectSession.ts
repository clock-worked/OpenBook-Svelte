import { get } from 'svelte/store';
import { bookRoot, bookRootHandle } from '$lib/stores/bookState';
import { bookRootAbsolutePath } from '$lib/stores/bookState';
import { setBackendBookRoot, setRootDirHandle } from '$lib/services/fs';
import { getStoredProjectHandle, updateLastAccessed, verifyHandlePermission } from '$lib/services/persistence';
import { bookRootPathOverride } from '$lib/stores/settings';

export type RestoreStoredProjectResult =
  | { ok: true; handle: FileSystemDirectoryHandle; name: string }
  | { ok: false; reason: 'missing' | 'permission_denied' | 'error'; error?: unknown };

export function applyProjectHandle(handle: FileSystemDirectoryHandle): void {
  setRootDirHandle(handle);
  bookRootHandle.set(handle);
  bookRoot.set(handle.name);
}

export function getActiveProjectHandle(): FileSystemDirectoryHandle | null {
  return get(bookRootHandle);
}

async function restoreBackendProject(rootPath: string): Promise<boolean> {
  for (const delayMs of [0, 250, 500, 1000]) {
    if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    if (await setBackendBookRoot(rootPath)) return true;
  }
  return false;
}

export async function getStoredProjectAvailability(): Promise<
  { available: true; name: string } |
  { available: false; shouldClear: boolean }
> {
  try {
    const storedPath = get(bookRootPathOverride);
    if (storedPath) {
      return {
        available: true,
        name: storedPath.split(/[\\/]/).filter(Boolean).at(-1) || storedPath,
      };
    }

    const stored = await getStoredProjectHandle();
    if (!stored?.handle) {
      return { available: false, shouldClear: false };
    }

    return { available: true, name: stored.name };
  } catch {
    return { available: false, shouldClear: true };
  }
}

export type RouteProjectInitResult =
  | {
    ok: true;
    rootHandle: FileSystemDirectoryHandle | null;
    isDevMode: boolean;
    devModePath: string | null;
    restoredFromPersistence: boolean;
  }
  | {
    ok: false;
    reason: 'missing' | 'permission_denied' | 'error';
    error?: unknown;
  };

export async function initRouteProjectContext(options?: {
  allowDevModeWithoutHandle?: boolean;
  requestPermission?: boolean;
  touchLastAccessed?: boolean;
}): Promise<RouteProjectInitResult> {
  const allowDevModeWithoutHandle = options?.allowDevModeWithoutHandle ?? false;
  const requestPermission = options?.requestPermission ?? true;
  const touchLastAccessed = options?.touchLastAccessed ?? true;

  const inMemoryHandle = get(bookRootHandle);
  const devModePath = get(bookRootAbsolutePath) || get(bookRootPathOverride) || null;

  if (inMemoryHandle) {
    return {
      ok: true,
      rootHandle: inMemoryHandle,
      isDevMode: false,
      devModePath,
      restoredFromPersistence: false,
    };
  }

  const isDevMode = !!devModePath;
  if (allowDevModeWithoutHandle && isDevMode) {
    const synced = await restoreBackendProject(devModePath);
    if (!synced) {
      return { ok: false, reason: 'error', error: new Error('Stored project path is unavailable') };
    }
    bookRootAbsolutePath.set(devModePath);
    bookRootPathOverride.set(devModePath);
    bookRoot.set(devModePath.split(/[\\/]/).filter(Boolean).at(-1) || devModePath);
    return {
      ok: true,
      rootHandle: null,
      isDevMode: true,
      devModePath,
      restoredFromPersistence: false,
    };
  }

  const restored = await restoreStoredProject({ requestPermission, touchLastAccessed });
  if (!restored.ok) {
    return restored;
  }

  return {
    ok: true,
    rootHandle: restored.handle,
    isDevMode: false,
    devModePath,
    restoredFromPersistence: true,
  };
}

export async function restoreStoredProject(options?: {
  requestPermission?: boolean;
  touchLastAccessed?: boolean;
}): Promise<RestoreStoredProjectResult> {
  try {
    const requestPermission = options?.requestPermission ?? true;
    const touchLastAccessed = options?.touchLastAccessed ?? true;

    const stored = await getStoredProjectHandle();
    if (!stored?.handle) {
      return { ok: false, reason: 'missing' };
    }

    const hasPermission = await verifyHandlePermission(stored.handle, requestPermission);
    if (!hasPermission) {
      return { ok: false, reason: 'permission_denied' };
    }

    applyProjectHandle(stored.handle);

    if (touchLastAccessed) {
      await updateLastAccessed();
    }

    return {
      ok: true,
      handle: stored.handle,
      name: stored.name,
    };
  } catch (error) {
    return {
      ok: false,
      reason: 'error',
      error,
    };
  }
}
