import { type Expose } from "../domain/types.ts";
import { boxFromSavedMode, customBoxFromCatalog } from "../parse/argv.ts";
import { wrapFromSource, type SupervisorDeps } from "./observe.ts";
import { catalogFromPlanJson } from "./plan.ts";
import { reconcile, type ReconcileOpts, type ReconcileResult } from "./reconcile.ts";
import { readExposeFile } from "./tunnel.ts";

/**
 * The custom guard checks that user-owned custom state is actually live and
 * repairs drift while custom is selected. A literal official mode is a user
 * selection, even when the saved custom catalog remains. Recovery must never
 * turn that selection back into custom. The parent schedules this guard;
 * there are no timers or daemons here.
 */

export type GuardDetail = "healthy" | "official" | "repaired" | "refused" | "no-custom-state";

export type GuardResult = {
  /** The mode file did not say custom and was rewritten to custom. */
  readonly modeRepaired: boolean;
  /** The host file was stock-unmarked and the wrap was reinstalled. */
  readonly wrapRepaired: boolean;
  /** Custom state is live after the guard ran. */
  readonly ok: boolean;
  readonly detail: GuardDetail;
  /** The repair reconcile, or undefined when no repair was needed or possible. */
  readonly reconcile: ReconcileResult | undefined;
};

export type GuardOpts = {
  /** Recorded on every audit line the repair reconcile writes. Default "guard". */
  readonly source?: string;
};

/** Missing/corrupt mode can recover from the catalog; explicit official cannot. */
function customBoxFromDisk(deps: SupervisorDeps, expose: Expose, mode: string | undefined) {
  const catalog = catalogFromPlanJson(deps.fs.read(deps.paths.plan));
  const box = boxFromSavedMode({ paths: deps.paths, mode, catalog, expose });
  if (box.kind === "custom") {
    return box;
  }
  if (catalog.models.length > 0) {
    return customBoxFromCatalog({ paths: deps.paths, catalog, expose });
  }
  return undefined;
}

function officialSelection(): GuardResult {
  return { modeRepaired: false, wrapRepaired: false, ok: true, detail: "official", reconcile: undefined };
}

export async function guardCustom(deps: SupervisorDeps, opts: GuardOpts = {}): Promise<GuardResult> {
  const mode = deps.fs.read(deps.paths.mode);
  if (mode?.trim() === "official") {
    return officialSelection();
  }
  const desired = customBoxFromDisk(deps, readExposeFile(deps.fs, deps.paths.expose), mode);
  if (desired === undefined) {
    return { modeRepaired: false, wrapRepaired: false, ok: false, detail: "no-custom-state", reconcile: undefined };
  }
  const modeDrift = deps.fs.read(deps.paths.mode)?.trim() !== "custom";
  const wrapDrift = wrapFromSource(deps.fs.read(deps.paths.hostMain)).kind === "stock-unmarked";
  if (!modeDrift && !wrapDrift) {
    return { modeRepaired: false, wrapRepaired: false, ok: true, detail: "healthy", reconcile: undefined };
  }
  // keepGuard: a repair always changes the wrap bytes, and the pid in the
  // guard pidfile is the daemon (or the one-shot tick's sibling daemon) that
  // must survive it. Only an install/update intentionally replaces the
  // daemon, and install.sh restarts it from the new tree.
  const repairOpts: ReconcileOpts = { source: opts.source ?? "guard", keepGuard: true, expectedMode: mode };
  const result = await reconcile(desired, deps, repairOpts);
  if (result.kind === "refused") {
    if (result.error.kind === "mode-changed" && deps.fs.read(deps.paths.mode)?.trim() === "official") {
      return officialSelection();
    }
    return { modeRepaired: false, wrapRepaired: false, ok: false, detail: "refused", reconcile: result };
  }
  return { modeRepaired: modeDrift, wrapRepaired: wrapDrift, ok: true, detail: "repaired", reconcile: result };
}
