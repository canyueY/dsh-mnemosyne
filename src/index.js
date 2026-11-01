/**
 * dsh-mnemosyne — DSH Host Adapter for external Memory Service.
 * Replaces in-process A-MEM with subprocess + REST/MCP tools.
 * SPDX-License-Identifier: Apache-2.0
 */

import { pathToFileURL } from 'node:url';
import { MemoryClient } from './memory-client.js';
import { MemoryProcessHost } from './memory-process.js';
import { resolveConfigExport } from './config-schema.js';
import {
  assertRunnableConfig,
  resolvePluginConfig,
  toLoggableConfig,
  SETTINGS_NAMESPACE,
} from './settings.js';
import {
  contextToolSpec,
  feedbackToolSpec,
  forgetToolSpec,
  getToolSpec,
  healthToolSpec,
  observeToolSpec,
  proposeToolSpec,
  searchToolSpec,
  selectAnswerToolSpec,
  taskToolSpec,
  updateToolSpec,
} from './tool-spec.js';

export const name = 'dsh-mnemosyne';
// Only tools are mandatory. config/settings/agentPresets are optional services:
// reading them through ctx.get(...) must not block plugin activation when a host
// does not export the service (DSH 0.2.1 web does not provide a `config` service).
export const inject = ['tools'];

export { MemoryClient } from './memory-client.js';
export { MemoryProcessHost } from './memory-process.js';
export {
  SETTINGS_NAMESPACE,
  settingsDescribe,
  settingsUpdate,
  redactSecrets,
  assertRunnableConfig,
} from './settings.js';

/** Cordis Config — settings namespace id = entry/plugin name `dsh-mnemosyne`. */
export const Config = await resolveConfigExport();


async function loadDefineTool() {
  try {
    const mod = await import('@deepseek-ai/dsh-tools');
    if (mod?.defineTool) return mod.defineTool;
  } catch { /* fall through */ }
  const override = process.env.DSH_TOOLS_PATH;
  if (override) {
    const mod = await import(pathToFileURL(override).href);
    if (mod?.defineTool) return mod.defineTool;
  }
  throw new Error('cannot resolve @deepseek-ai/dsh-tools (set DSH_TOOLS_PATH for local dev)');
}

/** Flatten one user/assistant message content into plain text. */
function contentText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (part && typeof part === 'object') {
          const kind = String(part.type || '').toLowerCase();
          // Never persist hidden reasoning/thinking; only user-visible text.
          if (/reason|think/.test(kind)) return '';
          return part.text ?? part.content ?? '';
        }
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (content && typeof content === 'object') {
    return String(content.text ?? content.content ?? '');
  }
  return '';
}

