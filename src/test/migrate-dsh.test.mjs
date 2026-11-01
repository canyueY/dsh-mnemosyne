/**
 * DA05–DA07: migrate-dsh.mjs with temporary DSH_HOME + temporary Bank.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
  existsSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const script = join(root, 'scripts', 'migrate-dsh.mjs');

function run(args, env = {}) {
  const res = spawnSync(process.execPath, [script, ...args], {
    encoding: 'utf8',
    cwd: root,
    env: { ...process.env, ...env },
    windowsHide: true,
  });
  const text = `${res.stdout || ''}\n${res.stderr || ''}`.trim();
  let json = null;
  try {
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start >= 0 && end > start) json = JSON.parse(text.slice(start, end + 1));
  } catch {
    json = null;
  }
  return { status: res.status ?? 1, text, json };
}

function seedLegacyProfile(home) {
  const dir = join(home, 'profiles', 'web');
  mkdirSync(dir, { recursive: true });
  const pkg = {
    name: 'dsh-profile-web',
    private: true,
    dependencies: {
      'dsh-cognitive-memory': 'file:C:/fake/old',
      'keep-dep': '1.0.0',
    },
    dsh: {
      profile: {
        bundles: ['@deepseek-ai/dsh-base', 'dsh-cognitive-memory', 'keep-other-bundle'],
      },
    },
  };
  writeFileSync(join(dir, 'package.json'), `${JSON.stringify(pkg, null, 2)}\n`);
  writeFileSync(
    join(dir, 'cordis.patch.yml'),
    '- insert:\n    - id: keep-other\n      name: keep-other\n- insert:\n    - id: dsh-cognitive-memory\n      name: dsh-cognitive-memory\n',
  );
  return dir;
}

test('DA05: migrates old bundle/dependency and preserves others', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-da05-'));
  seedLegacyProfile(home);
  const bank = mkdtempSync(join(tmpdir(), 'bank-da05-'));
  const { status, json, text } = run(
    ['--profile', 'web', '--dsh-home', home, '--data-dir', bank, '--skip-data'],
  );
  assert.equal(status, 0, text);
  assert.equal(json.ok, true);
  assert.equal(json.action, 'migrated');
  const pkg = JSON.parse(readFileSync(join(home, 'profiles', 'web', 'package.json'), 'utf8'));
  assert.ok(pkg.dependencies['dsh-mnemosyne']);
  assert.equal(pkg.dependencies['dsh-cognitive-memory'], undefined);
  assert.equal(pkg.dependencies['keep-dep'], '1.0.0');
  assert.deepEqual(pkg.dsh.profile.bundles, [
    '@deepseek-ai/dsh-base',
    'dsh-mnemosyne',
    'keep-other-bundle',
  ]);
  const cordis = readFileSync(join(home, 'profiles', 'web', 'cordis.patch.yml'), 'utf8');
  assert.match(cordis, /keep-other/);
  assert.match(cordis, /dsh-mnemosyne/);
  assert.doesNotMatch(cordis, /dsh-cognitive-memory/);
});

test('DA06: rollback restores pre-migration package.json', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-da06-'));
  const dir = seedLegacyProfile(home);
  const before = readFileSync(join(dir, 'package.json'), 'utf8');
  const beforeCordis = readFileSync(join(dir, 'cordis.patch.yml'), 'utf8');
  const mig = run(['--profile', 'web', '--dsh-home', home, '--skip-data']);
  assert.equal(mig.status, 0, mig.text);
  const mid = readFileSync(join(dir, 'package.json'), 'utf8');
  assert.notEqual(mid, before);
  const rb = run(['--profile', 'web', '--dsh-home', home, '--rollback']);
  assert.equal(rb.status, 0, rb.text);
  assert.equal(rb.json.action, 'rollback');
  assert.equal(readFileSync(join(dir, 'package.json'), 'utf8'), before);
  assert.equal(readFileSync(join(dir, 'cordis.patch.yml'), 'utf8'), beforeCordis);
});

test('DA07: interrupted migrate is re-entrant; data dir not deleted', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-da07-'));
  seedLegacyProfile(home);
  const bank = mkdtempSync(join(tmpdir(), 'bank-da07-'));
  writeFileSync(join(bank, 'marker.txt'), 'keep-me');
  const first = run(['--profile', 'web', '--dsh-home', home, '--data-dir', bank, '--skip-data']);
  assert.equal(first.status, 0, first.text);
  // simulate "interrupted" by deleting state mid-way then re-running
  const second = run(['--profile', 'web', '--dsh-home', home, '--data-dir', bank, '--skip-data']);
  assert.equal(second.status, 0, second.text);
  assert.equal(second.json.action, 'already_migrated');
  assert.equal(second.json.data_dir_deleted, false);
  assert.ok(existsSync(join(bank, 'marker.txt')));
});

test('missing profile fails visibly', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-miss-'));
  mkdirSync(join(home, 'profiles'), { recursive: true });
  const { status, json } = run(['--profile', 'ghost', '--dsh-home', home, '--skip-data']);
  assert.equal(status, 1);
  assert.equal(json.ok, false);
  assert.equal(json.code, 'profile_missing');
});

test('FA12: profile-only then retry continues data stage (not already_migrated without data)', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-fa12-'));
  seedLegacyProfile(home);
  const bank = mkdtempSync(join(tmpdir(), 'bank-fa12-'));
  writeFileSync(join(bank, 'marker.txt'), 'keep');
  const first = run(['--profile', 'web', '--dsh-home', home, '--data-dir', bank, '--skip-data']);
  assert.equal(first.status, 0, first.text);
  assert.equal(first.json.action, 'migrated');
  assert.equal(first.json.data?.skipped, true);
  const second = run(['--profile', 'web', '--dsh-home', home, '--data-dir', bank]);
  assert.ok(second.json, second.text);
  assert.notEqual(second.json.action, 'already_migrated');
  assert.ok(Object.hasOwn(second.json, 'data'), JSON.stringify(second.json));
  assert.ok(existsSync(join(bank, 'marker.txt')));
});

test('FA18: unrelated plugin sourcePath containing old name is byte-preserved', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-fa18-'));
  const dir = seedLegacyProfile(home);
  const sourcePath = 'D:/fixtures/dsh-cognitive-memory/archive.txt';
  writeFileSync(
    join(dir, 'cordis.patch.yml'),
    `- insert:\n    - id: keep-other\n      name: keep-other\n      config:\n        sourcePath: ${sourcePath}\n- insert:\n    - id: dsh-cognitive-memory\n      name: dsh-cognitive-memory\n`,
  );
  const { status, json, text } = run(['--profile', 'web', '--dsh-home', home, '--skip-data']);
  assert.equal(status, 0, text);
  assert.equal(json.ok, true);
  const patchAfter = readFileSync(join(dir, 'cordis.patch.yml'), 'utf8');
  assert.ok(patchAfter.includes(sourcePath), patchAfter);
  assert.match(patchAfter, /id:\s*dsh-mnemosyne/);
  assert.doesNotMatch(patchAfter, /^[ \t]*-?[ \t]*id:\s*dsh-cognitive-memory\s*$/m);
});

test('FA18b: quoted owned row changes; other plugin nested id/name is byte-preserved', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-fa18b-'));
  const dir = seedLegacyProfile(home);
  const foreign = [
    '- insert:',
    '    - id: keep-other',
    '      name: keep-other',
    '      config:',
    '        id: dsh-cognitive-memory',
    '        name: dsh-cognitive-memory',
    '        sourcePath: D:/fixtures/keep-other/archive.txt',
  ].join('\r\n');
  const owned = [
    '- insert:',
    "    - id: 'dsh-cognitive-memory'",
    '      name: "dsh-cognitive-memory"',
  ].join('\r\n');
  writeFileSync(join(dir, 'cordis.patch.yml'), `${foreign}\r\n${owned}\r\n`, 'utf8');
  const { status, json, text } = run(['--profile', 'web', '--dsh-home', home, '--skip-data']);
  assert.equal(status, 0, text);
  assert.equal(json.ok, true);
  const patchAfter = readFileSync(join(dir, 'cordis.patch.yml'), 'utf8');
  assert.ok(patchAfter.startsWith(foreign), patchAfter);
  assert.ok(patchAfter.includes("id: 'dsh-mnemosyne'"), patchAfter);
  assert.ok(patchAfter.includes('name: "dsh-mnemosyne"'), patchAfter);
});

test('FA18c: nested list and block scalar rows are not owned plugin rows', () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-mig-fa18c-'));
  const dir = seedLegacyProfile(home);
  const foreign = [
    '- insert:',
    '    - id: keep-other',
    '      name: keep-other',
    '      config:',
    '        sources:',
    '          - id: dsh-cognitive-memory',
    '            name: dsh-cognitive-memory',
    '        notes: |',
    '          - id: dsh-cognitive-memory',
    '            name: dsh-cognitive-memory',
  ].join('\r\n');
  const owned = [
    '- insert:',
    "    - id: 'dsh-cognitive-memory'",
    '      name: "dsh-cognitive-memory"',
  ].join('\r\n');
  writeFileSync(join(dir, 'cordis.patch.yml'), `${foreign}\r\n${owned}\r\n`, 'utf8');
  const { status, json, text } = run(['--profile', 'web', '--dsh-home', home, '--skip-data']);
  assert.equal(status, 0, text);
  assert.equal(json.ok, true);
  const patchAfter = readFileSync(join(dir, 'cordis.patch.yml'), 'utf8');
  assert.ok(patchAfter.startsWith(foreign), patchAfter);
  assert.ok(patchAfter.includes("id: 'dsh-mnemosyne'"), patchAfter);
  assert.ok(patchAfter.includes('name: "dsh-mnemosyne"'), patchAfter);
});
