/**
 * Standalone smoke test: boot the bundled Python Node, then exercise the same
 * REST paths the memory_* tools use. Run:
 *   node scripts/smoke.mjs [--keep] [--data-dir <path>]
 * By default a throwaway bank under the OS temp directory is used.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { MemoryProcessHost } from '../src/memory-process.js';
import { MemoryClient } from '../src/memory-client.js';

const args = process.argv.slice(2);
const keep = args.includes('--keep');
const dataIdx = args.indexOf('--data-dir');
const dataDir = dataIdx >= 0
  ? path.resolve(args[dataIdx + 1])
  : mkdtempSync(path.join(os.tmpdir(), 'mnemosyne-smoke-'));

const host = new MemoryProcessHost({ dataDir });
let info = null;
try {
  info = await host.start({ timeoutMs: 60000 });
  console.log('service:', info.baseUrl, '| pid', info.pid);
  const client = new MemoryClient({ baseUrl: info.baseUrl, token: info.token });

  const health = await client.health();
  console.log('health:', JSON.stringify(health));

  const observed = await client.observe({
    content: 'smoke test: standalone Mnemosyne node is reachable',
    source_kind: 'system',
    producer: 'mnemosyne-standalone-smoke',
  });
  console.log('observe:', JSON.stringify(observed).slice(0, 400));

  const search = await client.search({ query: 'smoke test', limit: 3 });
  const items = search?.items || search?.results || [];
  console.log('search hits:', Array.isArray(items) ? items.length : 'n/a');
  console.log('SMOKE_OK');
} catch (err) {
  console.error('SMOKE_FAILED:', err?.stack || err);
  process.exitCode = 1;
} finally {
  host.stop();
  if (!keep) {
    try { rmSync(dataDir, { recursive: true, force: true }); } catch { /* ignore */ }
  } else {
    console.log('kept data dir:', dataDir);
  }
}
