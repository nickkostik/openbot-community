import { useRef, useState } from "react";
import { ArrowRight, Check, ChevronLeft, Info, ShieldAlert, TriangleAlert } from "lucide-react";
import { ApiError, completeOpenAIOAuth, startOpenAIOAuth } from "../api/client";
import { credentialFileEntries, MAX_CREDENTIAL_FILE_BYTES, type CredentialFileEntry } from "../lib/credential-file";
import { PRESETS, type Preset } from "../lib/presets";
import { refusalKindLabel, refusalRemedy } from "../lib/refusal";
import { navigate } from "../lib/router";
import { useApp } from "../store";
import { Button, Notice } from "../components/ui";
import { Field, Input, PasswordInput } from "../components/fields";
import { Listbox } from "../components/Listbox";

type CredentialMode = "api-key" | "oauth" | "file";
type ApiType = "chat-completions" | "responses" | "anthropic";

export function Setup() {
  const { save } = useApp();
  const initial = PRESETS[0]!;
  const [step, setStep] = useState(1);
  const [presetId, setPresetId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [origin, setOrigin] = useState(initial.origin);
  const [apiType, setApiType] = useState<ApiType>(initial.apiType ?? "chat-completions");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [oauthBusy, setOauthBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [oauthSession, setOauthSession] = useState<string | null>(null);
  const [oauthUrl, setOauthUrl] = useState<string | null>(null);
  const [callbackUrl, setCallbackUrl] = useState("");
  const [credentialId, setCredentialId] = useState<string | null>(null);
  const [credentialMode, setCredentialMode] = useState<CredentialMode>("api-key");
  const [fileEntries, setFileEntries] = useState<CredentialFileEntry[]>([]);
  const [fileEntryId, setFileEntryId] = useState<string | null>(null);
  const fileReadOrder = useRef(0);

  const preset: Preset = PRESETS.find((p) => p.id === presetId) ?? initial;
  const selectedEntry = fileEntries.find((entry) => entry.id === fileEntryId);

  const pickPreset = (p: Preset) => {
    fileReadOrder.current++;
    setPresetId(p.id);
    setName(p.name);
    setOrigin(p.origin);
    setApiType(p.apiType ?? "chat-completions");
    setSecret("");
    setCredentialMode("api-key");
    setCredentialId(null);
    setOauthSession(null);
    setOauthUrl(null);
    setCallbackUrl("");
    setFileEntries([]);
    setFileEntryId(null);
    setFieldError(null);
    setRefusal(null);
  };

  const readCredentialFile = async (file: File | undefined) => {
    if (!file) return;
    const order = ++fileReadOrder.current;
    setFileEntries([]);
    setFileEntryId(null);
    setFieldError(null);
    try {
      if (file.size > MAX_CREDENTIAL_FILE_BYTES) throw new Error("Credential file exceeds 256 KiB.");
      const entries = credentialFileEntries(await file.text());
      if (order !== fileReadOrder.current) return;
      setFileEntries(entries);
      setFileEntryId(entries.length === 1 ? entries[0]!.id : null);
    } catch (err) {
      if (order === fileReadOrder.current) setFieldError(err instanceof Error ? err.message : "Could not read the login file.");
    }
  };

  const continueToReview = () => {
    setFieldError(null);
    if (!name.trim()) return setFieldError("Account name is required.");
    if (!origin.trim()) return setFieldError("Base URL is required.");
    try {
      const url = new URL(origin.trim());
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { return setFieldError("Enter an HTTP or HTTPS base URL without credentials, a query, or a fragment."); }
    if (credentialMode === "api-key" && !secret.trim()) return setFieldError("API key is required.");
    if (credentialMode === "oauth" && !credentialId) return setFieldError("Complete OpenAI sign-in before continuing.");
    if (credentialMode === "file" && !selectedEntry) return setFieldError("Choose a login file and select the account to import.");
    setStep(3);
  };

  const beginOAuth = async () => {
    setOauthBusy(true);
    setFieldError(null);
    try {
      const result = await startOpenAIOAuth();
      setOauthSession(result.sessionId);
      setOauthUrl(result.authorizationUrl);
    } catch (err) {
      setFieldError(err instanceof Error ? err.message : "Could not start OpenAI sign-in.");
    } finally { setOauthBusy(false); }
  };

  const finishOAuth = async () => {
    if (!oauthSession || !callbackUrl.trim()) return;
    setOauthBusy(true);
    setFieldError(null);
    try {
      const result = await completeOpenAIOAuth(oauthSession, callbackUrl.trim());
      setCredentialId(result.credentialId);
      setOauthSession(null);
      setOauthUrl(null);
      setCallbackUrl("");
    } catch (err) {
      setFieldError(err instanceof Error ? err.message : "OpenAI sign-in failed.");
    } finally { setOauthBusy(false); }
  };

  const addAccount = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const result = await save({
        kind: "add-provider", familyId: preset.id, name: name.trim(), origin: origin.trim(), apiType,
        ...(credentialMode === "oauth" && credentialId ? { credentialId }
          : { secret: credentialMode === "file" ? selectedEntry?.value ?? "" : secret }),
      }, { successTitle: "Account added", successMessage: "Fetch this account’s models, then choose which ones to add." });
      navigate({ kind: "models", providerId: result.createdProviderId });
    } catch (err) {
      setRefusal(err instanceof ApiError && err.refusal
        ? `${refusalKindLabel(err.refusal)} — ${refusalRemedy(err.refusal)}`
        : err instanceof Error ? err.message : "Could not add the account.");
    } finally { setBusy(false); }
  };

  const StepIndicator = ({ n }: { n: number }) => <div className="step__num">{n < step ? <Check aria-hidden="true" /> : n}</div>;
  const credentialOptions: { id: CredentialMode; label: string }[] = [
    { id: "api-key", label: "API key" },
    ...(preset.oauth ? [{ id: "oauth" as const, label: "OpenAI sign-in" }] : []),
    { id: "file", label: "Import login file" },
  ];

  return <div className="wizard">
    <div className="page-title-row">
      <button className="icon-btn" aria-label="Back to Models" disabled={busy || oauthBusy} onClick={() => navigate({ kind: "models" })}><ChevronLeft aria-hidden="true" /></button>
      <h1>Add a provider account</h1>
    </div>
    <div className="steps">
      {["Provider", "Credentials", "Review"].map((label, index) => <div className="step-pair" key={label}>
        {index ? <span className="step__sep" /> : null}
        <div className={`step${step === index + 1 ? " is-current" : ""}${step > index + 1 ? " is-done" : ""}`}><StepIndicator n={index + 1} />{label}</div>
      </div>)}
    </div>
    {refusal ? <Notice tone="danger" icon={ShieldAlert}>{refusal}</Notice> : null}

    {step === 1 ? <section>
      <div className="section-label" style={{ marginBottom: 10 }}>Pick a provider</div>
      <div className="preset-grid">
        {PRESETS.map((p) => <button type="button" key={p.id} className={`preset${p.id === presetId ? " is-selected" : ""}`} onClick={() => pickPreset(p)} aria-pressed={p.id === presetId}>
          <span className="preset__name">{p.name}</span><span className="preset__origin">{p.origin || "your-endpoint.example"}</span>
        </button>)}
      </div>
      <div className="preset__hint">{preset.hint}{preset.docsUrl ? <> <a href={preset.docsUrl} target="_blank" rel="noreferrer">Provider instructions</a></> : null}</div>
      <div className="row wrap" style={{ justifyContent: "flex-end", marginTop: 20 }}><Button variant="primary" onClick={() => { setFieldError(null); setStep(2); }}>Continue <ArrowRight aria-hidden="true" /></Button></div>
    </section> : null}

    {step === 2 ? <section>
      <div className="card card--pad" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <Field label="Account name" htmlFor="f-name" helper="Each account gets its own model family, even when names match."><Input id="f-name" large maxLength={128} value={name} disabled={oauthBusy} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Base URL" htmlFor="f-origin" helper={preset.id === "opencode-go" ? "OpenCode Go connects directly to this endpoint." : undefined}>
          <Input id="f-origin" large mono value={origin} readOnly={preset.id === "opencode-go" || credentialMode === "oauth"} disabled={oauthBusy} onChange={(e) => setOrigin(e.target.value)} />
        </Field>
        {preset.id === "custom" ? <Field label="API protocol"><Listbox label="API protocol" value={apiType} onChange={(v) => setApiType(v as ApiType)} groups={[{ label: "Protocols", options: [
          { value: "chat-completions", label: "OpenAI Chat Completions" }, { value: "responses", label: "OpenAI Responses" }, { value: "anthropic", label: "Anthropic Messages" },
        ] }]} /></Field> : null}
        <div className="credential-tabs" role="group" aria-label="Credential type">
          {credentialOptions.map((option) => <button key={option.id} type="button" aria-pressed={credentialMode === option.id} disabled={oauthBusy} className={credentialMode === option.id ? "is-active" : ""}
            onClick={() => { setCredentialMode(option.id); setFieldError(null); if (option.id === "oauth") { setOrigin(preset.origin); setApiType("responses"); } }}>{option.label}</button>)}
        </div>
        {credentialMode === "api-key" ? <Field label="API key" htmlFor="f-key" helper="Saved on this computer only after you add the account."><PasswordInput id="f-key" large value={secret} onChange={setSecret} placeholder={preset.id === "alibaba-token-plan" ? "sk-sp-…" : "Paste your key"} /></Field> : null}
        {credentialMode === "oauth" ? <div className="card card--pad oauth-panel" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p className="card__hint">Connect your ChatGPT/Codex subscription. Existing logins stay separate.</p>
          {credentialId ? <Notice tone="info" icon={Check}>Login ready. Add this account within 30 minutes.</Notice>
            : <Button variant="secondary" loading={oauthBusy} loadingLabel="Signing in…" disabled={oauthBusy} onClick={() => void beginOAuth()}>Start OpenAI sign-in</Button>}
          {oauthUrl ? <a href={oauthUrl} target="_blank" rel="noreferrer">Open the OpenAI authorization page</a> : null}
          {oauthSession ? <>
            <Field label="Authorization callback URL" htmlFor="oauth-callback" helper="After signing in, paste the complete localhost:1455 URL shown by your browser. A blank callback page is expected on a remote computer.">
              <Input id="oauth-callback" mono value={callbackUrl} disabled={oauthBusy} onChange={(e) => setCallbackUrl(e.target.value)} placeholder="http://localhost:1455/auth/callback?…" />
            </Field>
            <Button variant="secondary" disabled={oauthBusy || !callbackUrl.trim()} onClick={() => void finishOAuth()}>Complete OpenAI sign-in</Button>
          </> : null}
        </div> : null}
        {credentialMode === "file" ? <div className="credential-drop" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }} onDrop={(e) => { e.preventDefault(); void readCredentialFile(e.dataTransfer.files[0]); }}>
          <Field label="Saved login file" htmlFor="credential-file" helper="Drop or choose Codex auth.json or OpenCode auth.json (up to 256 KiB). Only your selected account is imported. OpenAI OAuth tokens need a direct OpenAI endpoint.">
            <Input id="credential-file" type="file" accept=".json,application/json" onChange={(e) => void readCredentialFile(e.target.files?.[0])} />
          </Field>
          {fileEntries.length > 1 ? <Field label="Account to import"><Listbox label="Account to import" value={fileEntryId} placeholder="Choose an account" emptyText="No matching accounts" onChange={setFileEntryId}
            groups={[{ label: "Saved accounts", options: fileEntries.map(entry => ({ value: entry.id, label: entry.label })) }]} /></Field> : null}
          {selectedEntry ? <Notice tone="info" icon={Check}>{selectedEntry.label} ready to import.</Notice> : null}
        </div> : null}
        {fieldError ? <Notice tone="warn" icon={TriangleAlert}>{fieldError}</Notice> : null}
      </div>
      <div className="row wrap" style={{ justifyContent: "space-between", marginTop: 20 }}>
        <Button variant="ghost" disabled={oauthBusy} onClick={() => setStep(1)}><ChevronLeft aria-hidden="true" />Back</Button>
        <Button variant="primary" disabled={oauthBusy} onClick={continueToReview}>Review <ArrowRight aria-hidden="true" /></Button>
      </div>
    </section> : null}

    {step === 3 ? <section>
      <div className="card card--pad" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="def-grid">
          <span className="k">Account</span><span className="v">{name}</span>
          <span className="k">Base URL</span><span className="v mono">{origin}</span>
          <span className="k">Models</span><span className="v">Fetch after adding</span>
          <span className="k">Credential</span><span className="v">{credentialMode === "oauth" ? "OpenAI login" : credentialMode === "file" ? selectedEntry?.label : "API key"}</span>
        </div>
        <Notice tone="info" icon={Info}>Adding this account preserves your selected model, mode, and bot settings.</Notice>
        <div className="row wrap" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <Button variant="ghost" disabled={busy} onClick={() => setStep(2)}><ChevronLeft aria-hidden="true" />Back</Button>
          <Button variant="primary-lg" loading={busy} loadingLabel="Adding account…" onClick={() => void addAccount()}>Add account</Button>
        </div>
      </div>
    </section> : null}
  </div>;
}
