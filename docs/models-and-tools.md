# Models, reasoning, cache, and tools

[Home](../README.md) · [Agent install](agent-install.md) · [Installation](portable-installation.md) · [Troubleshooting](troubleshooting.md)

The model you select should be the model your next turn actually uses. Start with one account and one bot, prove the route, then add the rest.

## Add your accounts

Open the panel's setup flow, or **Models → Add provider** on an existing installation. Choose a preset, complete its credential method, and add the account. Then choose that family, use **Fetch models**, and **Import selected**.

| Preset | Prefilled base URL | Credential choice |
|---|---|---|
| NVIDIA | `https://integrate.api.nvidia.com/v1` | NVIDIA API key. |
| OpenCode Go | `https://opencode.ai/zen/go/v1` | Your Go or Go Plus key. |
| OpenAI | `https://api.openai.com/v1` | API key, OpenAI sign-in, or a supported saved-login import. OAuth uses the authenticated Codex adapter. |
| OpenRouter | `https://openrouter.ai/api/v1` | OpenRouter API key. |
| Alibaba Coding Plan | `https://coding-intl.dashscope.aliyuncs.com/v1` | The matching international Coding Plan key. |
| Z.AI Coding Plan | `https://api.z.ai/api/coding/paas/v4` | The matching GLM Coding Plan key. |
| Custom | Your provider's base URL | The credential and supported protocol for that endpoint. |

These are this fork's presets, not interchangeable billing endpoints. Use the provider's documentation for your account and region. Subscription/token-plan credentials do not become PAYG keys just because the model name matches.

Adding another account creates another independent family. For example, label two Go accounts **Go personal** and **Go work**; keep their keys, IDs, model lists, and usage distinct. Account creation does not switch the active model or merge an existing family.

