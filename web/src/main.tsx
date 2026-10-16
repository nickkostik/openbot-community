import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { appErrorMessage, createErrorLimiter, rejectionErrorMessage } from "./lib/fatal-errors";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles.css";
import "./log-pair.css";
// Per-page additions from the polish round. Each page agent appends only inside
// its own labelled section, so three parallel branches merge without touching a
// shared file.
import "./styles/ui-polish.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("OpenBot: missing root");
}

/**
 * Failsafe for async errors that never reach a component (unhandled promise
 * rejections, uncaught exceptions). React keeps rendering, so a plain console
 * log is enough to avoid a silent failure; we surface a minimal DOM toast so a
 * background failure is still visible without depending on the React tree.
 */
function installGlobalErrorHandlers(): void {
  const shouldShow = createErrorLimiter();
  const showFatalToast = (message: string): void => {
    if (!shouldShow(message, Date.now())) return;
    let stack = document.querySelector<HTMLElement>(".toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.className = "toast-stack";
      document.body.appendChild(stack);
    }
    const el = document.createElement("div");
    el.className = "toast toast--error";
    el.setAttribute("role", "status");
    el.innerHTML =
      '<span class="toast__icon"></span>' +
      '<div class="toast__body"><div class="toast__title">Unexpected error</div>' +
      '<div class="toast__msg"></div></div>' +
      '<button type="button" class="icon-btn toast__close" aria-label="Dismiss error">×</button>';
    const msg = el.querySelector(".toast__msg");
    if (msg) msg.textContent = message;
    el.querySelector("button")?.addEventListener("click", () => el.remove());
    while (stack.querySelectorAll(".toast--global").length >= 2) stack.querySelector(".toast--global")?.remove();
    el.classList.add("toast--global");
    stack.appendChild(el);
    window.setTimeout(() => el.remove(), 6000);
  };

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    console.error("OpenBot: unhandled promise rejection:", reason);
    const visible = rejectionErrorMessage(reason, window.location.origin);
    if (visible) showFatalToast(visible);
  });

  window.addEventListener("error", (event) => {
    const message = appErrorMessage(event, window.location.origin);
    console.error("OpenBot: uncaught error:", event.error ?? event.message);
    if (message) showFatalToast(message);
  });
}

installGlobalErrorHandlers();

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
