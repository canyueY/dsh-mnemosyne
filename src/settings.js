/**
 * Settings namespace helpers for dsh-mnemosyne.
 * Secret fields: write-only on read paths; masked as ********; never logged.
 *
 * Persistence: optional file backend (MNEMOSYNE_SETTINGS_FILE / setSettingsPersistPath)
 * survives process restart; in-memory Map remains the default for pure unit tests.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import {
  CONFIG_DEFAULTS,
  CONFIG_FIELD_SCHEMA,
  LEGACY_NAMESPACE,
  SETTINGS_NAMESPACE,
} from './config-schema.js';

export { SETTINGS_NAMESPACE, LEGACY_NAMESPACE, CONFIG_DEFAULTS, CONFIG_FIELD_SCHEMA };

const SECRET_MASK = '********';

/** In-memory store for unit tests / headless describe-update without full Cordis. */
const _stores = new Map();
let _persistPath = process.env.MNEMOSYNE_SETTINGS_FILE || null;

function storeKey(ns = SETTINGS_NAMESPACE) {
  return String(ns || SETTINGS_NAMESPACE);
}

export function setSettingsPersistPath(path) {
  _persistPath = path || null;
  return _persistPath;
}

export function getSettingsPersistPath() {
  return _persistPath;
}

function loadPersisted(ns) {
  if (!_persistPath) return null;
  try {
    if (!existsSync(_persistPath)) return null;
    const raw = JSON.parse(readFileSync(_persistPath, 'utf8'));
    const bucket = raw?.[storeKey(ns)];
    if (!bucket || typeof bucket !== 'object') return null;
    return bucket;
  } catch {
    return null;
  }
}

function savePersisted(ns, value, rev) {
  if (!_persistPath) return;
  let all = {};
  try {
    if (existsSync(_persistPath)) {
      all = JSON.parse(readFileSync(_persistPath, 'utf8')) || {};
    }
  } catch {
    all = {};
  }
  all[storeKey(ns)] = { value, revision: rev, updated_at: new Date().toISOString() };
  mkdirSync(dirname(_persistPath), { recursive: true });
  writeFileSync(_persistPath, `${JSON.stringify(all, null, 2)}\n`, 'utf8');
}

export function resetSettingsStore(ns = SETTINGS_NAMESPACE) {
  _stores.delete(storeKey(ns));
  _stores.delete(`${storeKey(ns)}::rev`);
}

export function redactSecrets(value) {
  if (!value || typeof value !== 'object') return value;
  const out = { ...value };
  for (const [k, meta] of Object.entries(CONFIG_FIELD_SCHEMA)) {
    if (meta.secret && k in out && out[k]) {
      out[k] = SECRET_MASK;
    }
  }
  return out;
}

export function validateConfigPatch(patch) {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) {
    return { ok: false, code: 'invalid_patch', error: 'patch must be object', hint: 'Pass a plain object' };
  }
  for (const [key, val] of Object.entries(patch)) {
    const meta = CONFIG_FIELD_SCHEMA[key];
    if (!meta) {
      return {
        ok: false,
        code: 'unknown_field',
        error: `unknown field: ${key}`,
        hint: `Allowed: ${Object.keys(CONFIG_FIELD_SCHEMA).join(', ')}`,
      };
    }
    if (meta.type === 'boolean' && typeof val !== 'boolean') {
      return {
        ok: false,
        code: 'invalid_type',
        error: `${key} must be boolean`,
        hint: `Got ${typeof val}`,
      };
    }
    if (meta.type === 'string' && typeof val !== 'string') {
      return {
        ok: false,
        code: 'invalid_type',
        error: `${key} must be string`,
        hint: `Got ${typeof val}`,
      };
    }
  }
  return { ok: true };
}

/**
 * DA10: auto_start=false requires base_url + token.
 * @returns {{ok:true,cfg}|{ok:false,code,error,hint}}
 */
export function assertRunnableConfig(cfg) {
  const c = { ...CONFIG_DEFAULTS, ...(cfg || {}) };
  if (c.auto_start === false) {
    const base = String(c.base_url || '').trim();
    const token = String(c.token || '').trim();
    if (!base || !token) {
      return {
        ok: false,
        code: 'config_incomplete',
        error: 'auto_start=false requires base_url and token',
        hint: 'Provide base_url+token or set auto_start=true to spawn local service',
      };
    }
  }
  return { ok: true, cfg: c };
}

