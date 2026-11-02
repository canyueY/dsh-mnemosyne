/**
 * Mnemosyne agent preset loader + fail-closed tool gate (DA11/DA12 / FA07–FA08).
 * Session create/list must go through a real host registry — never invent session_id.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
export const PRESET_PATH = join(ROOT, 'dsh', 'agent-presets', 'mnemosyne', 'agent.cordis.yml');
export const PRESET_ID = 'mnemosyne';
export const PRESET_INSERT_ID = 'dsh-mnemosyne-agent-preset';

function parseSimpleYaml(text) {
  // Minimal YAML subset parser for our preset file (no dependencies).
  const lines = String(text).split(/\r?\n/);
  const out = {
    id: null,
    name: null,
    description: '',
    persona: '',
    tools: { allow: [], deny_prefixes: [], deny_exact: [], fail_closed: true },
    session: { creatable: true, default: false },
  };
  let mode = null;
  let personaLines = [];
  let inPersona = false;
  for (const raw of lines) {
    const line = raw.replace(/\t/g, '  ');
    if (/^\s*#/.test(line) || !line.trim()) continue;
    if (/^persona:\s*\|/.test(line)) {
      inPersona = true;
      mode = null;
      continue;
    }
    if (inPersona) {
      if (/^[a-zA-Z_]/.test(line)) {
        inPersona = false;
        out.persona = personaLines.join('\n').trim();
      } else {
        personaLines.push(line.replace(/^ {2}/, ''));
        continue;
      }
    }
    const kv = line.match(/^([a-zA-Z0-9_]+):\s*(.*)$/);
    if (kv && !line.startsWith(' ')) {
      mode = null;
      const key = kv[1];
      let val = kv[2];
      if (val === '>' || val === '|') continue;
      if (key === 'id' || key === 'name') out[key] = val.replace(/^['"]|['"]$/g, '');
      if (key === 'description') out.description = val.replace(/^['"]|['"]$/g, '');
      if (key === 'tools') mode = 'tools';
      if (key === 'session') mode = 'session';
      continue;
    }
    if (mode === 'tools' || String(mode || '').startsWith('tools.')) {
      if (/^\s+allow:/.test(line)) {
        mode = 'tools.allow';
        continue;
      }
      if (/^\s+deny_prefixes:/.test(line)) {
        mode = 'tools.deny_prefixes';
        continue;
      }
      if (/^\s+deny_exact:/.test(line)) {
        mode = 'tools.deny_exact';
        continue;
      }
      if (/^\s+fail_closed:\s*(true|false)/.test(line)) {
        out.tools.fail_closed = /true/.test(line);
        continue;
      }
    }
    if (mode === 'session') {
      if (/^\s+creatable:\s*(true|false)/.test(line)) {
        out.session.creatable = /true/.test(line);
        continue;
      }
      if (/^\s+default:\s*(true|false)/.test(line)) {
        out.session.default = /true/.test(line);
        continue;
      }
    }
    const item = line.match(/^\s+-\s+(.+)$/);
    if (item) {
      const v = item[1].trim().replace(/^['"]|['"]$/g, '');
      if (mode === 'tools.allow') out.tools.allow.push(v);
      if (mode === 'tools.deny_prefixes') out.tools.deny_prefixes.push(v);
      if (mode === 'tools.deny_exact') out.tools.deny_exact.push(v);
    }
  }
  if (personaLines.length && !out.persona) out.persona = personaLines.join('\n').trim();
  return out;
}

export function loadMnemosynePreset() {
  const text = readFileSync(PRESET_PATH, 'utf8');
  const preset = parseSimpleYaml(text);
  if (preset.id !== PRESET_ID) {
    throw new Error('preset id must be mnemosyne');
  }
  return { ...preset, path: PRESET_PATH, insert_id: PRESET_INSERT_ID };
}

/** Descriptor published to a real host agentPresets registry. */
export function mnemosynePresetDescriptor(preset = null) {
  const p = preset || loadMnemosynePreset();
  return {
    id: p.id,
    name: p.name,
    description: p.description,
    persona: p.persona,
    tools: p.tools,
    session: p.session,
    session_creatable: Boolean(p.session?.creatable),
    source: 'dsh/agent-presets/mnemosyne',
    path: p.path,
    insert_id: PRESET_INSERT_ID,
  };
}

/**
 * Register with a host-side agentPresets registry (real DSH surface).
 * registry must expose register(descriptor) and list().
 */
export function registerMnemosynePreset(registry, preset = null) {
  if (!registry || typeof registry.register !== 'function') {
    return {
      ok: false,
      code: 'host_registry_unavailable',
      error: 'agentPresets registry missing register()',
      hint: 'Mount via cordis insert dsh-mnemosyne-agent-preset on a real DSH host',
    };
  }
  const descriptor = mnemosynePresetDescriptor(preset);
  registry.register(descriptor);
  return { ok: true, preset_id: descriptor.id, insert_id: PRESET_INSERT_ID };
}

