/**
 * FA07–FA08 / DA11–DA12: real host registry path for mnemosyne preset.
 * No simulated session_id without register + host.createSession.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import {
  authorizePresetTool,
  createInProcessPresetHost,
  createSessionWithPreset,
  listAgentPresets,
  loadMnemosynePreset,
  registerMnemosynePreset,
  PRESET_INSERT_ID,
} from '../agent-preset.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

test('DA11: agentPresets/list includes mnemosyne after host register; session/create accepts it', () => {
  const bare = listAgentPresets(null);
  assert.equal(bare.ok, false);
  assert.equal(bare.code, 'host_agent_presets_unavailable');

  const host = createInProcessPresetHost();
  assert.deepEqual(listAgentPresets(host).presets, []);

  const reg = registerMnemosynePreset(host);
  assert.equal(reg.ok, true);
  assert.equal(reg.insert_id, PRESET_INSERT_ID);

  const list = listAgentPresets(host);
  assert.equal(list.ok, true);
  assert.ok(list.presets.some((p) => p.id === 'mnemosyne'));

  const created = createSessionWithPreset('mnemosyne', host);
  assert.equal(created.ok, true);
  assert.equal(created.preset_id, 'mnemosyne');
  assert.ok(created.session_id);
  assert.match(created.session_id, /^host_sess_/);

  const missing = createSessionWithPreset('not-a-preset', host);
  assert.equal(missing.ok, false);
  assert.equal(missing.code, 'preset_not_found');

  const noHost = createSessionWithPreset('mnemosyne', null);
  assert.equal(noHost.ok, false);
  assert.equal(noHost.code, 'host_session_create_unavailable');
});

test('DA12: local execute / file / process tools denied; unknown fail-closed', () => {
  const host = createInProcessPresetHost();
  registerMnemosynePreset(host);
  const p = loadMnemosynePreset();
  assert.equal(authorizePresetTool('memory_search', p).allowed, true);
  assert.equal(authorizePresetTool('ask_user_question', p).allowed, true);
  assert.equal(host.authorizeTool('memory_search').allowed, true);
  for (const bad of [
    'bash',
    'shell_exec',
    'local_execute',
    'write_file',
    'read_file',
    'manage_plugin',
    'process_kill',
  ]) {
    const r = host.authorizeTool(bad);
    assert.equal(r.allowed, false, bad);
    assert.ok(String(r.code).startsWith('tool_denied'), bad);
  }
  const unknown = host.authorizeTool('totally_unknown_tool_xyz');
  assert.equal(unknown.allowed, false);
  assert.equal(unknown.code, 'tool_denied_fail_closed');
});

test('preset shape has persona guidance and memory allowlist; cordis declares a real preset row', () => {
  const p = loadMnemosynePreset();
  assert.match(p.persona, /memory_/i);
  assert.ok(p.tools.allow.includes('memory_search'));
  assert.equal(p.tools.fail_closed, true);
  const cordis = readFileSync(join(root, 'cordis.patch.yml'), 'utf8');
  assert.match(cordis, /id:\s*preset-mnemosyne/);
  assert.match(cordis, /name:\s*'@deepseek-ai\/dsh-agent-preset'/);
  assert.match(cordis, /id:\s*mnemosyne/);
  assert.match(cordis, /plugins:/);
});
