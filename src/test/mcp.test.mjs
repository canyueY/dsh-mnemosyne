/**
 * DA13–DA15 / FA11: MCP gateway over REST (no SQLite) + stdio entry.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { McpGateway, listMcpTools, MCP_TOOL_PREFIX } from '../mcp-gateway.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const gatewayEntry = join(root, 'src', 'mcp-gateway.js');

function mockClient(overrides = {}) {
  return {
    health: async () => ({ ok: true, service: 'cognitive-memory' }),
    search: async () => ({ count: 0, items: [] }),
    get: async () => ({ id: 'm1', content: 'x' }),
    context: async () => ({ blocks: [] }),
    observe: async () => ({ id: 'o1' }),
    propose: async () => ({ id: 'p1' }),
    feedback: async () => ({ ok: true }),
    update: async () => ({ id: 'm1', version: 2 }),
    forget: async () => ({ ok: true }),
    task: async () => ({ id: 't1', status: 'done' }),
    ...overrides,
  };
}

test('DA13: tools/list returns memory tools after initialize', async () => {
  const gw = new McpGateway({ client: mockClient() });
  const before = await gw.handle({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
  assert.equal(before.error.code, -32600);
  await gw.handle({ jsonrpc: '2.0', id: 2, method: 'initialize', params: {} });
  const listed = await gw.handle({ jsonrpc: '2.0', id: 3, method: 'tools/list' });
  assert.ok(listed.result.tools.length >= 8);
  const names = listed.result.tools.map((t) => t.name);
  assert.ok(names.some((n) => n.startsWith(MCP_TOOL_PREFIX)));
  assert.ok(listMcpTools().some((t) => t.aliases.includes('memory_search')));
});

test('DA14: tools/call success + invalid params -32602 + unknown -32601', async () => {
  const gw = new McpGateway({ client: mockClient() });
  await gw.handle({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} });
  const ok = await gw.handle({
    jsonrpc: '2.0',
    id: 2,
    method: 'tools/call',
    params: { name: 'mcp__mnemosyne__search', arguments: { query: 'hello' } },
  });
  assert.ok(ok.result);
  assert.equal(ok.result.opened_sqlite, false);
  assert.equal(ok.result.transport, 'rest');

  const badArgs = await gw.handle({
    jsonrpc: '2.0',
    id: 3,
    method: 'tools/call',
    params: { name: 'mcp__mnemosyne__search', arguments: {} },
  });
  assert.equal(badArgs.error.code, -32602);

  const unknown = await gw.handle({
    jsonrpc: '2.0',
    id: 4,
    method: 'tools/call',
    params: { name: 'mcp__mnemosyne__nope', arguments: {} },
  });
  assert.equal(unknown.error.code, -32601);
});

test('DA15: gateway uses injected REST client and never opens sqlite', async () => {
  let searched = false;
  const gw = new McpGateway({
    client: mockClient({
      search: async (q) => {
        searched = true;
        assert.equal(q.query, 'q');
        return { count: 1, items: [{ id: '1', content: 'a' }] };
      },
    }),
  });
  await gw.handle({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} });
  const res = await gw.handle({
    jsonrpc: '2.0',
    id: 2,
    method: 'tools/call',
    params: { name: 'memory_search', arguments: { query: 'q' } },
  });
  assert.equal(searched, true);
  assert.equal(gw._openedSqlite, false);
  assert.equal(res.result.opened_sqlite, false);
  const text = res.result.content[0].text;
  assert.ok(!/memory\.db/i.test(text));
});

test('FA11: stdio subprocess initialize/initialized/tools/list/tools/call', async () => {
  const child = spawn(process.execPath, [gatewayEntry], {
    cwd: root,
    env: { ...process.env },
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const lines = [];
  let buf = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', (chunk) => {
    buf += chunk;
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (line) lines.push(JSON.parse(line));
    }
  });

  const write = (obj) => {
    child.stdin.write(`${JSON.stringify(obj)}\n`);
  };

  write({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} });
  await new Promise((r) => setTimeout(r, 200));
  write({ jsonrpc: '2.0', method: 'notifications/initialized' });
  write({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
  await new Promise((r) => setTimeout(r, 200));
  // tools/call without client config → structured JSON-RPC error, still a response
  write({
    jsonrpc: '2.0',
    id: 3,
    method: 'tools/call',
    params: { name: 'mcp__mnemosyne__health', arguments: {} },
  });
  await new Promise((r) => setTimeout(r, 300));
  child.stdin.end();
  const exitCode = await new Promise((resolve) => {
    child.on('close', (code) => resolve(code ?? 0));
  });

  assert.equal(exitCode, 0);
  assert.ok(lines.some((x) => x.id === 1 && x.result?.serverInfo?.name === 'mnemosyne'));
  assert.ok(lines.some((x) => x.id === 2 && Array.isArray(x.result?.tools)));
  const call = lines.find((x) => x.id === 3);
  assert.ok(call);
  assert.ok(call.result || call.error);
});
