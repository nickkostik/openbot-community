import type { FetchedModel } from "./provider-models.ts";
import type { CatalogModel } from "./model-catalog.ts";

export type CatalogMetadata = Pick<CatalogModel, "contextLength" | "maxOutputTokens" | "modalities" | "reasoning" | "providerKey" | "sourceUrl" | "outputModalities" | "apiType"> & { reasoningLevels?: readonly string[] };
export type ModelMetadataInput = {
  slug: string;
  apiType?: CatalogModel["apiType"];
  contextTokens: number;
  maxOutputTokens: number;
  modalities: readonly string[];
  reasoningLevels: readonly string[];
  catalogLimits?: { contextTokens: number | null; maxOutputTokens: number | null; sourceUrl: string; providerKey: string; outputModalities: readonly string[] };
};

/**
 * Build the `upsert-model` limits payload for an imported model, using
 * explicit provider limits first and route-scoped public metadata as fallback.
 * Bare `/v1/models` responses often omit capabilities entirely.
 *
 * Mapping rules (documented in docs/rebuild/PRD.md §8.1 / FR-53):
 * - contextTokens: provider value, else route catalog value, else omitted.
 * - maxOutputTokens: bounded agent budget; catalogLimits holds the published
 *   output maximum separately, including modern limits above 128k.
 * - modalities: the catalog's non-empty list, else the fetch list, else omitted.
 * - reasoningLevels:
 *   1. catalog `reasoningLevels` nonempty → use that ordered allow-list
 *   2. else fetched `reasoningLevels` nonempty → use Source A
 *   3. else catalog `reasoning === false` → `["default"]`
 *   4. else catalog `reasoning === true` → legacy `["default","none","high"]`
 *      (boolean-only / old cache)
 *   5. else omit (backend defaults)
 *   A boolean-only catalog must not wipe nonempty Source A levels.
 */

const LEGACY_CATALOG_REASONING: readonly string[] = ["default", "none", "high"];

function positive(value: number | null | undefined): number | undefined {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0 ? value : undefined;
}

function importReasoningLevels(
  fetched: FetchedModel,
  catalog?: CatalogMetadata,
): readonly string[] | undefined {
  if (catalog?.reasoningLevels !== undefined && catalog.reasoningLevels.length > 0) {
    return catalog.reasoningLevels;
  }
  if (fetched.reasoningLevels?.length) {
    return fetched.reasoningLevels;
  }
  if (catalog && catalog.reasoning === false) {
    return ["default"];
  }
  if (catalog && catalog.reasoning === true) {
    return LEGACY_CATALOG_REASONING;
  }
  return undefined;
}

export function modelImportFields(
  fetched: FetchedModel,
  catalog?: CatalogMetadata,
): {
  slug: string;
  apiType?: CatalogModel["apiType"];
  contextTokens?: number;
  maxOutputTokens?: number;
  reasoningLevels?: readonly string[];
  modalities?: readonly string[];
  catalogLimits?: ModelMetadataInput["catalogLimits"];
} {
  // A provider's explicit limits (including Codex's smaller context mode)
  // outrank the public catalog. Bare /models lists get route-scoped fallback.
  const contextTokens = positive(fetched.contextLength) ?? positive(catalog?.contextLength);
  const publishedOutput = positive(fetched.maxOutputTokens) ?? positive(catalog?.maxOutputTokens);
  const maxOutputTokens = publishedOutput ? Math.min(65536, publishedOutput) : undefined;
  const reasoningLevels = importReasoningLevels(fetched, catalog);

  const modalities = catalog?.modalities?.length
    ? catalog.modalities
    : fetched.modalities?.length
      ? fetched.modalities
      : undefined;

  return {
    slug: fetched.id,
    ...(catalog?.apiType ? { apiType: catalog.apiType } : {}),
    ...(contextTokens !== undefined ? { contextTokens } : {}),
    ...(maxOutputTokens !== undefined ? { maxOutputTokens } : {}),
    ...(reasoningLevels ? { reasoningLevels } : {}),
    ...(modalities ? { modalities } : {}),
    ...(catalog?.providerKey && catalog.sourceUrl ? { catalogLimits: {
      contextTokens: catalog.contextLength,
      maxOutputTokens: catalog.maxOutputTokens,
      providerKey: catalog.providerKey,
      sourceUrl: catalog.sourceUrl,
      outputModalities: catalog.outputModalities ?? ["text"],
    } } : {}),
  };
}

/** Refresh published facts without resetting a user's context/output caps. */
export function modelRefreshFields(model: ModelMetadataInput, catalog: CatalogMetadata): ReturnType<typeof modelImportFields> {
  const fields = modelImportFields({ id: model.slug, name: null, contextLength: null, maxOutputTokens: null,
    modalities: [...model.modalities], reasoningLevels: [...model.reasoningLevels] }, catalog);
  const derivedContext = model.contextTokens === 128000 || model.contextTokens === model.catalogLimits?.contextTokens;
  return { ...fields,
    ...(model.apiType ? { apiType: model.apiType } : {}),
    contextTokens: derivedContext ? fields.contextTokens ?? model.contextTokens : model.contextTokens,
    maxOutputTokens: catalog.maxOutputTokens && catalog.maxOutputTokens > 0
      ? Math.min(model.maxOutputTokens, catalog.maxOutputTokens) : model.maxOutputTokens,
  };
}
