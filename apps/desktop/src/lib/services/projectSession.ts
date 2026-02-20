import { get } from 'svelte/store';
import { bookRoot, bookRootHandle } from '$lib/stores/bookState';
import { bookRootAbsolutePath } from '$lib/stores/bookState';
import { setRootDirHandle } from '$lib/services/fs';
import { getStoredProjectHandle, updateLastAccessed, verifyHandlePermission } from '$lib/services/persistence';

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

export async function getStoredProjectAvailability(): Promise<
  { available: true; name: string } |
  { available: false; shouldClear: boolean }
> {
  try {
    const stored = await getStoredProjectHandle();
    if (!stored?.handle) {
      return { available: false, shouldClear: false };
    }

    const hasPermission = await verifyHandlePermission(stored.handle, false);
    if (!hasPermission) {
      return { available: false, shouldClear: true };
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
  const devModePath = get(bookRootAbsolutePath) || null;

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
