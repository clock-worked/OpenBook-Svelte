export function beginLandingAction(opts: {
  event?: MouseEvent;
  lastClickTime: number;
  debounceMs: number;
  isBusy: boolean;
  busyLogMessage: string;
  debouncedLogMessage?: string;
}): { proceed: boolean; nextLastClickTime: number } {
  if (opts.event) {
    opts.event.stopPropagation();
  }

  const now = Date.now();
  if (now - opts.lastClickTime < opts.debounceMs) {
    console.log(opts.debouncedLogMessage || 'Click debounced - too soon after last click');
    return { proceed: false, nextLastClickTime: opts.lastClickTime };
  }

  if (opts.isBusy) {
    console.log(opts.busyLogMessage);
    return { proceed: false, nextLastClickTime: now };
  }

  return { proceed: true, nextLastClickTime: now };
}

export function clearFlagAfterDelay(setter: (value: boolean) => void, delayMs: number = 100): void {
  setTimeout(() => setter(false), delayMs);
}
