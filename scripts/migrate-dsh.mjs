#!/usr/bin/env node
/**
 * Migrate profile bundle dsh-cognitive-memory → dsh-mnemosyne + optional data migrate-legacy.
 * Thin host: no SQLite open, no token reads. Temp DSH_HOME / Bank recommended.
 */
import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OLD_NAME = 'dsh-cognitive-memory';
const NEW_NAME = 'dsh-mnemosyne';
const STATE_FILE = '_mnemosyne_migrate_state.json';

function emit(payload, exit = 0) {
  console.log(JSON.stringify(payload, null, 2));
  process.exitCode = exit;
  return payload;
}
function fail(code, error, hint, extra = {}) {
  return emit({ ok: false, code, error, hint, ...extra }, 1);
}
function ok(payload) {
  return emit({ ok: true, ...payload }, 0);
}

function parseArgs(argv) {
  const args = {
    profile: null,
    dshHome: process.env.DSH_HOME || null,
    rollback: false,
    skipData: false,
    dataDir: process.env.MNEMOSYNE_MEMORY_BANK || null,
    localSource: true,
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--profile') args.profile = argv[++i];
    else if (a === '--dsh-home') args.dshHome = argv[++i];
    else if (a === '--rollback') args.rollback = true;
    else if (a === '--skip-data') args.skipData = true;
    else if (a === '--data-dir') args.dataDir = argv[++i];
    else if (a === '--help' || a === '-h') args.help = true;
  }
  return args;
}

function resolveDshHome(explicit) {
  if (explicit) return resolve(explicit);
  if (process.env.DSH_HOME) return resolve(process.env.DSH_HOME);
  return join(homedir(), '.dsh');
}

