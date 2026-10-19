import { useEffect, useState } from "react";
import { getLocalTransfer, resumeLocalTransfer } from "../api/client";
import type { LocalTransfer } from "../api/types";
import { Badge, Button } from "./ui";

const ADVICE: Record<string, string> = {
  "publication-backlog": "Check native publication progress and retry receipts before activating any copy.",
  "identity-review": "Review owned server bindings and the transfer receipt before remapping identities.",
  "history-review": "Compare immutable history receipts; preserve every source until the conflict is resolved.",
  "provider-backoff": "Wait for the recorded retry window; do not change providers or repeat dispatch.",
  "control-plane-review": "Check the owned control-plane connection and retry after it recovers.",
  "insufficient-evidence": "The diagnostic snapshot does not establish a cause. Keep the transfer paused for review.",
};

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
      <Badge tone={review ? "warning" : "info"}>{progress?.workerRunning ? "Running" : progress?.lockHeld ? "Transfer locked" : progress?.phase === "bound-box" && progress.publicationVerified && progress.rosterComplete ? "Transfer recorded" : "Resumable"}</Badge></div>
    <div className="card__body stack" style={{ gap: 12 }}>
      <div aria-live="polite" role="status">
        <p>{progress ? progress.phase.replaceAll("-", " ") : "Checking transfer…"}
          {progress?.agentCount !== undefined ? ` · ${progress.counts?.active ?? 0} of ${progress.agentCount} active` : ""}</p>
        {progress?.counts ? <p className="mono">{Object.entries(progress.counts).map(([phase, count]) => `${phase}: ${count}`).join(" · ")}</p> : null}
        {progress?.verifiedAgents !== undefined ? <p>{progress.verifiedAgents} publication checks passed at the last check.</p> : null}
        {progress?.jobId ? <p className="mono">Job {progress.jobId}</p> : null}
        {progress?.untrackedSourceAgents ? <p>{progress.untrackedSourceAgents} owner-roster bots are outside this transfer plan ({progress.untrackedVisibleAgents ?? 0} visible, {progress.unknownSourceAgents ?? 0} requiring ownership or visibility review). Import and review their complete cloud history and SDK context before adding them. Existing copies are retained; automatic source appending is unavailable.</p> : null}
        {progress?.publicationVerified && !progress.rosterComplete && !progress.untrackedSourceAgents ? <p>The copied cohort passed publication checks. The complete owner roster still needs a current check before transfer completion.</p> : null}
        {progress?.lockHeld && !progress.workerRunning ? <p>Another process holds the transfer lock. A running transfer worker has not been confirmed.</p> : null}
        {review ? <p>Review required during {progress.refusalPhase?.replaceAll("-", " ") ?? "preflight"}: {progress.diagnosticCode?.replaceAll("-", " ") ?? "preflight refused"}. Resolve the conflict, then resume this job.</p> : null}
      </div>
      {review ? <div role="status" aria-label="AI diagnostic review">
        <p>{progress?.diagnosticAssistant === "running" ? "AI diagnostic review is running." : progress?.diagnosticAssistant === "available" ? "AI diagnostic review completed." : progress?.diagnosticAssistant === "failed" ? "AI diagnostic review did not complete. You can still resolve the conflict and resume." : progress?.diagnosticAssistant === "disabled" ? "AI diagnostic review is disabled or the selected route is unsupported." : "Automatic AI review is unavailable."}</p>
        {progress?.diagnosticAssistant === "available" && progress.diagnosticAdviceCode && ADVICE[progress.diagnosticAdviceCode] ? <p>{ADVICE[progress.diagnosticAdviceCode]}</p> : null}
        <p>Advisory only: this review cannot change modes, run tools, merge history, or verify message delivery.</p>
      </div> : null}
      <p>Transfer preserves its recorded copies and saved settings when resumed. Native app delivery and billing have not been verified.</p>
      {progress && !progress.enabled ? <p>Custom mode and Local opt-in are required to resume.</p> : null}
      {progress && !progress.available ? <p>The Local transfer helper is missing from this installation.</p> : null}
      <div><Button variant="secondary" loading={busy} disabled={!progress?.enabled || !progress.available || progress.lockHeld || progress.workerRunning} onClick={() => void resume()}>
        {progress?.jobId ? "Resume Local transfer" : "Start Local transfer"}</Button></div>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  </section>;
}
