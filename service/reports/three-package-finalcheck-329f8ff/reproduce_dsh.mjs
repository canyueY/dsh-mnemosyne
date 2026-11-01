import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '../../..');
const root = mkdtempSync(join(here, 'isolated-dsh-review-'));
const home = join(root, 'dsh-home');
const profile = join(home, 'profiles', 'web');
mkdirSync(profile, { recursive: true });
const sourcePath = 'D:/fixtures/dsh-cognitive-memory/archive.txt';
writeFileSync(join(profile, 'package.json'), JSON.stringify({
  name: 'synthetic-review-profile', private: true,
  dependencies: { 'dsh-cognitive-memory': 'file:C:/synthetic/legacy' },
  dsh: { profile: { bundles: ['keep-other', 'dsh-cognitive-memory'] } },
}));
writeFileSync(join(profile, 'cordis.patch.yml'),
  `- insert:\n    - id: keep-other\n      name: keep-other\n      config:\n        sourcePath: ${sourcePath}\n- insert:\n    - id: dsh-cognitive-memory\n      name: dsh-cognitive-memory\n`);
function migrate(extra) {
  const child = spawnSync(process.execPath,
    [join(repo, 'scripts/migrate-dsh.mjs'), '--profile', 'web', '--dsh-home', home, '--data-dir', join(root, 'bank'), ...extra],
    { cwd: repo, env: { ...process.env, DSH_HOME: home }, encoding: 'utf8', windowsHide: true });
  return { exit: child.status, result: JSON.parse(child.stdout) };
}
const first = migrate(['--skip-data']);
if (first.exit !== 0) throw new Error(JSON.stringify(first));
const second = migrate([]); // Earlier profile-only phase prevents actual data migration on retry.
const patchAfter = readFileSync(join(profile, 'cordis.patch.yml'), 'utf8');
const result = {
  first_action: first.result.action,
  first_data_skipped: first.result.data?.skipped,
  retry_action: second.result.result?.action || second.result.action,
  retry_has_data_result: Object.hasOwn(second.result, 'data'),
  unrelated_original_source_path: sourcePath,
  unrelated_source_path_preserved: patchAfter.includes(sourcePath),
  unrelated_source_path_after: patchAfter.split('\n').find(x => x.includes('sourcePath:'))?.trim(),
  only_isolated_profile_used: true,
};
writeFileSync(join(here, 'counterexamples-dsh.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
