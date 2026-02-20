export type ProjectInitFailureReason = 'missing' | 'permission_denied' | 'error';

export interface InitFailurePolicy {
  delayMs: number;
  logLevel: 'warn' | 'error';
  chapterMessage: string;
}

export function getProjectInitFailurePolicy(reason: ProjectInitFailureReason): InitFailurePolicy {
  if (reason === 'missing') {
    return {
      delayMs: 1500,
      logLevel: 'warn',
      chapterMessage: 'No project loaded. Redirecting to landing page...',
    };
  }

  if (reason === 'permission_denied') {
    return {
      delayMs: 2000,
      logLevel: 'error',
      chapterMessage: 'Permission denied. Please select a project from the landing page.',
    };
  }

  return {
    delayMs: 2000,
    logLevel: 'error',
    chapterMessage: 'Failed to load project: Unknown error',
  };
}
