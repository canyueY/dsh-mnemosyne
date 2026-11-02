/**
 * Spawn / supervise the Python mnemosyne subprocess.
 * SPDX-License-Identifier: Apache-2.0
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function isServiceRoot(root) {
  return Boolean(root)
    && (fs.existsSync(path.join(root, 'src', 'mnemosyne', 'main.py'))
      || fs.existsSync(path.join(root, 'src', 'cognitive_memory', 'main.py')));
}

/** Walk candidates so DSH profile installs work without env/config. */
function resolveServiceRoot(explicit) {
  const tried = [];
  const push = (p) => {
    if (!p) return;
    const abs = path.resolve(p);
    if (!tried.includes(abs)) tried.push(abs);
  };

  if (explicit) push(explicit);
  if (process.env.DSH_MNEMOSYNE_ROOT) {
    // Legacy env var from dsh-cognitive-memory; kept for one migration cycle.
    push(process.env.DSH_MNEMOSYNE_ROOT);
  }

  // package root = …/dsh-mnemosyne (Host Adapter next to service/)
  push(path.join(__dirname, '..', 'service'));
  push(path.join(__dirname, '..', '..', 'service'));

  // walk up a few levels (node_modules nesting / profile copies)
  let cur = path.resolve(__dirname);
  for (let i = 0; i < 8; i += 1) {
    push(path.join(cur, 'service'));
    push(path.join(cur, 'services', 'mnemosyne'));
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }

  // monorepo fallback (desktop pet checkout)
  push(path.join(__dirname, '..', '..', '..', 'services', 'mnemosyne'));

  for (const root of tried) {
    if (isServiceRoot(root)) return root;
  }

  const hint = [
    'mnemosyne Python service not found.',
    'Tried:',
    ...tried.map((p) => `  - ${p}`),
    'Fix: keep repo `service/` next to the plugin, or set',
    '  DSH_MNEMOSYNE_ROOT / config.service_root',
    'then: pip install -e "<that-path>" (optional; PYTHONPATH also works).',
  ].join('\n');
  throw new Error(hint);
}

function defaultServiceRoot() {
  return resolveServiceRoot(undefined);
}

export { resolveServiceRoot, isServiceRoot };

function defaultDataDir() {
  const envDir = process.env.MNEMOSYNE_MEMORY_BANK
    || process.env.MNEMOSYNE_DATA_DIR
    || process.env.DSH_MEMORY_DATA_DIR
    || process.env.DSH_MNEMOSYNE_DATA_DIR;
  if (envDir) return envDir;
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), '.local', 'share');
  // Standalone profile-isolated bank. Deliberately NOT the desktop-pet bank:
  // one Node owns one Memory Bank, and the pet already owns its own.
  return path.join(local, 'Mnemosyne DSH Memory Bank');
}

function managedPython() {
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), '.local', 'share');
  const venv = process.platform === 'win32'
    ? path.join(local, 'Mnemosyne DSH Node', 'venv', 'Scripts', 'python.exe')
    : path.join(local, 'Mnemosyne DSH Node', 'venv', 'bin', 'python');
  return fs.existsSync(venv) ? venv : '';
}

function pythonBin() {
  return process.env.DSH_MEMORY_PYTHON
    || managedPython()
    || process.env.PYTHON
    || (process.platform === 'win32' ? 'python' : 'python3');
}

export class MemoryProcessHost {
  /**
   * @param {{ serviceRoot?: string, dataDir?: string, python?: string, token?: string }} [opts]
   */
  constructor(opts = {}) {
    // Defer resolve until start() so missing service fails with a full path list.
    this._serviceRootOpt = opts.serviceRoot || '';
    this.serviceRoot = this._serviceRootOpt
      ? path.resolve(this._serviceRootOpt)
      : '';
    this.dataDir = opts.dataDir || defaultDataDir();
    this.python = opts.python || pythonBin();
    this.token = opts.token || process.env.DSH_MEMORY_TOKEN || '';
    this.child = null;
    this.baseUrl = '';
    this.ready = false;
    this._starting = null;
    this._adopted = false;
  }