export function mergeConfigLayers(rootConfig = {}, fiberConfig = null) {
  const root = rootConfig || {};
  const fromRoot =
    root[SETTINGS_NAMESPACE] || root[LEGACY_NAMESPACE] || {};
  // fiber/plugin Config (volatile) wins over nested root key when provided
  return { ...CONFIG_DEFAULTS, ...fromRoot, ...(fiberConfig || {}) };
}

function persistedConfig(ns) {
  const bucket = loadPersisted(ns);
  const value = bucket?.value;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  // Older settings files wrote the full default set. Do not let those default
  // values mask an explicit root/row value; only keep values that differ.
  return Object.fromEntries(
    Object.entries(value).filter(([key, entry]) => entry !== CONFIG_DEFAULTS[key]),
  );
}

/**
 * Resolve effective config from Cordis ctx.
 * Precedence: Loader/row config (fiber) > legacy file store > ctx.get('config')[ns] > defaults.
 *
 * Deliberately does not call `ctx.get('settings').describe()`: in DSH
 * 0.2.1-rc.5 through 0.2.2 that path reads every plugin's Config and crashes
 * on a null schema (`'toJSON' in null`).
 */
export function resolvePluginConfig(ctx, fiberConfig = null) {
  let rootConfig = {};
  try {
    rootConfig = (typeof ctx?.get === 'function' ? ctx.get('config') : null) || {};
  } catch {
    rootConfig = {};
  }
  const persisted = persistedConfig(SETTINGS_NAMESPACE);
  return mergeConfigLayers(rootConfig, { ...persisted, ...(fiberConfig || {}) });
}

/** Test/host helper implementing settings.describe contract. */
export function settingsDescribe(ns = SETTINGS_NAMESPACE, { redactSecrets: redact = true } = {}) {
  const key = storeKey(ns);
  const persisted = loadPersisted(ns);
  if (persisted?.value && !_stores.has(key)) {
    _stores.set(key, { ...persisted.value });
    _stores.set(`${key}::rev`, Number(persisted.revision || 1));
  }
  const current = { ...CONFIG_DEFAULTS, ...(_stores.get(key) || {}) };
  const value = redact ? redactSecrets(current) : { ...current };
  const secrets = [];
  if (redact && current.token) {
    secrets.push({ path: ['token'], present: true, mask: SECRET_MASK });
  }
  return [
    {
      ns,
      autoGenerate: true,
      schema: CONFIG_FIELD_SCHEMA,
      value,
      revision: Number(_stores.get(`${key}::rev`) || 1),
      applies: 'live',
      persisted: Boolean(_persistPath),
      persist_path: _persistPath || null,
      secrets,
    },
  ];
}

/** Test/host helper implementing settings.update contract. */
export function settingsUpdate(ns, patch, expectedRevision) {
  const v = validateConfigPatch(patch);
  if (!v.ok) {
    const err = new Error(v.error);
    err.code = v.code;
    err.hint = v.hint;
    throw err;
  }
  const key = storeKey(ns);
  const persisted = loadPersisted(ns);
  if (persisted?.value && !_stores.has(key)) {
    _stores.set(key, { ...persisted.value });
    _stores.set(`${key}::rev`, Number(persisted.revision || 1));
  }
  const rev = Number(_stores.get(`${key}::rev`) || 1);
  if (expectedRevision != null && Number(expectedRevision) !== rev) {
    const err = new Error('settings revision conflict');
    err.code = 'SETTINGS_CONFLICT';
    err.expected = expectedRevision;
    err.actual = rev;
    throw err;
  }
  const merged = { ...CONFIG_DEFAULTS, ...(_stores.get(key) || {}), ...patch };
  // Never persist masked placeholder as real token
  if (merged.token === SECRET_MASK) {
    const prev = _stores.get(key) || {};
    merged.token = prev.token || '';
  }
  _stores.set(key, merged);
  const nextRev = rev + 1;
  _stores.set(`${key}::rev`, nextRev);
  savePersisted(ns, merged, nextRev);
  return { ok: true, revision: nextRev, persisted: Boolean(_persistPath) };
}

/** Safe log serializer — secrets always masked. */
export function toLoggableConfig(cfg) {
  return redactSecrets(cfg || {});
}
