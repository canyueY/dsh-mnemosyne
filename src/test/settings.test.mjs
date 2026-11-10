/**
 * DA08–DA10: settings namespace describe/update + secret redaction + apply gate.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  SETTINGS_NAMESPACE,
  settingsDescribe,
  settingsUpdate,
  resetSettingsStore,
  redactSecrets,
  assertRunnableConfig,
  toLoggableConfig,
} from '../settings.js';
import { apply } from '../index.js';

test('DA08: settings.describe lists dsh-mnemosyne namespace and field schema', () => {
  resetSettingsStore();
  const list = settingsDescribe(SETTINGS_NAMESPACE);
  assert.equal(list.length, 1);
  assert.equal(list[0].ns, 'dsh-mnemosyne');
  assert.ok(list[0].schema.auto_start);
  assert.ok(list[0].schema.token.secret);
  assert.equal(list[0].value.auto_start, true);
});

test('DA09: settings.update persists token but describe redacts it; logs stay masked', () => {
  resetSettingsStore();
  const r1 = settingsUpdate(SETTINGS_NAMESPACE, {
    base_url: 'http://127.0.0.1:9',
    token: 'super-secret-token',
  });
  assert.equal(r1.ok, true);
  const described = settingsDescribe(SETTINGS_NAMESPACE, { redactSecrets: true })[0];
  assert.equal(described.value.token, '********');
  assert.equal(described.value.base_url, 'http://127.0.0.1:9');
  assert.ok(described.secrets?.some((s) => s.path?.[0] === 'token'));
  // raw store still has secret for runtime; log helper must mask
  const logged = JSON.stringify(toLoggableConfig({ token: 'super-secret-token', base_url: 'x' }));
  assert.ok(!logged.includes('super-secret-token'));
  assert.ok(logged.includes('********'));
  // updating with mask must not wipe real token
  settingsUpdate(SETTINGS_NAMESPACE, { token: '********' });
  const raw = settingsDescribe(SETTINGS_NAMESPACE, { redactSecrets: false })[0];
  assert.equal(raw.value.token, 'super-secret-token');
});

test('DA09b: illegal field/type rejected', () => {
  resetSettingsStore();
  assert.throws(() => settingsUpdate(SETTINGS_NAMESPACE, { auto_start: 'yes' }), /boolean/);
  assert.throws(() => settingsUpdate(SETTINGS_NAMESPACE, { not_a_field: 1 }), /unknown field/);
});

test('DA10: auto_start=false without base_url/token fails apply with diagnosable error', async () => {
  const tools = [];
  const ctx = {
    get(name) {
      if (name === 'config') {
        return {
          'dsh-mnemosyne': { auto_start: false },
        };
      }
      return null;
    },
    tools: {
      register(t) {
        tools.push(t);
        return () => {};
      },
    },
    on() {
      return () => {};
    },
  };
  await assert.rejects(() => apply(ctx), (err) => {
    assert.match(String(err.message), /auto_start=false|config_incomplete|base_url/);
    assert.equal(err.code, 'config_incomplete');
    assert.ok(err.loggable_config);
    // empty or masked — never a plaintext secret
    assert.ok(!err.loggable_config.token || err.loggable_config.token === '********');
    return true;
  });
  assert.equal(tools.length, 0);
});

test('DA10b: assertRunnableConfig helper', () => {
  assert.equal(assertRunnableConfig({ auto_start: false }).ok, false);
  assert.equal(
    assertRunnableConfig({ auto_start: false, base_url: 'http://x', token: 't' }).ok,
    true,
  );
  assert.equal(assertRunnableConfig({ auto_start: true }).ok, true);
});

test('redactSecrets never echoes plaintext token', () => {
  const r = redactSecrets({ token: 'abc', auto_start: true });
  assert.equal(r.token, '********');
  assert.equal(r.auto_start, true);
});

test('FA19: settings persist to file and reload after process-store reset (restart)', async () => {
  const { mkdtempSync, readFileSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const {
    setSettingsPersistPath,
    resetSettingsStore,
    settingsUpdate,
    settingsDescribe,
    resolvePluginConfig,
  } = await import('../settings.js');

  const dir = mkdtempSync(join(tmpdir(), 'mnemo-settings-'));
  const file = join(dir, 'settings.json');
  setSettingsPersistPath(file);
  resetSettingsStore();
  const upd = settingsUpdate(SETTINGS_NAMESPACE, {
    auto_start: false,
    base_url: 'http://127.0.0.1:18765',
    token: 'persist-secret',
  });
  assert.equal(upd.ok, true);
  assert.equal(upd.persisted, true);
  assert.ok(readFileSync(file, 'utf8').includes('http://127.0.0.1:18765'));

  // Simulate process restart: clear memory, keep file
  resetSettingsStore();
  const described = settingsDescribe(SETTINGS_NAMESPACE, { redactSecrets: false })[0];
  assert.equal(described.value.base_url, 'http://127.0.0.1:18765');
  assert.equal(described.value.token, 'persist-secret');
  assert.equal(described.value.auto_start, false);
  assert.equal(described.persisted, true);

  // Runtime apply path reads the settings file directly, not settings.describe.
  const tools = [];
  const ctx = {
    get(name) {
      if (name === 'settings') {
        return {
          describe: ({ redactSecrets: r } = {}) => settingsDescribe(SETTINGS_NAMESPACE, { redactSecrets: r }),
        };
      }
      if (name === 'config') return {};
      return null;
    },
    tools: { register(t) { tools.push(t); return () => {}; } },
    on() { return () => {}; },
  };
  const cfg = resolvePluginConfig(ctx);
  assert.equal(cfg.base_url, 'http://127.0.0.1:18765');
  assert.equal(cfg.token, 'persist-secret');
  setSettingsPersistPath(null);
  resetSettingsStore();
});
