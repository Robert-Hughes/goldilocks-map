export function readStoredBoolean(key: string, fallback: boolean): boolean {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // localStorage can be unavailable in privacy-restricted contexts.
  }
  return fallback;
}

export function writeStoredBoolean(key: string, value: boolean) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Controls still work without persistence.
  }
}

export function readStoredString(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStoredString(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Controls still work without persistence.
  }
}
