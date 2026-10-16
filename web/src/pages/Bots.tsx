import { useEffect, useState } from "react";
import { Bot as BotIcon, Check, Minus } from "lucide-react";
import { getBotModels, getBots, getPauseBots, setBotModel, setBotModels, setBotReasoning, setPauseBot, setPauseBots } from "../api/client";
import type { BotInfo, BotModels, ReasoningLevel } from "../api/types";
import { useApp, useBoxState } from "../store";
import { Badge, Button, EmptyState, Spinner, Switch } from "../components/ui";
import { Modal } from "../components/overlays";
import { Listbox } from "../components/Listbox";
import { modelGroupsForState } from "../lib/model-options";
import { computePauseIds, computeResumeIds } from "../lib/batch-pause";
import { botModel, reasoningOptions } from "../lib/bot-reasoning";

function formatBotTime(value: number | null): string {
  if (value === null) return "Time unavailable";
  const age = Math.max(0, Date.now() - value);
  if (age < 60 * 60 * 1000) return `Updated ${Math.max(1, Math.round(age / 60000))}m ago`;
  if (age < 24 * 60 * 60 * 1000) return `Updated ${Math.round(age / 3600000)}h ago`;
  return `Updated ${new Date(value).toLocaleDateString()}`;
}

export function Bots() {
  const state = useBoxState();
  const { pushToast } = useApp();
  const [bots, setBots] = useState<BotInfo[]>([]);
  const [pausedBotIds, setPausedBotIds] = useState<Set<string>>(() => new Set());
  const [botModels, setBotModelsState] = useState<BotModels | null>(null);
  const [modelDraft, setModelDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyBotId, setBusyBotId] = useState<string | null>(null);
  const [modelBusyBotId, setModelBusyBotId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchModel, setBatchModel] = useState("");
  const [reasoningBusyBotId, setReasoningBusyBotId] = useState<string | null>(null);
  const [reasoningDialog, setReasoningDialog] = useState(false);
  const [reasoningModelIds, setReasoningModelIds] = useState<Set<string>>(() => new Set());
  const [batchEffort, setBatchEffort] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const settingsBusy = batchBusy || modelBusyBotId !== null || reasoningBusyBotId !== null;

  useEffect(() => {
    let alive = true;
    Promise.all([getBots(), getPauseBots(), getBotModels()])
      .then(([nextBots, pauseState, nextBotModels]) => {
        if (!alive) return;
        setBots(nextBots);
        setPausedBotIds(new Set(pauseState.pausedBotIds));
        setBotModelsState(nextBotModels);
        setModelDraft(nextBotModels.assignments);
      })
      .catch(() => {
        if (alive) pushToast("error", "Bots failed to load", "Could not reach the bot pause endpoint.");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [pushToast]);

  const toggle = async (bot: BotInfo, paused: boolean) => {
    if (busyBotId) return;
    setBusyBotId(bot.botId);
    const before = pausedBotIds;
    setPausedBotIds((current) => {
      const next = new Set(current);
      if (paused) next.add(bot.botId);
      else next.delete(bot.botId);
      return next;
    });
    try {
      const next = await setPauseBot(bot.botId, paused);
      setPausedBotIds(new Set(next.pausedBotIds));
      pushToast("success", paused ? "Bot paused" : "Bot resumed", bot.botName);
    } catch {
      setPausedBotIds(before);
      pushToast("error", "Pause switch failed", "Could not save this bot's pause state.");
    } finally {
      setBusyBotId(null);
    }
  };

  const saveModel = async (bot: BotInfo, modelId: string) => {
    if (settingsBusy) return;
    setModelBusyBotId(bot.botId);
    setModelDraft((current) => ({ ...current, [bot.botId]: modelId }));
    try {
      const next = await setBotModel(bot.botId, modelId || null);
      setBotModelsState(next);
      setModelDraft(next.assignments);
      pushToast(
        "success",
        modelId ? "Bot model saved" : "Bot model cleared",
        modelId ? bot.botName + " uses the selected model on its next message." : bot.botName + " is back on the global model.",
      );
    } catch (err) {
      setModelDraft(botModels?.assignments ?? {});
      pushToast("error", "Bot model save failed", err instanceof Error ? err.message : "Could not save this bot's model override.");
    } finally {
      setModelBusyBotId(null);
    }
  };

  const visibleBots = bots.filter((bot) => !bot.hidden);
  const activeBots = visibleBots.filter((bot) => !bot.deleted);
  const selectedActiveIds = activeBots.filter((bot) => selectedIds.has(bot.botId)).map((bot) => bot.botId);
  const allSelected = activeBots.length > 0 && selectedActiveIds.length === activeBots.length;
  const toggleSelected = (botId: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(botId)) next.delete(botId); else next.add(botId);
    return next;
  });
  const toggleAll = () => setSelectedIds(allSelected ? new Set() : new Set(activeBots.map((bot) => bot.botId)));

  const saveReasoning = async (bot: BotInfo, modelId: string, effort: string) => {
    if (settingsBusy) return;
    setReasoningBusyBotId(bot.botId);
    try {
      const next = await setBotReasoning([bot.botId], [modelId], effort ? effort as ReasoningLevel : null);
      setBotModelsState(next);
      setModelDraft(next.assignments);
      pushToast("success", "Bot reasoning saved", `${bot.botName}'s choice applies on its next message.`);
    } catch (err) {
      pushToast("error", "Bot reasoning save failed", err instanceof Error ? err.message : "Could not save reasoning.");
    } finally { setReasoningBusyBotId(null); }
  };
  const openReasoningDialog = () => {
    setReasoningModelIds(new Set(selectedActiveIds.map((id) => botModel(state, modelDraft[id])?.id).filter((id): id is string => Boolean(id))));
    setBatchEffort(""); setModelSearch(""); setReasoningDialog(true);
  };
  const batchReasoningModels = state.models.filter((model) => reasoningModelIds.has(model.id));
  const batchReasoningOptions = reasoningOptions(batchReasoningModels);
  const applyReasoning = async () => {
    if (settingsBusy || !selectedActiveIds.length || !batchReasoningModels.length) return;
    setBatchBusy(true);
    try {
      const next = await setBotReasoning(selectedActiveIds, batchReasoningModels.map((model) => model.id), batchEffort ? batchEffort as ReasoningLevel : null);
      setBotModelsState(next); setModelDraft(next.assignments); setReasoningDialog(false);
      pushToast("success", "Reasoning updated", `Saved for ${selectedActiveIds.length} bots and ${batchReasoningModels.length} models.`);
    } catch (err) {
      pushToast("error", "Reasoning update failed", err instanceof Error ? err.message : "Could not save reasoning.");
    } finally { setBatchBusy(false); }
  };

  const runBatch = async (kind: "model" | "pause" | "resume") => {
    if (settingsBusy || selectedActiveIds.length === 0) return;
    setBatchBusy(true);
    try {
      if (kind === "model") {
        const next = await setBotModels(selectedActiveIds, batchModel || null);
        setBotModelsState(next);
        setModelDraft(next.assignments);
        pushToast("success", "Models updated", `Updated ${selectedActiveIds.length} bot${selectedActiveIds.length === 1 ? "" : "s"}.`);
      } else {
        // PUT /api/pause-bots replaces the whole list, so compute the new list
        // from the latest server state, not React state — otherwise a pause
        // added by another client between loads would be dropped.
        const latest = await getPauseBots();
        const nextIds = kind === "pause"
          ? computePauseIds(latest.pausedBotIds, selectedActiveIds)
          : computeResumeIds(latest.pausedBotIds, selectedActiveIds);
        const next = await setPauseBots(nextIds);
        setPausedBotIds(new Set(next.pausedBotIds));
        pushToast("success", kind === "pause" ? "Bots paused" : "Bots resumed", `Updated ${selectedActiveIds.length} bot${selectedActiveIds.length === 1 ? "" : "s"}.`);
      }
    } catch (err) {
      pushToast("error", "Batch update failed", err instanceof Error ? err.message : "Some bots could not be updated.");
    } finally {
      setBatchBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="page-title-row">
        <h1>Bots</h1>
        <span className="sub">Choose a model and reasoning per bot. Each model remembers its own effort.</span>
      </div>

      <section className="card" aria-labelledby="bots-heading">
        <div className="card__head">
          <div className="card__head-main">
            <span className="card__label" id="bots-heading">Bot access</span>
            <p className="card__hint">Model and reasoning changes apply on the next message. Model setting inherits the route's saved effort.</p>
          </div>
          <div className="row gap-2">
            {!loading && activeBots.length > 0 ? (
              <label className="bot-select-all">
                <input type="checkbox" aria-label="Select all active bots" checked={allSelected} onChange={toggleAll} />
                <span className="checkbox__box">{allSelected ? <Check aria-hidden="true" /> : selectedActiveIds.length > 0 ? <Minus aria-hidden="true" /> : null}</span>
                <span className="label">Select all</span>
              </label>
            ) : null}
            {loading ? <Spinner /> : <Badge>{activeBots.length} {activeBots.length === 1 ? "bot" : "bots"}</Badge>}
          </div>
        </div>

        {loading ? (
          <div className="card__body stack" aria-busy="true">
            <div className="skel skel--row" />
            <div className="skel skel--row" />
            <div className="skel skel--row" />
          </div>
        ) : visibleBots.length === 0 ? (
          <div className="card__body">
            <EmptyState
              icon={BotIcon}
              title="No bots found"
              body="Create a bot profile on the Computer and it will appear here."
            />
          </div>
        ) : (
          <>
          {selectedActiveIds.length > 0 ? (
            <div className="bot-batch-bar bot-batch-bar--grouped" role="region" aria-label="Batch actions" aria-live="polite">
              <span className="bot-batch-count">{selectedActiveIds.length} selected</span>
              <span className="bot-batch-actions">
                <span className="bot-batch-group">
                  <Listbox label="Set model" groups={[{ label: "Global", options: [{ value: "", label: "Global model" }] }, ...modelGroupsForState(state, state.models.filter((model) => botModels?.available.includes(model.id)))]} value={batchModel} placeholder="Set model" disabled={settingsBusy || botModels === null} onChange={setBatchModel} />
                  <button className="btn btn--secondary" type="button" disabled={settingsBusy || botModels === null} onClick={() => void runBatch("model")}>{batchBusy ? "Applying..." : "Apply model"}</button>
                  <button className="btn btn--secondary" type="button" disabled={settingsBusy || botModels === null} onClick={openReasoningDialog}>Set reasoning…</button>
                </span>
                <span className="bot-batch-sep" aria-hidden="true" />
                <span className="bot-batch-group">
                  <button className="btn btn--secondary" type="button" disabled={settingsBusy} onClick={() => void runBatch("resume")}>Resume</button>
                  <button className="btn btn--secondary" type="button" disabled={settingsBusy} onClick={() => void runBatch("pause")}>Pause</button>
                </span>
              </span>
            </div>
          ) : null}
          <div className="bot-list">
            {visibleBots.map((bot) => {
              const paused = pausedBotIds.has(bot.botId);
              const busy = busyBotId === bot.botId;
              const model = botModel(state, modelDraft[bot.botId]);
              const savedEffort = model ? botModels?.reasoning?.[bot.botId]?.[model.id] : undefined;
              const effort = savedEffort && model?.reasoningLevels.includes(savedEffort) ? savedEffort : "";
              return (
                <div className={"bot-row bot-row--dense" + (bot.deleted ? " is-deleted" : "")} key={bot.botId} title={bot.deleted ? "Deleted" : undefined}>
                  <label className="checkbox"><input type="checkbox" aria-label={`Select ${bot.botName}`} checked={selectedIds.has(bot.botId)} disabled={bot.deleted || batchBusy} onChange={() => toggleSelected(bot.botId)} /><span className="checkbox__box">{selectedIds.has(bot.botId) ? <Check aria-hidden="true" /> : null}</span></label>
                  <span className="bot-row__icon" aria-hidden="true"><BotIcon /></span>
                  <div className="bot-row__main">
                    <strong>{bot.botName}</strong>
                    <span className="bot-row__id">{bot.botId}</span>
                    <span className="bot-row__time">{bot.deleted ? "Deleted" : formatBotTime(bot.updatedAtMs)}</span>
                  </div>
                  <div className="bot-row__controls">
                  <div className="bot-row__model">
                    <span className="bot-control-label">Model</span>
                    <Listbox
                      label={"Model for " + bot.botName}
                      groups={[{ label: "Global", options: [{ value: "", label: "Default" }] }, ...modelGroupsForState(state, state.models.filter((model) => botModels?.available.includes(model.id)))]}
                      value={modelDraft[bot.botId] ?? ""}
                      disabled={bot.deleted || settingsBusy || botModels === null}
                      onChange={(id) => void saveModel(bot, id)}
                    />
                  </div>
                  <div className="bot-row__reasoning">
                    <span className="bot-control-label">Reasoning</span>
                    <Listbox label={"Reasoning for " + bot.botName} groups={[{ label: "Reasoning", options: reasoningOptions(model ? [model] : []) }]} value={effort}
                      disabled={bot.deleted || settingsBusy || botModels === null || !model}
                      onChange={(value) => { if (model) void saveReasoning(bot, model.id, value); }} />
                  </div>
                  </div>
                  <Switch
                    checked={!paused}
                    disabled={bot.deleted || busy}
                    label={busy ? "Saving" : paused ? "Paused" : "Active"}
                    onChange={(next) => void toggle(bot, !next)}
                  />
                </div>
              );
            })}
          </div>
          </>
        )}
      </section>
      <Modal open={reasoningDialog} onClose={() => { if (!batchBusy) setReasoningDialog(false); }} labelledBy="batch-reasoning-title" dismissable={!batchBusy}>
        <div className="dialog dialog--wide bot-reasoning-modal">
          <div className="card__head"><div className="card__head-main"><h2 id="batch-reasoning-title">Reasoning for {selectedActiveIds.length} selected bots</h2>
            <p className="card__hint">Choose one or more models. This keeps each bot's current model and saves effort for the selected routes.</p></div></div>
          <div className="card__body stack">
            <input className="input" type="search" aria-label="Search reasoning models" placeholder="Search models or providers" value={modelSearch} onChange={(event) => setModelSearch(event.target.value)} disabled={batchBusy} />
            <div className="bot-reasoning-models" role="group" aria-label="Models for reasoning">
              {modelGroupsForState(state, state.models.filter((model) => botModels?.available.includes(model.id))).map((group) => {
                const options = group.options.filter((option) => `${option.label} ${group.label}`.toLowerCase().includes(modelSearch.toLowerCase()));
                return options.length ? <div key={group.label}><strong>{group.label}</strong>{options.map((option) => <label key={option.value}>
                  <input type="checkbox" aria-label={`Reasoning model ${option.label} (${group.label})`} checked={reasoningModelIds.has(option.value)} disabled={batchBusy} onChange={() => {
                    setReasoningModelIds((current) => { const next = new Set(current); if (next.has(option.value)) next.delete(option.value); else next.add(option.value); return next; });
                    setBatchEffort("");
                  }} /><span>{option.label}</span></label>)}</div> : null;
              })}
            </div>
            <span className="card__hint">{batchReasoningModels.length} models selected. Only efforts supported by every selected model are offered.</span>
            <Listbox label="Reasoning for selected models" groups={[{ label: "Reasoning", options: batchReasoningOptions }]} value={batchEffort} disabled={batchBusy || !batchReasoningModels.length} onChange={setBatchEffort} />
            <div className="row" style={{ justifyContent: "flex-end" }}><Button variant="secondary" disabled={batchBusy} onClick={() => setReasoningDialog(false)}>Cancel</Button>
              <Button variant="primary" loading={batchBusy} disabled={batchBusy || !batchReasoningModels.length || !selectedActiveIds.length} onClick={() => void applyReasoning()}>Save reasoning</Button></div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
