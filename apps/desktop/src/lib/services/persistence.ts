// IndexedDB persistence for FileSystemDirectoryHandle
// This allows us to remember the last opened project

const DB_NAME = 'openbook-storage';
const DB_VERSION = 1;
const STORE_NAME = 'project-handles';
const HANDLE_KEY = 'last-project-handle';

interface StoredProject {
  handle: FileSystemDirectoryHandle;
  name: string;
  lastAccessed: number;
}

/**
 * Initialize IndexedDB
 */
function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
  });
}

/**
 * Store a directory handle for later retrieval
 */
export async function storeProjectHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  try {
    const db = await openDatabase();
    const transaction = db.transaction(STORE_NAME, 'readwrite');
    const store = transaction.objectStore(STORE_NAME);

    const projectData: StoredProject = {
      handle,
      name: handle.name,
      lastAccessed: Date.now()
    };

    return new Promise((resolve, reject) => {
      const request = store.put(projectData, HANDLE_KEY);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  } catch (error) {
    console.error('Failed to store project handle:', error);
    throw error;
  }
}

/**
 * Retrieve the last opened project handle
 */
export async function getStoredProjectHandle(): Promise<StoredProject | null> {
  try {
    const db = await openDatabase();
    const transaction = db.transaction(STORE_NAME, 'readonly');
    const store = transaction.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.get(HANDLE_KEY);
      request.onsuccess = () => {
        const result = request.result as StoredProject | undefined;
        resolve(result || null);
      };
      request.onerror = () => reject(request.error);
    });
  } catch (error) {
    console.error('Failed to retrieve project handle:', error);
    return null;
  }
}

/**
 * Clear stored project handle
 */
export async function clearStoredProjectHandle(): Promise<void> {
  try {
    const db = await openDatabase();
    const transaction = db.transaction(STORE_NAME, 'readwrite');
    const store = transaction.objectStore(STORE_NAME);

    return new Promise((resolve, reject) => {
      const request = store.delete(HANDLE_KEY);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });
  } catch (error) {
    console.error('Failed to clear project handle:', error);
    throw error;
  }
}

/**
 * Verify that we still have permission to access a stored handle
 */
export async function verifyHandlePermission(
  handle: FileSystemDirectoryHandle,
  requestPermission: boolean = false
): Promise<boolean> {
  try {
    // Check if we already have permission
    const permission = await handle.queryPermission({ mode: 'readwrite' });
    
    if (permission === 'granted') {
      return true;
    }

    // If requested, try to get permission
    if (requestPermission) {
      const newPermission = await handle.requestPermission({ mode: 'readwrite' });
      return newPermission === 'granted';
    }

    return false;
  } catch (error) {
    console.error('Failed to verify handle permission:', error);
    return false;
  }
}

/**
 * Update the last accessed timestamp for the stored project
 */
export async function updateLastAccessed(): Promise<void> {
  try {
    const stored = await getStoredProjectHandle();
    if (stored) {
      stored.lastAccessed = Date.now();
      await storeProjectHandle(stored.handle);
    }
  } catch (error) {
    console.error('Failed to update last accessed time:', error);
  }
}

