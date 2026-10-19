import { useEffect, useState } from "react";
import { getLocalTransfer, resumeLocalTransfer } from "../api/client";
import type { LocalTransfer } from "../api/types";
import { Badge, Button } from "./ui";

export function LocalTransferCard() {
  const [progress, setProgress] = useState<LocalTransfer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let mounted = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try { const next = await getLocalTransfer(); if (mounted) { setProgress(next); setError(null); } }
      catch { if (mounted) setError("Could not read Local transfer progress."); }
      finally { polling = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, []);
  if (progress && !progress.enabled && !progress.jobId) return null;
  const review = progress?.phase === "review-needed";
  const resume = async () => {
    setBusy(true); setError(null);
    try { setProgress(await resumeLocalTransfer(progress?.jobId ? "resume" : "start")); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not resume Local transfer."); }
    finally { setBusy(false); }
  };
  return <section className="card col-12" aria-labelledby="h-local-transfer">
    <div className="card__head"><span className="card__label" id="h-local-transfer">Local transfer</span>
      <Badge tone={review ? "warning" : "info"}>{progress?.workerRunning ? "Running" : progress?.phase === "bound-box" ? "Transfer recorded" : "Resumable"}</Badge></div>
    <div className="card__body stack" style={{ gap: 12 }}>
      <div aria-live="polite" role="status">
        <p>{progress ? progress.phase.replaceAll("-", " ") : "Checking transfer…"}
          {progress?.agentCount !== undefined ? ` · ${progress.counts?.active ?? 0} of ${progress.agentCount} active` : ""}</p>
        {progress?.counts ? <p className="mono">{Object.entries(progress.counts).map(([phase, count]) => `${phase}: ${count}`).join(" · ")}</p> : null}
        {progress?.verifiedAgents !== undefined ? <p>{progress.verifiedAgents} publication checks passed at the last check.</p> : null}
        {progress?.jobId ? <p className="mono">Job {progress.jobId}</p> : null}
        {review ? <p>Review required during {progress.refusalPhase?.replaceAll("-", " ") ?? "preflight"}: {progress.diagnosticCode?.replaceAll("-", " ") ?? "preflight refused"}. Resolve the conflict, then resume this job.</p> : null}
      </div>
      {review ? <p>{progress?.diagnosticReason} Automatic AI review is unavailable.</p> : null}
      <p>Transfer preserves its recorded copies and saved settings when resumed. Native app delivery and billing have not been verified.</p>
      {progress && !progress.enabled ? <p>Custom mode and Local opt-in are required to resume.</p> : null}
      {progress && !progress.available ? <p>The Local transfer helper is missing from this installation.</p> : null}
      <div><Button variant="secondary" loading={busy} disabled={!progress?.enabled || !progress.available || progress.workerRunning} onClick={() => void resume()}>
        {progress?.jobId ? "Resume Local transfer" : "Start Local transfer"}</Button></div>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  </section>;
}