  serviceJsonPath() {
    return path.join(this.dataDir, 'runtime', 'service.json');
  }

  tokenPath() {
    return path.join(this.dataDir, 'runtime', 'token');
  }

  /**
   * Adopt a live node that already owns this bank (for example after a DSH
   * restart left the child running). Returns info() on success, null otherwise.
   */
  async _adopt(trace = () => {}) {
    try {
      const sj = this.serviceJsonPath();
      const tp = this.tokenPath();
      if (!fs.existsSync(sj) || !fs.existsSync(tp)) return null;
      const meta = JSON.parse(fs.readFileSync(sj, 'utf8'));
      if (!meta || !Number(meta.port)) return null;
      const token = fs.readFileSync(tp, 'utf8').trim();
      if (!token) return null;
      const { MemoryClient } = await import('./memory-client.js');
      const baseUrl = `http://${meta.host || '127.0.0.1'}:${meta.port}`;
      const client = new MemoryClient({ baseUrl, token, timeoutMs: 3000 });
      const health = await client.health();
      if (!health?.ok) return null;
      this.baseUrl = baseUrl;
      this.token = token;
      this.ready = true;
      this._adopted = true;
      trace(`adopted existing pid=${meta.pid} ${baseUrl}`);
      return this.info();
    } catch {
      return null;
    }
  }

  /**
   * Idempotent, concurrency-safe start. Tool calls and the session auto-capture
   * hook can race on the first message; they must share one child process.
   */
  start(opts = {}) {
    if (this.ready && this.baseUrl) return Promise.resolve(this.info());
    if (this.child && !this.child.killed) {
      // A child exists but has not reported ready yet: wait on the in-flight start.
      return this._starting || Promise.resolve(this.info());
    }
    if (this._starting) return this._starting;
    this._starting = this._start(opts).finally(() => { this._starting = null; });
    return this._starting;
  }

