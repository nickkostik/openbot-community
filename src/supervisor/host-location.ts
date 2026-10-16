import fs from 'node:fs';
import path from 'node:path';
import { HOST_MAIN } from '../domain/types.ts';

export const NATIVE_HOST_CANDIDATES = ['/opt/sand/sand-host/host-main.cjs', HOST_MAIN] as const;

export function selectNativeHostMain(input: {
  explicit?: string | undefined;
  running: readonly string[];
  available: readonly string[];
}): string {
  if (input.explicit) return input.explicit;
  const live = [...new Set(input.running.filter(candidate => (NATIVE_HOST_CANDIDATES as readonly string[]).includes(candidate)))];
  if (live.length > 1) throw new Error('OpenBot: multiple native host locations are running; specify --host-main explicitly.');
  if (live[0]) return live[0];
  return NATIVE_HOST_CANDIDATES.find(candidate => input.available.includes(candidate)) || HOST_MAIN;
}

/** Resolve once at an adapter's startup, never once per panel request. */
export function discoverNativeHostMain(explicit?: string): string {
  if (explicit) return explicit;
  const running: string[] = [];
  try {
    for (const pid of fs.readdirSync('/proc')) {
      if (!/^\d+$/.test(pid)) continue;
      try {
        const args = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').filter(Boolean);
        const file = args.at(-1);
        if (path.basename(args[0] || '') === 'node' && file && (NATIVE_HOST_CANDIDATES as readonly string[]).includes(file)) running.push(file);
      } catch { /* a process may exit or be unreadable */ }
    }
  } catch { /* /proc is absent outside a Linux Computer */ }
  return selectNativeHostMain({ running, available: NATIVE_HOST_CANDIDATES.filter(file => fs.existsSync(file)) });
}
