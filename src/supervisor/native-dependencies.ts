import { createHash } from "node:crypto";
import { createRequire } from "node:module";

type Ast = { type: string; [key: string]: unknown };
// Retain only the last import list, never the large vendor AST or source.
let lastParsed: { sha256: string; imports: string[] } | undefined;

/** Resolve eager static native imports without loading or booting the vendor. */
export function checkNativeDependencies(source: string, hostMain: string): { ok: true } | { ok: false; reason: string } {
  try {
    const hash = createHash("sha256").update(source).digest("hex");
    const imports = new Set<string>(lastParsed?.sha256 === hash ? lastParsed.imports : []);
    const deferred = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression", "ClassDeclaration", "ClassExpression", "TryStatement"]);
    function visit(value: unknown): void {
      if (Array.isArray(value)) { for (const child of value) visit(child); return; }
      if (value === null || typeof value !== "object") return;
      const node = value as Ast;
      // Optional imports protected by try/catch and deferred callbacks are not
      // boot requirements. This intentionally makes no claim about them.
      if (deferred.has(node.type)) return;
      if (node.type === "CallExpression") {
        const callee = node.callee as Ast | undefined;
        const args = node.arguments as Ast[] | undefined;
        const first = args?.[0];
        if (callee?.type === "Identifier" && callee.name === "require" && args?.length === 1 &&
            first?.type === "Literal" && typeof first.value === "string" && !first.value.startsWith(".") && !first.value.startsWith("/")) {
          imports.add(first.value);
        }
      }
      for (const [key, child] of Object.entries(node)) if (key !== "start" && key !== "end") visit(child);
    }
    if (lastParsed?.sha256 !== hash) {
      // Portable installs may retain only Acorn's CommonJS distribution.
      // Load lazily: a missing parser must refuse Local verification without
      // preventing Official controls from starting on a minimal install.
      const { parse } = createRequire(import.meta.url)("acorn") as { parse: typeof import("acorn").parse };
      const root = parse(source, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true }) as unknown as Ast;
      visit(root);
      lastParsed = { sha256: hash, imports: [...imports] };
    }
    // Resolution stays live: installing/removing a dependency must be visible
    // immediately even when the pinned source digest has not changed.
    const require = createRequire(hostMain);
    const missing: string[] = [];
    for (const specifier of imports) {
      try { require.resolve(specifier); } catch { missing.push(specifier); }
    }
    if (missing.length) return { ok: false, reason: `Native host dependencies are missing: ${missing.sort().join(", ")}` };
    return { ok: true };
  } catch (error) {
    return { ok: false, reason: `Native dependency preflight failed: ${String(error)}` };
  }
}
