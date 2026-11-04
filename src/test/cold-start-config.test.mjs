/**
 * DSH 0.2.1-rc.5 cold-start boundary.
 * Host behavior copied from the rc.5 app bundle:
 *   cordis resolveConfig: if (!runtime.Config) return config
 *   dsh-settings schema(): schema !== undefined && 'toJSON' in schema
 * A null Config skips validation and then throws on `'toJSON' in null`.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

function hostSettingsSchema(runtime) {
  const schema = runtime?.Config;
  return schema !== undefined && 'toJSON' in schema ? schema : undefined;
}

function hostResolveConfig(runtime, config) {
  if (!runtime.Config) return config;
  const result = runtime.Config['~standard'].validate(config);
  if (result && typeof result === 'object' && 'then' in result) {
    throw new TypeError('Async config validation is not supported');
  }
  if (result?.issues) throw new Error('invalid config');
  return result.value;
}

test('entry does not export Config, so rc.5 settings.describe does not touch toJSON', async () => {
  const entry = await import('../index.js');
  assert.equal('Config' in entry, false);
  assert.equal(entry.Config, undefined);
  assert.equal(hostSettingsSchema(entry), undefined);
  const row = { auto_start: true, auto_capture: true, data_dir: 'D:/banks/keep' };
  assert.deepEqual(hostResolveConfig(entry, row), row);
});

test('a null Config is what crashes rc.5; this entry must not publish one', () => {
  assert.throws(() => {
    const schema = null;
    return schema !== undefined && 'toJSON' in schema;
  }, TypeError);
});

test('resolveConfigExport never returns null when schemastery is missing', async () => {
  const { resolveConfigExport } = await import('../config-schema.js');
  try {
    const schema = await resolveConfigExport();
    assert.notEqual(schema, null);
    assert.equal(typeof schema.toJSON, 'function');
    assert.equal(typeof schema['~standard']?.validate, 'function');
  } catch (error) {
    assert.equal(error.code, 'config_schema_unavailable');
    assert.match(error.message, /refusing to export Config/);
  }
});

test('plugin-row config wins over a broken root config and is not rewritten', async () => {
  process.env.DSH_TOOLS_PATH = join(here, '..', '..', 'legacy', 'dsh-plugin', 'test-support', 'dsh-tools-stub.mjs');
  const { apply } = await import('../index.js');
  const bank = join(mkdtempSync(join(tmpdir(), 'mnemo-row-')), 'bank');
  const registered = [];
  const ctx = {
    get(name) {
      if (name === 'config') return { 'dsh-mnemosyne': { auto_start: false } };
      if (name === 'settings') throw new Error('settings.describe failed');
      return null;
    },
    tools: {
      register(tool) {
        registered.push(tool.name);
        return () => {};
      },
    },
    on() {
      return () => {};
    },
  };
  const dispose = await apply(ctx, {
    auto_start: false,
    base_url: 'http://127.0.0.1:9',
    token: 'row-token',
    data_dir: bank,
    auto_capture: false,
  });
  assert.ok(registered.includes('memory_health'));
  dispose();
});

test('a failed settings read keeps the plugin row and does not write defaults', async () => {  const { resolvePluginConfig, setSettingsPersistPath, resetSettingsStore } = await import('../settings.js');
  const dir = mkdtempSync(join(tmpdir(), 'mnemo-settings-'));
  const file = join(dir, 'settings.json');
  writeFileSync(file, '{', 'utf8');
  setSettingsPersistPath(file);
  resetSettingsStore();
  try {
    const bank = join(dir, 'bank');
    const cfg = resolvePluginConfig({
      get(name) {
        if (name === 'settings') throw new Error('describe failed');
        if (name === 'config') return {};
        return null;
      },
    }, {
      auto_start: false,
      base_url: 'http://127.0.0.1:9',
      token: 'kept-token',
      data_dir: bank,
    });
    assert.equal(cfg.data_dir, bank);
    assert.equal(cfg.base_url, 'http://127.0.0.1:9');
    assert.equal(cfg.token, 'kept-token');
    assert.equal(readFileSync(file, 'utf8'), '{');
  } finally {
    setSettingsPersistPath(null);
    resetSettingsStore();
  }
});

test('invalid plugin-row config fails before touching the host settings service', async () => {
  process.env.DSH_TOOLS_PATH = join(here, '..', '..', 'legacy', 'dsh-plugin', 'test-support', 'dsh-tools-stub.mjs');
  const { apply } = await import('../index.js');
  let settingsCalls = 0;
  const ctx = {
    get(name) {
      if (name === 'settings') {
        settingsCalls += 1;
        throw new Error('settings.describe must not run for invalid row config');
      }
      return null;
    },
    tools: { register() { return () => {}; } },
    on() { return () => {}; },
  };
  await assert.rejects(
    () => apply(ctx, { auto_start: 'not-a-boolean' }),
    (error) => error.code === 'invalid_type' && /auto_start/.test(error.message),
  );
  assert.equal(settingsCalls, 0);
});
