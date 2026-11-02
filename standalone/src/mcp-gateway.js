/**
 * MCP stdio/in-process gateway for Mnemosyne memory tools.
 * Calls MemoryClient (REST) only — never opens SQLite.
 *
 * Tool wire names: mcp__mnemosyne__<name> (and bare memory_* aliases).
 *
 * Stdio entry: `node src/mcp-gateway.js`
 * Config: MNEMOSYNE_BASE_URL / MNEMOSYNE_TOKEN (or DSH_MEMORY_BASE_URL / DSH_MEMORY_TOKEN).
 */
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { MemoryClient } from './memory-client.js';

export const MCP_PROTOCOL_VERSION = '2025-06-18';
export const MCP_SERVER_NAME = 'mnemosyne';
export const MCP_TOOL_PREFIX = 'mcp__mnemosyne__';

const TOOL_DEFS = [
  { name: 'memory_health', rest: 'health', description: 'Node health' },
  { name: 'memory_search', rest: 'search', description: 'Search memories' },
  { name: 'memory_get', rest: 'get', description: 'Get memory by id' },
  { name: 'memory_context', rest: 'context', description: 'Build context envelope' },
  { name: 'memory_observe', rest: 'observe', description: 'Write observation' },
  { name: 'memory_propose', rest: 'propose', description: 'Propose memory' },
  { name: 'memory_feedback', rest: 'feedback', description: 'Feedback on memory' },
  { name: 'memory_update', rest: 'update', description: 'Update memory' },
  { name: 'memory_forget', rest: 'forget', description: 'Forget memory' },
  { name: 'memory_task_status', rest: 'task', description: 'Task status' },
];

function wireName(bare) {
  return `${MCP_TOOL_PREFIX}${bare.replace(/^memory_/, '')}`;
}

function normalizeToolName(name) {
  const n = String(name || '');
  if (n.startsWith(MCP_TOOL_PREFIX)) {
    const rest = n.slice(MCP_TOOL_PREFIX.length);
    return rest.startsWith('memory_') ? rest : `memory_${rest}`;
  }
  return n;
}

export function listMcpTools() {
  return TOOL_DEFS.map((t) => ({
    name: wireName(t.name),
    aliases: [t.name],
    description: t.description,
    inputSchema: { type: 'object', additionalProperties: true },
  }));
}

export class McpGateway {
  constructor({ client = null, baseUrl = null, token = null } = {}) {
    this.client = client;
    this.baseUrl = baseUrl;
    this.token = token;
    this.initialized = false;
    this._openedSqlite = false; // invariant: always false
  }

  ensureClient() {
    if (this.client) return this.client;
    if (!this.baseUrl || !this.token) {
      const err = new Error('MCP gateway not configured (base_url+token required)');
      err.rpcCode = -32600;
      throw err;
    }
    this.client = new MemoryClient({ baseUrl: this.baseUrl, token: this.token });
    return this.client;
  }

  async handle(request) {
    const rid = request?.id ?? null;
    const method = request?.method;
    const params = request?.params || {};
    try {
      if (method === 'initialize') {
        this.initialized = true;
        return {
          jsonrpc: '2.0',
          id: rid,
          result: {
            protocolVersion: MCP_PROTOCOL_VERSION,
            serverInfo: { name: MCP_SERVER_NAME, version: '2.0.0' },
            capabilities: { tools: {} },
          },
        };
      }
      if (method === 'ping') {
        return { jsonrpc: '2.0', id: rid, result: {} };
      }
      if (!this.initialized && method !== 'initialize') {
        return {
          jsonrpc: '2.0',
          id: rid,
          error: { code: -32600, message: 'not initialized; call initialize first' },
        };
      }
      if (method === 'tools/list') {
        return {
          jsonrpc: '2.0',
          id: rid,
          result: { tools: listMcpTools() },
        };
      }
      if (method === 'tools/call') {
        return {
          jsonrpc: '2.0',
          id: rid,
          result: await this.callTool(params?.name, params?.arguments || {}),
        };
      }
      return {
        jsonrpc: '2.0',
        id: rid,
        error: { code: -32601, message: `method not found: ${method}` },
      };
    } catch (err) {
      const code = err.rpcCode || -32000;
      return {
        jsonrpc: '2.0',
        id: rid,
        error: {
          code,
          message: String(err.message || err),
          data: { hint: err.hint || undefined },
        },
      };
    }
  }