export async function apply(ctx) {
  // Read config through ctx.get('config') / ctx.get('settings'); never access
  // ctx.config directly because DSH 0.2.1 treats it as an injected service and
  // a host without a `config` service would leave the plugin pending forever.
  const fiberCfg = null;
  const cfg = resolvePluginConfig(ctx, fiberCfg);
  const runnable = assertRunnableConfig({
    ...cfg,
    // env token satisfies remote mode when config token empty
    token: cfg.token || process.env.DSH_MEMORY_TOKEN || '',
  });
  if (!runnable.ok) {
    const err = new Error(`${runnable.error} [${runnable.code}] ${runnable.hint || ''}`);
    err.code = runnable.code;
    err.hint = runnable.hint;
    // never log raw token
    err.loggable_config = toLoggableConfig(cfg);
    throw err;
  }
  const defineTool = await loadDefineTool();
  const autoStart = cfg.auto_start !== false;

  // Optional: register page policy when settings service exists (does not require settings to run tools)
  try {
    const settings = typeof ctx?.get === 'function' ? ctx.get('settings') : ctx?.settings;
    if (settings && typeof settings.configure === 'function') {
      ctx.effect?.(settings.configure({ auto: true }, ctx.fiber));
    }
  } catch { /* settings optional */ }

  // Agent preset is registered declaratively by cordis.patch.yml as a real
  // @deepseek-ai/dsh-agent-preset row. Do not also register a descriptor at
  // runtime: the runtime registry expects {id, plugins} composition shape.

  const host = new MemoryProcessHost({
    serviceRoot: cfg.service_root,
    dataDir: cfg.data_dir,
    python: cfg.python,
    token: cfg.token || process.env.DSH_MEMORY_TOKEN,
  });

  let client = null;

  async function ensureClient() {
    if (client) return client;
    if (cfg.base_url && (cfg.token || process.env.DSH_MEMORY_TOKEN)) {
      client = new MemoryClient({
        baseUrl: cfg.base_url,
        token: cfg.token || process.env.DSH_MEMORY_TOKEN,
      });
      return client;
    }
    if (!autoStart) {
      const err = new Error('memory service not configured (set base_url+token or auto_start)');
      err.code = 'config_incomplete';
      err.loggable_config = toLoggableConfig(cfg);
      throw err;
    }
    const info = await host.start();
    client = new MemoryClient({ baseUrl: info.baseUrl, token: info.token });
    return client;
  }

  const tools = [
    defineTool({
      ...healthToolSpec(),
      async execute(_args, exec) {
        const c = await ensureClient();
        return c.health({ signal: exec?.signal });
      },
    }),
    defineTool({
      ...searchToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.search({
          query: args.query,
          limit: args.limit ?? 10,
          types: args.types,
        }, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...getToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.get(args.id, Boolean(args.include_evidence), { signal: exec?.signal });
      },
    }),
    defineTool({
      ...contextToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        const body = {
          query: args.query,
          token_budget: args.token_budget,
        };
        if (args.envelope && typeof args.envelope === 'object') body.envelope = args.envelope;
        if (args.has_accurate_tokenizer === true) body.has_accurate_tokenizer = true;
        return c.context(body, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...selectAnswerToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        const payload = {
          bank_id: args.bank_id || 'personal',
          query: args.query || '',
          mode: args.mode || 'current',
        };
        if (args.target && typeof args.target === 'object') payload.target = args.target;
        if (args.context && typeof args.context === 'object') payload.context = args.context;
        if (args.at) payload.at = args.at;
        if (args.budget && typeof args.budget === 'object') payload.budget = args.budget;
        // 宿主未提供 usage → 不伪造零费用；由 Node 记 host unknown。
        if (!args.host_usage) {
          payload.context = {
            ...(payload.context || {}),
            correlation_id: args.correlation_id || undefined,
            host_usage: null,
          };
        } else {
          payload.context = {
            ...(payload.context || {}),
            host_usage: args.host_usage,
            correlation_id: args.correlation_id || undefined,
          };
        }
        const selection = await c.selectAnswer(payload, { signal: exec?.signal });
        // 可选附带 search 候选：仅对照，不作事实权威
        if (args.include_search_candidates && selection.availability === 'available' && args.query) {
          try {
            const cand = await c.search({ query: args.query, limit: 3 }, { signal: exec?.signal });
            selection.search_candidates = {
              items: cand?.items || cand?.results || [],
              note: 'search candidates are not authoritative; selection service decides current answer',
            };
          } catch {
            selection.search_candidates = { unavailable: true };
          }
        }
        return selection;
      },
    }),
    defineTool({
      ...proposeToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.propose({
          content: args.content,
          type: args.type || 'semantic',
          source_ids: args.source_ids || [],
          status: args.status || 'candidate',
        }, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...feedbackToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.feedback(args, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...updateToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        const { id, ...rest } = args;
        return c.update(id, rest, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...forgetToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.forget(args.id, Boolean(args.hard), { signal: exec?.signal });
      },
    }),
    defineTool({
      ...taskToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.task(args.id, { signal: exec?.signal });
      },
    }),
    defineTool({
      ...observeToolSpec(),
      async execute(args, exec) {
        const c = await ensureClient();
        return c.observe({
          content: args.content,
          source_kind: args.source_kind || 'dialog',
          correlation_id: args.correlation_id,
          producer: args.producer || 'dsh.agent',
        }, { signal: exec?.signal });
      },
    }),
  ];

  // Host-level capture: write every user/assistant message into the memory
  // service without asking the model to call memory_observe. The model may
  // still call memory_observe for an extra semantic note; this hook is the
  // baseline that guarantees the turn is not lost.
  let offSession = null;
  let offFlush = null;
  const pendingCaptures = new Set();
  const latestAssistant = new Map();
  if (cfg.auto_capture !== false && typeof ctx.on === 'function') {
    offFlush = ctx.on('session/flush', async () => {
      // Durability checkpoint: do not let the process exit before the
      // fire-and-forget observation requests have reached the service.
      await Promise.allSettled([...pendingCaptures]);
    });
    offSession = ctx.on('session/event', (session, event) => {
      try {
        const type = event?.type;
        if (type !== 'user/message' && type !== 'assistant/message' && type !== 'turn/end') return;
        const sessionId = String(session?.id || session?.header?.id || '');
        if (!sessionId) return;
        const data = event.data || {};

        const capture = (role, content, seq, time, source) => {
          // Only user-visible message text — never hidden reasoning / tool traces.
          const text = contentText(content).trim();
          if (!text) return;
          const occurredAt = new Date(Number(time) || Date.now()).toISOString();
          const messageId = `dsh:${sessionId}:${Number(seq) || 0}`;
          const metadata = {
            session_id: sessionId,
            event_seq: Number(seq) || 0,
            role,
            source: source ?? null,
            visible_only: true,
          };
          const task = (async () => {
            const c = await ensureClient();
            // Session ledger first: stable-key dedupe; replay does not boost fact support.
            try {
              await c.recordSessionMessage({
                session_id: sessionId,
                bank_id: cfg.bank_id || 'personal',
                role,
                content: text,
                message_id: messageId,
                producer: 'dsh.host',
                metadata,
              });
            } catch (ledgerErr) {
              if (process.env.DSH_MEMORY_DEBUG) {
                process.stderr.write(`[mnemosyne] session ledger skipped: ${ledgerErr}\n`);
              }
            }
            await c.observe({
              content: text,
              source_kind: 'dialog',
              producer: 'dsh.host',
              correlation_id: sessionId,
              occurred_at: occurredAt,
              metadata,
              idempotency_key: messageId,
            });
          })().catch((err) => {
            if (process.env.DSH_MEMORY_DEBUG) {
              process.stderr.write(`[mnemosyne] auto-capture skipped: ${err}\n`);
            }
          });
          pendingCaptures.add(task);
          task.finally(() => pendingCaptures.delete(task));
        };

        if (type === 'user/message') {
          if (!data || data.role !== 'user') return;
          capture('user', data.content, event.seq, event.time, data.source);
          return;
        }
        if (type === 'assistant/message') {
          const message = data.message;
          if (!message || message.role !== 'assistant') return;
          // Keep only the last assistant message of the turn; intermediate
          // steps are tool-call/reasoning traffic. Persist at turn/end.
          latestAssistant.set(sessionId, {
            content: message.content,
            seq: event.seq,
            time: event.time,
            source: message.source,
          });
          return;
        }
        // turn/end: flush the final visible answer, not the thinking stream.
        const last = latestAssistant.get(sessionId);
        latestAssistant.delete(sessionId);
        if (last) {
          capture('assistant', last.content, last.seq, last.time, last.source);
        }
      } catch (err) {
        if (process.env.DSH_MEMORY_DEBUG) {
          process.stderr.write(`[mnemosyne] auto-capture ignored: ${err}\n`);
        }
      }
    });
  }

  const disposers = tools.map((t) => ctx.tools.register(t));
  if (offSession) disposers.push(offSession);
  if (offFlush) disposers.push(offFlush);
  return () => {
    for (const d of disposers.reverse()) d?.();
    host.stop();
  };
}
