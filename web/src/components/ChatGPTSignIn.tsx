import { useEffect, useState } from "react";
import { Check, TriangleAlert } from "lucide-react";
import { chatGPTSignInStatus, startChatGPTSignIn } from "../api/client";
import { Button, Notice } from "./ui";

export function ChatGPTSignIn({ providerId, ready, onReady }: {
  providerId?: string;
  ready: boolean;
  onReady: (credentialId: string) => void;
}) {
  const [attempt, setAttempt] = useState<Awaited<ReturnType<typeof startChatGPTSignIn>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!attempt || ready) return;
    let cancelled = false;
    let timer: number;
    const poll = async () => {
      try {
        const result = await chatGPTSignInStatus(attempt.sessionId);
        if (cancelled) return;
        if (result.phase === "ready") {
          setAttempt(null);
          onReady(result.credentialId);
        } else if (result.phase === "failed" || result.phase === "expired") {
          setAttempt(null);
          setError(result.phase === "failed" ? result.error : "This sign-in expired. Start again.");
        } else {
          timer = window.setTimeout(poll, 1200);
        }
      } catch (err) {
        if (cancelled) return;
        setAttempt(null);
        setError(err instanceof Error ? err.message : "Could not check ChatGPT sign-in.");
      }
    };
    timer = window.setTimeout(poll, 1000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [attempt, onReady, ready]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try { setAttempt(await startChatGPTSignIn(providerId)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not start ChatGPT sign-in."); }
    finally { setBusy(false); }
  };

  if (window.location.protocol !== "http:" || window.location.hostname !== "127.0.0.1") {
    return <p className="card__hint">Open this panel at its 127.0.0.1 address on the computer running OpenBot to connect a ChatGPT plan.</p>;
  }

  return <div className="stack" style={{ gap: 10 }}>
    {ready ? <Notice tone="info" icon={Check}>ChatGPT account verified. Save to finish connecting it.</Notice>
      : attempt ? <>
        <a href={attempt.authorizationUrl} target="_blank" rel="noreferrer">Authorize OpenBot in ChatGPT</a>
        <p className="card__hint">After authorizing OpenBot, return to this page.</p>
      </> : <Button variant="secondary" loading={busy} loadingLabel="Opening sign-in…" disabled={busy} onClick={() => void start()}>Continue with ChatGPT</Button>}
    {error ? <Notice tone="warn" icon={TriangleAlert}>{error}</Notice> : null}
  </div>;
}