  async callTool(name, args) {
    const bare = normalizeToolName(name);
    const def = TOOL_DEFS.find((t) => t.name === bare);
    if (!def) {
      const err = new Error(`unknown tool: ${name}`);
      err.rpcCode = -32601;
      throw err;
    }
    if (args == null || typeof args !== 'object' || Array.isArray(args)) {
      const err = new Error('invalid params: arguments must be object');
      err.rpcCode = -32602;
      throw err;
    }
    // Structural required checks
    if (bare === 'memory_search' && !args.query) {
      const err = new Error('invalid params: query required');
      err.rpcCode = -32602;
      throw err;
    }
    if (bare === 'memory_get' && !args.id) {
      const err = new Error('invalid params: id required');
      err.rpcCode = -32602;
      throw err;
    }
    const client = this.ensureClient();
    let data;
    switch (bare) {
      case 'memory_health':
        data = await client.health();
        break;
      case 'memory_search':
        data = await client.search({
          query: args.query,
          limit: args.limit,
          types: args.types,
        });
        break;
      case 'memory_get':
        data = await client.get(args.id, Boolean(args.include_evidence));
        break;
      case 'memory_context':
        data = await client.context({
          query: args.query,
          token_budget: args.token_budget,
        });
        break;
      case 'memory_observe':
        data = await client.observe(args);
        break;
      case 'memory_propose':
        data = await client.propose(args);
        break;
      case 'memory_feedback':
        data = await client.feedback({
          memory_id: args.memory_id || args.id,
          useful: args.useful,
          incorrect: args.incorrect,
          note: args.note || '',
        });
        break;
      case 'memory_update':
        data = await client.update(args.id, args);
        break;
      case 'memory_forget':
        data = await client.forget(args.id, Boolean(args.hard));
        break;
      case 'memory_task_status':
        data = await client.task(args.id);
        break;
      default: {
        const err = new Error(`unknown tool: ${name}`);
        err.rpcCode = -32601;
        throw err;
      }
    }
    // Never include sqlite paths/tokens in payload wrappers
    const safe = JSON.parse(JSON.stringify(data, (k, v) => {
      if (/token|password|secret|api_key/i.test(String(k))) return '********';
      if (typeof v === 'string' && /memory\.db|\\data\\|\/data\//i.test(v)) {
        return '[redacted-path]';
      }
      return v;
    }));
    return {
      content: [{ type: 'text', text: JSON.stringify(safe) }],
      structuredContent: safe,
      transport: 'rest',
      opened_sqlite: false,
    };
  }
}

function writeStdout(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

export async function runStdioServer(options = {}) {
  const baseUrl =
    options.baseUrl ||
    process.env.MNEMOSYNE_BASE_URL ||
    process.env.DSH_MEMORY_BASE_URL ||
    null;
  const token =
    options.token ||
    process.env.MNEMOSYNE_TOKEN ||
    process.env.DSH_MEMORY_TOKEN ||
    null;
  const client = options.client || null;
  const gateway = new McpGateway({ client, baseUrl, token });
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });

  for await (const line of rl) {
    const trimmed = String(line || '').trim();
    if (!trimmed) continue;
    let request;
    try {
      request = JSON.parse(trimmed);
    } catch (err) {
      writeStdout({
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: `parse error: ${err.message || err}` },
      });
      continue;
    }
    // Notifications (no id): accept initialized silently
    if (
      (request.id === undefined || request.id === null) &&
      request.method === 'notifications/initialized'
    ) {
      gateway.initialized = true;
      continue;
    }
    if (
      (request.id === undefined || request.id === null) &&
      request.method === 'initialized'
    ) {
      gateway.initialized = true;
      continue;
    }
    const response = await gateway.handle(request);
    if (request.id === undefined || request.id === null) {
      // notification — no response body required
      continue;
    }
    writeStdout(response);
  }
}

function isMainModule() {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return import.meta.url === pathToFileURL(entry).href;
  } catch {
    return false;
  }
}

if (isMainModule()) {
  runStdioServer().catch((err) => {
    writeStdout({
      jsonrpc: '2.0',
      id: null,
      error: { code: -32000, message: String(err?.message || err) },
    });
    process.exitCode = 1;
  });
}