/**
 * List presets via host agentPresets/list contract.
 * Does not invent a local success list when host is absent.
 */
export function listAgentPresets(host = null) {
  if (!host || typeof host.list !== 'function') {
    return {
      ok: false,
      code: 'host_agent_presets_unavailable',
      error: 'real agentPresets/list host required',
      hint: 'Inject host with list() from DSH agentPresets service; do not use simulated lists',
      presets: [],
    };
  }
  const listed = host.list() || [];
  return { ok: true, presets: Array.isArray(listed) ? listed : [] };
}

/**
 * session/create through host — never mint sess_* locally.
 */
export function createSessionWithPreset(presetId, hostOrOpts = null, maybeOpts = {}) {
  const host =
    hostOrOpts && typeof hostOrOpts.createSession === 'function'
      ? hostOrOpts
      : hostOrOpts && hostOrOpts.host
        ? hostOrOpts.host
        : null;
  const opts =
    hostOrOpts && typeof hostOrOpts.createSession === 'function'
      ? maybeOpts
      : hostOrOpts && typeof hostOrOpts === 'object'
        ? hostOrOpts
        : maybeOpts;

  if (!host || typeof host.createSession !== 'function') {
    return {
      ok: false,
      code: 'host_session_create_unavailable',
      error: 'real session/create host required',
      hint: 'Inject DSH session.create; simulated session_id is forbidden',
    };
  }
  const listed = listAgentPresets(host);
  if (!listed.ok) return listed;
  const hit = listed.presets.find((p) => p && p.id === presetId);
  if (!hit) {
    return {
      ok: false,
      code: 'preset_not_found',
      error: `unknown preset: ${presetId}`,
      hint: 'Use agentPresets/list; available includes mnemosyne after registerMnemosynePreset',
    };
  }
  if (hit.session_creatable === false) {
    return { ok: false, code: 'preset_not_creatable', error: 'preset not creatable' };
  }
  const created = host.createSession(presetId, opts || {});
  if (!created || !created.ok || !created.session_id) {
    return {
      ok: false,
      code: created?.code || 'session_create_failed',
      error: created?.error || 'host session/create failed',
      hint: created?.hint || 'Host must return session_id from real create',
    };
  }
  return {
    ok: true,
    session_id: created.session_id,
    preset_id: presetId,
    tools: hit.tools || null,
  };
}

/**
 * In-process host adapter used only after registerMnemosynePreset —
 * mirrors agentPresets/list + session/create contracts for isolated tests.
 * Not a success simulator: list is empty until register is called.
 */
export function createInProcessPresetHost() {
  const presets = new Map();
  let seq = 0;
  return {
    register(descriptor) {
      if (!descriptor?.id) throw new Error('descriptor.id required');
      presets.set(descriptor.id, { ...descriptor });
    },
    list() {
      return [...presets.values()];
    },
    createSession(presetId, opts = {}) {
      if (!presets.has(presetId)) {
        return { ok: false, code: 'preset_not_found', error: `unknown preset: ${presetId}` };
      }
      seq += 1;
      return {
        ok: true,
        session_id: opts.session_id || `host_sess_${presetId}_${seq}`,
        preset_id: presetId,
      };
    },
    authorizeTool(toolName, presetId = PRESET_ID) {
      const p = presets.get(presetId);
      if (!p) {
        return { ok: false, allowed: false, code: 'preset_not_registered' };
      }
      return authorizePresetTool(toolName, p);
    },
  };
}

/**
 * Fail-closed tool authorization for the mnemosyne preset.
 * Unknown tool names are denied when fail_closed=true.
 */
export function authorizePresetTool(toolName, preset = null) {
  const p = preset || loadMnemosynePreset();
  const name = String(toolName || '');
  if (!name) {
    return { ok: false, code: 'tool_name_required', allowed: false };
  }
  const allow = new Set(p.tools.allow || []);
  const denyExact = new Set(p.tools.deny_exact || []);
  const denyPrefixes = p.tools.deny_prefixes || [];
  if (denyExact.has(name)) {
    return { ok: false, code: 'tool_denied_exact', allowed: false, tool: name };
  }
  for (const prefix of denyPrefixes) {
    if (name === prefix || name.startsWith(prefix)) {
      return { ok: false, code: 'tool_denied_prefix', allowed: false, tool: name, prefix };
    }
  }
  if (allow.has(name)) {
    return { ok: true, allowed: true, tool: name };
  }
  if (p.tools.fail_closed !== false) {
    return {
      ok: false,
      code: 'tool_denied_fail_closed',
      allowed: false,
      tool: name,
      hint: 'Unknown tools are not silently allowed in mnemosyne preset',
    };
  }
  return { ok: true, allowed: true, tool: name, note: 'fail_closed=false' };
}
