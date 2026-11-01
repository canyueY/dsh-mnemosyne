/**
 * DA02–DA04: install-dsh.mjs failure visibility + idempotent / preserve behavior.
 * Uses temporary DSH_HOME; does not touch real profiles.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const script = join(root, 'scripts', 'install-dsh.mjs');
const defaultCli = join(
  process.env.USERPROFILE || process.env.HOME || '',
  '.dsh',
  'profiles',
  'node_modules',
  '@deepseek-ai',
  'dsh',
  'lib',
  'bin.js',
);

function runInstall(extraArgs, env = {}) {
  const res = spawnSync(process.execPath, [script, ...extraArgs], {
    encoding: 'utf8',
    cwd: root,
    env: { ...process.env, ...env },
    windowsHide: true,
  });
  const text = `${res.stdout || ''}\n${res.stderr || ''}`.trim();
  let json = null;
  try {
    // last JSON object in stdout
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start >= 0 && end > start) json = JSON.parse(text.slice(start, end + 1));
  } catch {
    json = null;
  }
  return { status: res.status ?? 1, text, json };
}

function seedProfile(home, name, bundles) {
  const dir = join(home, 'profiles', name);
  mkdirSync(dir, { recursive: true });
  const pkg = {
    name: `dsh-profile-${name}`,
    private: true,
    dependencies: {},
    dsh: { profile: { bundles: [...bundles] } },
  };
  writeFileSync(join(dir, 'package.json'), `${JSON.stringify(pkg, null, 2)}\n`);
  writeFileSync(join(dir, 'cordis.patch.yml'), '- insert:\n    - id: keep-other\n      name: keep-other\n');
  return dir;
}

test('DA04: missing DSH CLI returns structured failure', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-install-da04-'));
  seedProfile(home, 'web', ['@deepseek-ai/dsh-base']);
  const { status, json } = runInstall(['--local', '--profile', 'web', '--dsh-home', home, '--dry-run'], {
    DSH_CLI: join(home, 'missing-dsh-bin.js'),
    // prevent fallback discovery of real CLI
    Path: '',
    PATH: '',
  });
  // dry-run still needs CLI resolution — without CLI should fail
  assert.equal(status, 1);
  assert.ok(json);
  assert.equal(json.ok, false);
  assert.equal(json.code, 'dsh_cli_missing');
  assert.ok(json.hint);
});

test('DA04: missing profile returns structured failure', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-install-da04b-'));
  mkdirSync(join(home, 'profiles'), { recursive: true });
  const env = existsSync(defaultCli) ? { DSH_CLI: defaultCli } : {};
  const { status, json } = runInstall(
    ['--local', '--profile', 'no-such-profile', '--dsh-home', home, '--dry-run'],
    env,
  );
  assert.equal(status, 1);
  assert.ok(json);
  assert.equal(json.ok, false);
  assert.equal(json.code, 'profile_missing');
  assert.ok(json.hint);
});

test('DA02/DA03: install preserves other bundles and second run is idempotent', async (t) => {
  if (!existsSync(defaultCli)) {
    t.skip('local DSH CLI not present');
    return;
  }
  const home = mkdtempSync(join(tmpdir(), 'dsh-install-da023-'));
  seedProfile(home, 'web', [
    '@deepseek-ai/dsh-base',
    '@deepseek-ai/dsh-web-app',
    'keep-other-bundle',
  ]);
  const env = { DSH_CLI: defaultCli, DSH_HOME: home };

  const first = runInstall(
    [
      '--local',
      '--profile',
      'web',
      '--dsh-home',
      home,
      '--accept-version-risk',
      '--skip-post-check',
    ],
    env,
  );
  assert.equal(first.status, 0, first.text);
  assert.equal(first.json?.ok, true);
  assert.equal(first.json?.action, 'installed');

  const pkg1 = JSON.parse(readFileSync(join(home, 'profiles', 'web', 'package.json'), 'utf8'));
  assert.ok(pkg1.dependencies?.['dsh-mnemosyne']);
  assert.deepEqual(
    pkg1.dsh.profile.bundles,
    ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'keep-other-bundle', 'dsh-mnemosyne'],
  );
  const cordis = readFileSync(join(home, 'profiles', 'web', 'cordis.patch.yml'), 'utf8');
  assert.match(cordis, /keep-other/);

  const second = runInstall(
    ['--local', '--profile', 'web', '--dsh-home', home, '--skip-post-check'],
    env,
  );
  assert.equal(second.status, 0, second.text);
  assert.equal(second.json?.ok, true);
  assert.equal(second.json?.action, 'skipped_idempotent');
});
