import { getModelCatalog } from "../api/client.ts";
import type { CatalogLookupModel, FetchedModel } from "../api/types";

export { modelImportFields, modelRefreshFields } from "../../../src/catalog/model-fields.ts";

/** Source B catalog enrichment for a fetched model list (badge + auto-fill metadata). */
export type CatalogEnrichment = {
  matched: Set<string>;
  lookup: Map<string, CatalogLookupModel>;
};

export async function enrichCatalogModels(fetched: FetchedModel[], providerId?: string): Promise<CatalogEnrichment> {
  const matched = new Set<string>();
  const lookup = new Map<string, CatalogLookupModel>();
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(6, fetched.length) }, async () => {
      while (next < fetched.length) {
      const m = fetched[next++]!;
      try {
        const c = await getModelCatalog(m.id, providerId);
        if (c.lookup?.found && c.lookup.model) {
          matched.add(m.id);
          lookup.set(m.id, c.lookup.model);
        }
      } catch {
        /* skip */
      }
      }
    }),
  );
  return { matched, lookup };
}
