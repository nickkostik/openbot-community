/** Browser extensions and opaque cross-origin scripts do not identify app faults. */
export function appErrorMessage(event: { message?: string; filename?: string; error?: unknown }, origin: string): string | undefined {
  let errorMessage: string | undefined;
  let stack: string | undefined;
  try {
    if (event.error && typeof event.error === "object") {
      const error = event.error as { message?: unknown; stack?: unknown };
      if (typeof error.message === "string") errorMessage = error.message;
      if (typeof error.stack === "string") stack = error.stack;
    }
  } catch { /* An injected error object's getters must not break this handler. */ }
  const message = errorMessage || event.message;
  if (!message || /^script error\.?$/i.test(message.trim())) return undefined;
  const isAppScript = (source: string) => {
    try {
      const url = new URL(source.replace(/:\d+(?::\d+)?$/, ""), origin);
      // Injected scripts often report the document URL as their filename.
      // Sharing an origin does not make that script part of OpenBot.
      return url.origin === origin && (/\/app\.js$/.test(url.pathname) || /\/src\/.+\.[cm]?[jt]sx?$/.test(url.pathname));
    } catch { return false; }
  };
  const ownScript = Boolean(event.filename && isAppScript(event.filename)) ||
    Boolean(stack?.match(/https?:\/\/[^\s)]+/g)?.some(isAppScript));
  if (!ownScript && /window\.ethereum|selectedAddress|chrome-extension:|moz-extension:/i.test(message)) return undefined;
  return message;
}

/** Carry source evidence from rejected Error objects, including other realms. */
export function rejectionErrorMessage(reason: unknown, origin: string): string | undefined {
  let message: string;
  try { message = String(reason ?? "Unhandled rejection"); }
  catch { message = "Unhandled rejection"; }
  return appErrorMessage({ message, error: reason }, origin);
}

export function createErrorLimiter(windowMs = 10000): (message: string, now: number) => boolean {
  const seen = new Map<string, number>();
  return (message, now) => {
    for (const [key, at] of seen) if (now - at >= windowMs) seen.delete(key);
    if (seen.has(message)) return false;
    seen.set(message, now);
    return true;
  };
}