  async _start({ timeoutMs = 60000 } = {}) {
    if (this.ready && this.baseUrl) return this.info();

    this.serviceRoot = resolveServiceRoot(this._serviceRootOpt || undefined);

    if (!this.token) {
      this.token = Buffer.from(cryptoGetRandom()).toString('base64url');
    }
    fs.mkdirSync(path.join(this.dataDir, 'runtime'), { recursive: true });

    // Durable start trace: survives a host crash and makes spawn failures
    // diagnosable without DSH_MEMORY_DEBUG.
    const traceFile = path.join(this.dataDir, 'runtime', 'start.log');
    const trace = (line) => {
      try {
        fs.appendFileSync(traceFile, `[${new Date().toISOString()}] ${line}\n`, 'utf8');
      } catch { /* tracing must never break startup */ }
    };
    trace(`start python=${this.python} root=${this.serviceRoot} data=${this.dataDir} token=${this.token ? 'set' : 'unset'}`);

    // Reuse a healthy node left by a previous DSH process on this bank rather
    // than starting a second writer on the same SQLite database.
    const adopted = await this._adopt(trace);
    if (adopted) return adopted;

    // A previous DSH process may leave service.json pointing at a dead port.
    try { fs.rmSync(this.serviceJsonPath(), { force: true }); } catch { /* ignore */ }

    const env = {
      ...process.env,
      DSH_MEMORY_TOKEN: this.token,
      DSH_MEMORY_DATA_DIR: this.dataDir,
      DSH_MEMORY_HOST: '127.0.0.1',
      DSH_MEMORY_PORT: '0',
      PYTHONPATH: [
        path.join(this.serviceRoot, 'src'),
        process.env.PYTHONPATH || '',
      ].filter(Boolean).join(path.delimiter),
    };

    const mainPy = [
      path.join(this.serviceRoot, 'src', 'mnemosyne', 'main.py'),
      path.join(this.serviceRoot, 'src', 'cognitive_memory', 'main.py'),
    ].find((p) => fs.existsSync(p));
    if (!mainPy) throw new Error('mnemosyne Python entrypoint not found');
    trace(`spawn ${this.python} ${mainPy}`);
    this.child = spawn(this.python, [mainPy, '--host', '127.0.0.1', '--port', '0', '--data-dir', this.dataDir, '--token', this.token], {
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stderrTail = '';
    let spawnError = null;
    this.child.on('error', (err) => {
      spawnError = err;
      trace(`spawn-error code=${err?.code || ''} message=${err?.message || err}`);
    });
    this.child.stderr.on('data', (buf) => {
      const line = String(buf);
      stderrTail = `${stderrTail}${line}`.slice(-4000);
      trace(`stderr ${line.trimEnd()}`);
      if (process.env.DSH_MEMORY_DEBUG) process.stderr.write(`[mnemosyne] ${line}`);
    });
    this.child.stdout.on('data', (buf) => {
      const text = String(buf);
      const m = text.match(/DSH_MEMORY_BOUND\s+(\S+)/);
      if (m) {
        this.baseUrl = `http://${m[1]}`;
        this.ready = true;
        trace(`bound ${this.baseUrl}`);
      }
    });
    this.child.on('exit', (code, signal) => {
      trace(`exit code=${code} signal=${signal}`);
      this.ready = false;
      this.child = null;
    });

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (spawnError) break;
      if (this.ready && this.baseUrl) break;
      // also poll service.json
      try {
        const sj = this.serviceJsonPath();
        if (fs.existsSync(sj)) {
          const meta = JSON.parse(fs.readFileSync(sj, 'utf8'));
          if (meta.port) {
            this.baseUrl = `http://${meta.host || '127.0.0.1'}:${meta.port}`;
            this.ready = true;
            break;
          }
        }
      } catch { /* ignore */ }
      await sleep(100);
    }
    if (!this.ready) {
      this.stop();
      const detail = spawnError
        ? `spawn ${spawnError.code || ''}: ${spawnError.message}`
        : (stderrTail.trim().split(/\r?\n/).slice(-6).join(' | ') || 'no stderr; see runtime/start.log');
      trace(`failed ready=false detail=${detail.slice(0, 600)}`);
      throw new Error(`mnemosyne subprocess failed to become ready (${detail.slice(0, 800)})`);
    }
    // health check (the port may be bound before the app is ready)
    const { MemoryClient } = await import('./memory-client.js');
    const client = new MemoryClient({ baseUrl: this.baseUrl, token: this.token });
    let healthy = false;
    let lastErr = null;
    for (let i = 0; i < 20; i += 1) {
      try {
        await client.health();
        healthy = true;
        break;
      } catch (err) {
        lastErr = err;
        await sleep(100);
      }
    }
    if (!healthy) {
      this.stop();
      throw new Error(`mnemosyne health check failed: ${lastErr?.message || 'unknown'}`);
    }
    return this.info();
  }

  info() {
    return {
      ready: this.ready,
      baseUrl: this.baseUrl,
      token: this.token,
      dataDir: this.dataDir,
      pid: this.child?.pid || null,
      adopted: Boolean(this._adopted),
    };
  }

  stop() {
    // An adopted node belongs to the bank (possibly shared with another host
    // process); never kill it from this instance.
    if (!this._adopted && this.child && !this.child.killed) {
      try { this.child.kill(); } catch { /* ignore */ }
    }
    this.child = null;
    this.ready = false;
    this._adopted = false;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function cryptoGetRandom() {
  // eslint-disable-next-line no-undef
  return globalThis.crypto?.getRandomValues
    ? globalThis.crypto.getRandomValues(new Uint8Array(24))
    : Buffer.from(String(Date.now()) + Math.random());
}