OpenCode **Go** really does use `/zen/go/v1`. The `zen` path segment does not make it a Zen PAYG route. Go models can require Chat Completions, Responses, or Anthropic Messages individually; use the route's supported protocol rather than one protocol for the whole list. [OpenCode's direct Go endpoints](https://opencode.ai/docs/go/).

This fork uses direct Go connections. It does not add N5, MetaKey, or a new Zen PAYG account as a substitute.

## OpenAI API key or subscription login

An API key uses OpenAI Platform credits. OpenAI sign-in uses the supported subscription credential adapter and the account's available entitlements. The two quotas are separate. [Official OpenAI authentication guidance](https://learn.chatgpt.com/docs/auth).

Choose sign-in in the panel, complete the browser flow, and return its callback to that same setup session. The login remains staged until **Add account** succeeds. A restart can discard the pending login draft; sign in again rather than pasting tokens into a key field.

For a saved login, use the supported credential JSON import/drop control. Codex commonly stores `auth.json` under its configured home; a credential store can be used instead, so a file is not guaranteed to exist. Check `codex login status` to confirm the selected authentication method. Import only your own supported file, and never post its contents to chat, issues, or GitHub. [OpenAI login and storage guidance](https://learn.chatgpt.com/docs/auth).

OAuth routes use the authenticated Responses adapter and account-scoped refresh. API-key routes use their configured origin. A successful login or model listing is not proof that the account currently has quota for that model. Verify one completed inference turn.

## Global model versus a bot pin

On **Dashboard**, choose the custom model you want as the global default. On **Bots**, choose either **Default** or an explicit model for each bot.

| Bot setting | Next new turn |
|---|---|
| Default / no pin | Inherits the current global model. |
| Explicit model | Uses that pinned route even when the global default changes. |
| Paused | Is refused with a pause response until resumed. |

If a switch looks ignored, first inspect that bot's pin. Do not wipe all bot overrides to fix one inherited route. A running turn keeps the route it started with; test with a new message after the save completes.

The bot/model pair also remembers its reasoning choice. Switching to another model does not erase the saved effort for the previous one. Batch **Set reasoning…** lets you choose bots and models; only efforts supported by every selected model are offered.

| Reasoning choice | Meaning |
|---|---|
| Inherit model setting | Clear the bot-specific choice and use the route's saved effort. |
| Default | Omit an explicit effort and let the upstream choose. |
| Off | Explicitly disable reasoning, only where that route supports it. |
| Low, Medium, High, XHigh, Max | Send the supported value for that route. Availability varies. |

Default is not Off. A level supported by one provider's model is not automatically supported by another route with the same name. Keep the allow-list tied to the account, model, and protocol.

## Model discovery and limits

Provider discovery tells you what that account lists. The provider-scoped catalog enriches those rows with published limits and capabilities. Import the full selection you want; adding another account does not remove the old model list.

**Configured context** is the window OpenBot operates within. **Published context** is source metadata. Output caps are separate again. An advertised large window does not prove that your credential, protocol, or selected context mode accepts it.

Use a model's catalog refresh to update its metadata, and review an explicit protocol override when the provider reports a protocol mismatch. Refresh preserves intentional settings; it does not silently correct every explicit override or raise every output cap to the published maximum.

Automatic saved-provider discovery defaults to every six hours while the unified service is running. Inspect `/api/model-catalog` and its `autoRefresh` status for last attempt, completion, provider outcomes, and next due time. Scheduled discovery is additive; preserved routes or cached metadata are not proof that a removed provider model still accepts requests. The policy and exact preservation rules are in the [configuration reference](../skills/openbot-config/reference.md#automatic-saved-provider-refresh).

## Compaction

Compaction aims to trigger above **85%** estimated context and trim toward **35%**, keeping leading instructions and the latest complete message/tool group. Token estimates are not a provider tokenizer. Large tool schemas or a protected tool group can keep the result above the target.

The bot's selected model is the primary summarizer. A recognized context overflow can use a configured approved wider route: DeepSeek 4.1 Flash, GPT-6.1 Sol, or Muse 1.3 Contributor/Max. GPT/Muse fallback effort prefers supported Max, then XHigh. Each account keeps its own credentials and endpoint. Kimi K3 is excluded from compaction, including when it is selected for chat.

Authentication, quota, rate-limit, and transport errors do not silently switch the summarizer to another paid route. If no valid route fits, the job pauses and uses a lossy local fallback instead of pretending a full model summary succeeded.

Completed chunks and coverage checkpoints survive restarts. A changed covered prefix or incompatible route invalidates stale work; appending new history does not mean resummarizing everything. Compaction is still lossy. Preserve backups and check important task state after a long conversation.

For exact thresholds, summary budgets, approved routing, and checkpoint rules, read [context compaction](../skills/openbot-config/reference.md#context-compaction-routing-and-state). There is no separate UI switch that guarantees these percentages.

## Cache hits that mean something

Provider prompt caching and OpenBot's local summary reuse are different things. A reused summary is not itself a provider cache hit.

Stable instructions and tool definitions belong near the front of the prompt; changing their order or inserting new content into that prefix can reduce reuse. Provider cache behavior remains account-, model-, and service-dependent. [OpenAI prompt caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching).

OpenBot keeps main-chat and helper cache/session scope separate and preserves stable conversation/account identities. It does not rotate those identities to chase a prettier number or borrow another account's cache.

Use **Logs → Usage** for the actual time window and provider/model. Read both:

| Measure | Calculation |
|---|---|
| Cache hit rate | Provider-reported cached input ÷ input with a valid cache count. |
| Cache coverage | Input with a valid cache count ÷ all recorded input. |

An explicit zero is a measured miss. A missing count is unknown. A high hit rate with low coverage is incomplete evidence. Compare bot turns, auxiliary work, and compaction separately, and keep their costs in view.

A useful cache test keeps the same account, model, effort, conversation, instructions, and tool prefix for successive new turns. Record the first cold call and later appended turns, then compare provider counts. Use a harmless file-read task rather than repeated outbound actions. A tiny one-word prompt may be too short to qualify for caching.

That proves caching for the tested route. It does not prove a fleet-wide 90% rate or billing savings. Make the target a measured result over real traffic.

## MCP tools and ACP agents

MCP servers provide tools; ACP connections provide coding-agent sessions such as Codex or OpenCode. Their authentication and runtime dependencies stay owned by your Computer. OpenBot does not ship someone else's connected accounts or install every integration automatically.

After an update, check the native connection list, perform one harmless tool call, and make one agent session complete a small read-only task. For an ACP resume test, reconnect to the same saved thread and verify it retained context. Model visibility, a connected badge, or a successful version command does not prove a full tool/session result.

Use the normal native integration setup when a connection needs login. Do not change a chat provider key to repair unrelated MCP authentication. Keep connection errors, protocol errors, and model quota errors separate.

## Logs are evidence, not billing

Custom inference can produce an upstream record and a harness record for the same call. Usage excludes the harness companion. Retries, auxiliary requests, and compaction calls are still separate provider work.

Recording and body retention are configurable in **Settings**. Bodies can contain private chats and tool results; keep them off or errors-only unless you need more detail. Share safe trace IDs, model/account family labels, statuses, and timing instead of request bodies.

TPS uses measured output and generation time, not an average of inflated near-zero intervals. Reasoning tokens are part of output. Sampled totals are not exact billing, and HTTP 200 does not prove a stream completed or a user received a reply. [Usage accounting](../skills/openbot-config/reference.md#provider-usage-accounting-and-cache-verification).
