/** Split a command line into words, honouring double quotes. */
export function splitCommand(line: string): string[] {
  const out: string[] = [];
  const re = /"([^"]*)"|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(line))) out.push(m[1] !== undefined ? m[1] : m[2]);
  return out;
}

/** Guess which built-in provider an API key belongs to from its prefix. Order matters: specific first. */
export function detectProvider(key: string): string | null {
  const k = key.trim();
  if (/^sk-ant-/.test(k)) return 'anthropic';
  if (/^sk-or-/.test(k)) return 'openrouter';
  if (/^nvapi-/.test(k)) return 'nvidia';
  if (/^gsk_/.test(k)) return 'groq';
  if (/^AIza[0-9A-Za-z_-]{20,}$/.test(k)) return 'gemini';
  if (/^sk-[A-Za-z0-9_-]{16,}$/.test(k)) return 'openai';
  return null;
}

export const isValidName = (name: string): boolean => /^[a-z][a-z0-9_]{0,31}$/.test(name);

/** Parse "NAME=value" lines (environment variables or headers). Returns null when a line is malformed. */
export function parsePairs(text: string): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.search(/[=:]/);
    if (at <= 0) return null;
    const name = line.slice(0, at).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(name)) return null;
    out[name] = line.slice(at + 1).trim();
  }
  return out;
}

/** The HUD runs inside the OS web view, so the browser's own platform string tells the two apart. */
export const IS_MAC = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || navigator.userAgent);
export const KEY_STORE = IS_MAC ? 'the macOS Keychain' : 'Windows Credential Manager';
export const OS_NAME = IS_MAC ? 'macOS' : 'Windows';