function resolveProfile(args, dshHome) {
  if (args.profile) return args.profile;
  if (process.env.DSH_PROFILE) return process.env.DSH_PROFILE;
  const profilesDir = join(dshHome, 'profiles');
  if (existsSync(join(profilesDir, 'desktop', 'package.json'))) return 'desktop';
  if (existsSync(join(profilesDir, 'web', 'package.json'))) return 'web';
  return 'web';
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function writeJson(path, data) {
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

function snapshotDataDir(dataDir) {
  if (!dataDir || !existsSync(dataDir)) {
    return { path: dataDir || null, exists: false, files: [] };
  }
  try {
    const files = readdirSync(dataDir);
    return { path: dataDir, exists: true, files: files.slice(0, 50), count: files.length };
  } catch (err) {
    return { path: dataDir, exists: true, error: String(err.message || err) };
  }
}

function backupProfile(profilePath, kind = 'migrate') {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupRoot = join(profilePath, '_backups', `${kind}-${stamp}`);
  mkdirSync(backupRoot, { recursive: true });
  const saved = [];
  for (const name of ['package.json', 'cordis.patch.yml', 'pnpm-lock.yaml', STATE_FILE]) {
    const src = join(profilePath, name);
    if (!existsSync(src)) continue;
    copyFileSync(src, join(backupRoot, name));
    saved.push(name);
  }
  return { backupRoot, saved };
}

function rewriteCordis(text) {
  // Only rewrite owned id/name fields for this plugin — never global string replace
  // (other plugins may embed OLD_NAME inside sourcePath / comments / configs).
  if (!text) return { text: text || '', changed: false };
  let changed = false;
  const next = String(text).replace(
    /^([ \t]*-?[ \t]*(?:id|name):\s*)dsh-cognitive-memory(\s*)$/gm,
    (_, prefix, suffix) => {
      changed = true;
      return `${prefix}${NEW_NAME}${suffix}`;
    },
  );
  let out = next;
  const hasOwned =
    /(^|\n)[ \t]*-?[ \t]*id:\s*dsh-mnemosyne(\s|$)/.test(out) ||
    /(^|\n)[ \t]*-?[ \t]*id:\s*dsh-cognitive-memory(\s|$)/.test(out);
  if (!hasOwned) {
    const block = `- insert:\n    - id: ${NEW_NAME}\n      name: ${NEW_NAME}\n`;
    out = out.trimEnd() ? `${out.trimEnd()}\n${block}` : block;
    changed = true;
  }
  return { text: out, changed: changed || out !== text };
}

function readMigrateState(profilePath) {
  const statePath = join(profilePath, STATE_FILE);
  if (!existsSync(statePath)) return null;
  try {
    return readJson(statePath);
  } catch {
    return null;
  }
}

function writeMigrateState(profilePath, state) {
  writeJson(join(profilePath, STATE_FILE), state);
}

function phaseStatus(state) {
  const profileDone = Boolean(state?.profile_done);
  const dataDone = Boolean(state?.data_done);
  const dataPending = Boolean(state?.data_pending) || (profileDone && !dataDone);
  return { profileDone, dataDone, dataPending };
}

function migratePackage(pkg, localFileSource) {
  const before = JSON.stringify(pkg);
  const deps = { ...(pkg.dependencies || {}) };
  const hadOldDep = Object.prototype.hasOwnProperty.call(deps, OLD_NAME);
  const oldDepValue = hadOldDep ? deps[OLD_NAME] : null;
  if (hadOldDep) {
    delete deps[OLD_NAME];
    deps[NEW_NAME] = localFileSource || oldDepValue || '2.0.0';
  }
  const bundles = [...(pkg.dsh?.profile?.bundles || [])];
  const hadOldBundle = bundles.includes(OLD_NAME);
  const nextBundles = bundles.map((b) => (b === OLD_NAME ? NEW_NAME : b));
  if (!nextBundles.includes(NEW_NAME) && (hadOldDep || hadOldBundle)) {
    nextBundles.push(NEW_NAME);
  }
  // Preserve other keys byte-wise via structured clone of untouched fields
  const next = {
    ...pkg,
    dependencies: deps,
    dsh: {
      ...(pkg.dsh || {}),
      profile: {
        ...(pkg.dsh?.profile || {}),
        bundles: nextBundles,
      },
    },
  };
  return {
    pkg: next,
    changed: JSON.stringify(next) !== before,
    hadOldDep,
    hadOldBundle,
    oldDepValue,
  };
}

function findLatestBackup(profilePath, prefix) {
  const backupsDir = join(profilePath, '_backups');
  if (!existsSync(backupsDir)) return null;
  const dirs = readdirSync(backupsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && d.name.startsWith(prefix))
    .map((d) => d.name)
    .sort();
  if (!dirs.length) return null;
  return join(backupsDir, dirs[dirs.length - 1]);
}

function restoreBackup(profilePath, backupRoot) {
  const restored = [];
  for (const name of ['package.json', 'cordis.patch.yml', 'pnpm-lock.yaml']) {
    const src = join(backupRoot, name);
    if (!existsSync(src)) continue;
    copyFileSync(src, join(profilePath, name));
    restored.push(name);
  }
  return restored;
}

function runMigrateLegacy(dataDir) {
  const pyCandidates = [
    join(REPO_ROOT, 'service', '.venv', 'Scripts', 'python.exe'),
    join(REPO_ROOT, 'service', '.venv', 'bin', 'python'),
    process.env.PYTHON || '',
    'py',
  ].filter(Boolean);
  let python = null;
  for (const c of pyCandidates) {
    if (c === 'py' || existsSync(c)) {
      python = c;
      break;
    }
  }
  if (!python) {
    return {
      ok: false,
      code: 'python_missing',
      error: 'no python for memory-bank migrate-legacy',
      hint: 'Install Python 3.10+ or set service/.venv',
    };
  }
  const env = {
    ...process.env,
    PYTHONPATH: join(REPO_ROOT, 'service', 'src'),
  };
  if (dataDir) env.MNEMOSYNE_MEMORY_BANK = dataDir;
  const argv =
    python === 'py'
      ? ['-3', '-m', 'cognitive_memory.node_cli', 'memory-bank', 'migrate-legacy']
      : ['-m', 'cognitive_memory.node_cli', 'memory-bank', 'migrate-legacy'];
  const res = spawnSync(python, argv, {
    encoding: 'utf8',
    cwd: join(REPO_ROOT, 'service'),
    env,
    windowsHide: true,
    shell: python === 'py',
  });
  return {
    ok: (res.status ?? 1) === 0,
    status: res.status,
    stdout: String(res.stdout || '').slice(0, 4000),
    stderr: String(res.stderr || '').slice(0, 4000),
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(`Usage: node scripts/migrate-dsh.mjs --profile <name> [--rollback] [--skip-data] [--data-dir <bank>]
Env: DSH_HOME, DSH_PROFILE, MNEMOSYNE_MEMORY_BANK`);
    process.exitCode = 0;
    return;
  }

  const dshHome = resolveDshHome(args.dshHome);
  const profile = resolveProfile(args, dshHome);
  const pdir = join(dshHome, 'profiles', profile);
  const pkgPath = join(pdir, 'package.json');

  if (!existsSync(pkgPath)) {
    return fail(
      'profile_missing',
      `profile not found: ${pkgPath}`,
      'Pass --profile under temporary DSH_HOME; do not invent a silent success',
      { dshHome, profile },
    );
  }

  if (args.rollback) {
    const statePath = join(pdir, STATE_FILE);
    let backupRoot = null;
    if (existsSync(statePath)) {
      try {
        backupRoot = readJson(statePath).backupRoot || null;
      } catch {
        backupRoot = null;
      }
    }
    if (!backupRoot || !existsSync(backupRoot)) {
      backupRoot = findLatestBackup(pdir, 'migrate-');
    }
    if (!backupRoot || !existsSync(backupRoot)) {
      return fail(
        'rollback_backup_missing',
        'no migrate backup found',
        'Cannot rollback without _backups/migrate-*',
        { profile },
      );
    }
    const before = readFileSync(pkgPath, 'utf8');
    const restored = restoreBackup(pdir, backupRoot);
    const after = readFileSync(pkgPath, 'utf8');
    // drop state file after rollback
    try {
      if (existsSync(statePath)) rmSync(statePath);
    } catch {
      /* ignore */
    }
    return ok({
      action: 'rollback',
      profile,
      backupRoot,
      restored,
      package_restored: before !== after || restored.includes('package.json'),
      data_dir_deleted: false,
    });
  }

  let pkg = readJson(pkgPath);
  const localSource = `file:${REPO_ROOT.replace(/\\/g, '/')}`;
  const migrated = migratePackage(pkg, localSource);
  const cordisPath = join(pdir, 'cordis.patch.yml');
  const cordisBefore = existsSync(cordisPath) ? readFileSync(cordisPath, 'utf8') : '';
  const cordis = rewriteCordis(cordisBefore);
  const priorState = readMigrateState(pdir) || {};
  const phases = phaseStatus(priorState);

  const ownedIdLine = /(^|\n)[ \t]*-?[ \t]*id:\s*dsh-cognitive-memory(\s|$)/.test(cordisBefore);
  const needsProfile =
    migrated.hadOldDep || migrated.hadOldBundle || ownedIdLine || !phases.profileDone;

  if (!needsProfile) {
    // Profile already migrated — continue unfinished data stage when requested.
    if ((pkg.dependencies || {})[NEW_NAME] || (pkg.dsh?.profile?.bundles || []).includes(NEW_NAME)) {
      if (!args.skipData && (phases.dataPending || !phases.dataDone)) {
        const dataBefore = snapshotDataDir(args.dataDir);
        const dataResult = runMigrateLegacy(args.dataDir);
        const dataAfter = snapshotDataDir(args.dataDir);
        if (!dataResult.ok) {
          writeMigrateState(pdir, {
            ...priorState,
            profile_done: true,
            data_pending: true,
            data_done: false,
            updated_at: new Date().toISOString(),
            data_before: dataBefore,
            data_after: dataAfter,
            last_data_error: dataResult.error || dataResult.stderr || 'data_migrate_failed',
          });
          return fail(
            'data_migrate_failed',
            dataResult.error || dataResult.stderr || 'memory-bank migrate-legacy failed',
            dataResult.hint ||
              'Profile already migrated; fix Bank/Python and re-run to continue data stage.',
            {
              profile,
              action: 'data_continued',
              data: dataResult,
              data_before: dataBefore,
              data_after: dataAfter,
              data_dir_deleted: false,
              profile_migrated: true,
              phases: { profile_done: true, data_pending: true, data_done: false },
            },
          );
        }
        writeMigrateState(pdir, {
          ...priorState,
          profile_done: true,
          data_pending: false,
          data_done: true,
          finished_at: new Date().toISOString(),
          data_before: dataBefore,
          data_after: dataAfter,
        });
        return ok({
          action: 'data_continued',
          profile,
          reentrant: true,
          data: dataResult,
          data_before: dataBefore,
          data_after: dataAfter,
          data_dir_deleted: false,
          phases: { profile_done: true, data_pending: false, data_done: true },
        });
      }
      return ok({
        action: 'already_migrated',
        profile,
        reentrant: true,
        data_dir_deleted: false,
        data: {
          skipped: true,
          reason: phases.dataDone ? 'data_already_done' : 'skip_data_or_no_pending',
        },
        phases: {
          profile_done: true,
          data_pending: Boolean(phases.dataPending && !phases.dataDone),
          data_done: Boolean(phases.dataDone),
        },
        hint: 'Nothing to do; safe to re-run',
      });
    }
    return fail(
      'legacy_bundle_missing',
      `profile has no ${OLD_NAME} to migrate`,
      'Install legacy bundle first or skip migrate',
      { profile },
    );
  }

  const dataBefore = snapshotDataDir(args.dataDir);
  const backup = backupProfile(pdir, 'migrate');
  const state = {
    ...priorState,
    backupRoot: backup.backupRoot,
    profile,
    dshHome,
    started_at: priorState.started_at || new Date().toISOString(),
    data_before: dataBefore,
    profile_done: false,
    data_pending: !args.skipData,
    data_done: false,
  };
  writeMigrateState(pdir, state);

  // Apply package + cordis
  writeJson(pkgPath, migrated.pkg);
  if (cordis.changed || ownedIdLine) {
    writeFileSync(cordisPath, cordis.text.endsWith('\n') ? cordis.text : `${cordis.text}\n`, 'utf8');
  }

  // Verify other bundles preserved
  const beforeBundles = (pkg.dsh?.profile?.bundles || []).filter((b) => b !== OLD_NAME);
  const afterBundles = migrated.pkg.dsh.profile.bundles.filter((b) => b !== NEW_NAME);
  const lost = beforeBundles.filter((b) => !migrated.pkg.dsh.profile.bundles.includes(b));
  if (lost.length) {
    restoreBackup(pdir, backup.backupRoot);
    return fail(
      'other_entries_lost',
      `migration would drop bundles: ${lost.join(',')}`,
      'Restored backup; report bug',
      { backup },
    );
  }

  state.profile_done = true;
  state.data_pending = true;
  state.data_done = false;
  writeMigrateState(pdir, state);

  let dataResult = { skipped: true, reason: 'skip_data' };
  if (!args.skipData) {
    dataResult = runMigrateLegacy(args.dataDir);
    if (!dataResult.ok) {
      // Data migrate failure is visible but profile migrate already applied;
      // leave profile as-is (re-entrant) and report structured error for data half.
      const dataAfter = snapshotDataDir(args.dataDir);
      state.data_pending = true;
      state.data_done = false;
      state.data_after = dataAfter;
      state.last_data_error = dataResult.error || dataResult.stderr || 'data_migrate_failed';
      writeMigrateState(pdir, state);
      return fail(
        'data_migrate_failed',
        dataResult.error || dataResult.stderr || 'memory-bank migrate-legacy failed',
        dataResult.hint ||
          'Profile bundle rename kept; fix Python/Bank path and re-run (re-entrant). Data was not deleted.',
        {
          profile,
          backup,
          data: dataResult,
          dataResult,
          data_before: dataBefore,
          data_after: dataAfter,
          data_dir_deleted: false,
          profile_migrated: true,
          phases: { profile_done: true, data_pending: true, data_done: false },
        },
      );
    }
    state.data_pending = false;
    state.data_done = true;
  }

  const dataAfter = snapshotDataDir(args.dataDir);
  state.finished_at = new Date().toISOString();
  state.data_after = dataAfter;
  writeMigrateState(pdir, state);

  return ok({
    action: 'migrated',
    profile,
    backup,
    had_old_dependency: migrated.hadOldDep,
    had_old_bundle: migrated.hadOldBundle,
    bundles: migrated.pkg.dsh.profile.bundles,
    preserved_other_bundles: afterBundles,
    cordis_changed: cordis.changed || ownedIdLine,
    data: dataResult,
    data_before: dataBefore,
    data_after: dataAfter,
    data_dir_deleted: false,
    reentrant: true,
    phases: {
      profile_done: true,
      data_pending: Boolean(state.data_pending),
      data_done: Boolean(state.data_done),
    },
    rollback: {
      command: `node scripts/migrate-dsh.mjs --profile ${profile} --dsh-home ${dshHome} --rollback`,
      backup: backup.backupRoot,
    },
  });
}

main();
