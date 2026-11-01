#!/usr/bin/env node
/**
 * Stable DSH self-install for dsh-mnemosyne (thin host).
 * Does not touch Memory Bank / SQLite / private tokens.
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
const PKG_NAME = 'dsh-mnemosyne';
const BACKUP_KEEP = 5;

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
    local: false,
    tag: null,
    profile: null,
    dryRun: false,
    acceptVersionRisk: false,
    skipPostCheck: false,
    dshHome: process.env.DSH_HOME || null,
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--local') args.local = true;
    else if (a === '--tag') args.tag = argv[++i];
    else if (a === '--profile') args.profile = argv[++i];
    else if (a === '--dry-run') args.dryRun = true;
    else if (a === '--accept-version-risk') args.acceptVersionRisk = true;
    else if (a === '--skip-post-check') args.skipPostCheck = true;
    else if (a === '--dsh-home') args.dshHome = argv[++i];
    else if (a === '--help' || a === '-h') args.help = true;
  }
  if (!args.tag && !args.local) args.local = true;
  return args;
}

function resolveDshHome(explicit) {
  if (explicit) return resolve(explicit);
  if (process.env.DSH_HOME) return resolve(process.env.DSH_HOME);
  return join(homedir(), '.dsh');
}

function resolveDshCli() {
  if (process.env.DSH_CLI) {
    const p = resolve(process.env.DSH_CLI);
    if (!existsSync(p)) {
      return { kind: 'missing', path: p, error: 'DSH_CLI points to missing file' };
    }
    return { kind: 'node-script', path: p };
  }
  const which = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['dsh'], {
    encoding: 'utf8',
  });
  const first = String(which.stdout || '')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)[0];
  if (first && existsSync(first)) return { kind: 'bin', path: first };

  for (const home of [process.env.DSH_HOME, join(homedir(), '.dsh')].filter(Boolean)) {
    const cand = join(home, 'profiles', 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');
    if (existsSync(cand)) return { kind: 'node-script', path: cand };
  }
  return null;
}

function detectRunningProfile() {
  if (process.platform !== 'win32') return null;
  try {
    const ps = spawnSync(
      'powershell',
      [
        '-NoProfile',
        '-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'profiles[/\\\\]([^/\\\\]+)' } | Select-Object -ExpandProperty CommandLine",
      ],
      { encoding: 'utf8', windowsHide: true },
    );
    const m = String(ps.stdout || '').match(/profiles[/\\]([^/\\\s"']+)/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

function detectRecentProfile(dshHome) {
  try {
    const profilesDir = join(dshHome, 'profiles');
    if (!existsSync(profilesDir)) return null;
    const names = readdirSync(profilesDir, { withFileTypes: true })
      .filter((d) => d.isDirectory() && d.name !== 'node_modules')
      .map((d) => d.name);
    if (names.includes('desktop')) return 'desktop';
    if (names.includes('web')) return 'web';
    return names[0] || null;
  } catch {
    return null;
  }
}

function resolveProfile(args, dshHome) {
  if (args.profile) return { profile: args.profile, source: 'cli' };
  if (process.env.DSH_PROFILE) return { profile: process.env.DSH_PROFILE, source: 'env' };
  const running = detectRunningProfile();
  if (running) return { profile: running, source: 'running_process' };
  const recent = detectRecentProfile(dshHome);
  if (recent) return { profile: recent, source: 'recent' };
  return { profile: 'web', source: 'default' };
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function backupProfile(profilePath) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupRoot = join(profilePath, '_backups', `install-${stamp}`);
  mkdirSync(backupRoot, { recursive: true });
  const saved = [];
  for (const name of ['package.json', 'cordis.patch.yml', 'pnpm-lock.yaml']) {
    const src = join(profilePath, name);
    if (!existsSync(src)) continue;
    copyFileSync(src, join(backupRoot, name));
    saved.push(name);
  }
  const backupsDir = join(profilePath, '_backups');
  try {
    const dirs = readdirSync(backupsDir, { withFileTypes: true })
      .filter((d) => d.isDirectory() && d.name.startsWith('install-'))
      .map((d) => d.name)
      .sort();
    while (dirs.length > BACKUP_KEEP) {
      const old = dirs.shift();
      try {
        rmSync(join(backupsDir, old), { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    }
  } catch {
    /* ignore */
  }
  return { backupRoot, saved };
}

function alreadyInstalled(pkg) {
  const dep = pkg?.dependencies?.[PKG_NAME];
  const bundles = pkg?.dsh?.profile?.bundles || [];
  const inBundles = bundles.includes(PKG_NAME);
  if (!dep || !inBundles) return { same: false, dep, inBundles };
  if (String(dep).startsWith('file:') && inBundles) {
    return { same: true, dep, inBundles, reason: 'local_file_present' };
  }
  return { same: true, dep, inBundles, reason: 'dep_and_bundle_present' };
}

function buildSource(args) {
  if (args.tag) return `github:canyueY/dsh-mnemosyne#${args.tag}`;
  return `file:${REPO_ROOT.replace(/\\/g, '/')}`;
}

function runDsh(cli, dshArgs, env) {
  const fullEnv = { ...process.env, ...env };
  let cmd;
  let argv;
  if (cli.kind === 'node-script') {
    cmd = process.execPath;
    argv = [cli.path, ...dshArgs];
  } else if (process.platform === 'win32' && /\.cmd$/i.test(cli.path)) {
    cmd = 'cmd.exe';
    argv = ['/d', '/s', '/c', cli.path, ...dshArgs];
  } else {
    cmd = cli.path;
    argv = dshArgs;
  }
  const res = spawnSync(cmd, argv, {
    encoding: 'utf8',
    env: fullEnv,
    windowsHide: true,
    cwd: REPO_ROOT,
  });
  return {
    status: res.status ?? 1,
    stdout: String(res.stdout || ''),
    stderr: String(res.stderr || ''),
    error: res.error ? String(res.error.message || res.error) : null,
    cmd,
    argv,
  };
}

function postCheck(args) {
  if (args.skipPostCheck) return { skipped: true, ok: true, results: [] };
  const results = [];
  const check = spawnSync('npm', ['run', 'check'], {
    encoding: 'utf8',
    cwd: REPO_ROOT,
    shell: process.platform === 'win32',
    windowsHide: true,
  });
  results.push({ name: 'npm_run_check', ok: (check.status ?? 1) === 0, status: check.status });
  const host = spawnSync(process.execPath, ['--test', 'src/test/host-compat-021.mjs'], {
    encoding: 'utf8',
    cwd: REPO_ROOT,
    windowsHide: true,
  });
  results.push({ name: 'host_compat_021', ok: (host.status ?? 1) === 0, status: host.status });
  return { skipped: false, ok: results.every((r) => r.ok), results };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(`Usage: node scripts/install-dsh.mjs [--local|--tag <ver>] [--profile <name>]
  --accept-version-risk   allow-version when engines.dsh mismatches local CLI
  --dry-run               resolve without mutating
  --dsh-home <path>       override DSH_HOME
  --skip-post-check       skip npm check / host-compat
Env: DSH_HOME, DSH_PROFILE, DSH_CLI`);
    process.exitCode = 0;
    return;
  }

  const dshHome = resolveDshHome(args.dshHome);
  const { profile, source: profileSource } = resolveProfile(args, dshHome);
  const pdir = join(dshHome, 'profiles', profile);
  const pkgPath = join(pdir, 'package.json');
  const cli = resolveDshCli();

  if (!cli || cli.kind === 'missing') {
    return fail(
      'dsh_cli_missing',
      cli?.error || 'DSH CLI not found (PATH / DSH_CLI / profiles/node_modules/@deepseek-ai/dsh)',
      'Set DSH_CLI to @deepseek-ai/dsh/lib/bin.js or put dsh on PATH',
      { dshHome, profile, dshCli: cli?.path || null },
    );
  }

  if (!existsSync(pkgPath)) {
    return fail(
      'profile_missing',
      `profile package.json not found: ${pkgPath}`,
      `Create profile under ${join(dshHome, 'profiles')} or pass --profile <existing>`,
      { dshHome, profile, profileSource },
    );
  }

  let pkg;
  try {
    pkg = readJson(pkgPath);
  } catch (err) {
    return fail('profile_invalid', `cannot parse package.json: ${err.message}`, 'Fix profile JSON', {
      profile,
    });
  }

  const repoPkg = readJson(join(REPO_ROOT, 'package.json'));
  const repoVersion = String(repoPkg.version || '0.0.0');
  const present = alreadyInstalled(pkg);
  const otherBundles = (pkg.dsh?.profile?.bundles || []).filter((b) => b !== PKG_NAME);
  const source = buildSource(args);

  if (present.same) {
    return ok({
      action: 'skipped_idempotent',
      profile,
      profileSource,
      dshHome,
      source,
      dependency: present.dep,
      bundles: pkg.dsh?.profile?.bundles || [],
      preserved_other_bundles: otherBundles,
      rollback: {
        command: `dsh plugin --profile ${profile} remove ${PKG_NAME}`,
        hint: 'Restore package.json/cordis from _backups/install-* if needed',
      },
    });
  }

  if (args.dryRun) {
    return ok({
      action: 'dry_run',
      profile,
      profileSource,
      dshHome,
      source,
      would_install: true,
      preserved_other_bundles: otherBundles,
    });
  }

  const backup = backupProfile(pdir);
  const env = { DSH_HOME: dshHome };

  if (args.acceptVersionRisk) {
    const dshVerRes = runDsh(cli, ['--version'], env);
    const dshVer = String(dshVerRes.stdout || dshVerRes.stderr || '')
      .trim()
      .split(/\r?\n/)
      .filter(Boolean)
      .pop();
    if (dshVer) {
      const allow = runDsh(
        cli,
        [
          'plugin',
          '--profile',
          profile,
          'allow-version',
          `${PKG_NAME}@${repoVersion}`,
          '--dsh-version',
          dshVer,
          '--accept-risk',
        ],
        env,
      );
      if (allow.status !== 0) {
        return fail(
          'allow_version_failed',
          (allow.stderr || allow.stdout || 'allow-version failed').slice(0, 2000),
          'Upgrade DSH to engines.dsh range, or fix allow-version',
          { profile, dshVer },
        );
      }
    }
  }

  const add = runDsh(cli, ['plugin', '--profile', profile, 'add', source], env);
  if (add.status !== 0) {
    const combined = `${add.stderr}\n${add.stdout}`;
    let code = 'install_failed';
    let hint = 'Inspect profile .plugin-manager/logs; restore from backup if needed';
    if (/incompatible with dsh/i.test(combined)) {
      code = 'version_mismatch';
      hint =
        'engines.dsh requires >=0.2.1-0 <0.3.0; upgrade DSH or re-run with --accept-version-risk';
    } else if (/nothing was installed/i.test(combined)) {
      code = 'nothing_installed';
    }
    return fail(code, combined.trim().slice(0, 2000) || add.error || 'dsh plugin add failed', hint, {
      profile,
      source,
      backup,
      dsh: { status: add.status },
    });
  }

  let after;
  try {
    after = readJson(pkgPath);
  } catch (err) {
    return fail('post_read_failed', err.message, 'Partial install — restore backup', { backup });
  }
  const bundles = after?.dsh?.profile?.bundles || [];
  if (!bundles.includes(PKG_NAME)) {
    return fail(
      'bundle_not_appended',
      'add succeeded but dsh.profile.bundles lacks dsh-mnemosyne',
      'Check plugin-manager logs; restore backup',
      { backup, bundles },
    );
  }
  const missingOthers = otherBundles.filter((b) => !bundles.includes(b));
  if (missingOthers.length) {
    return fail(
      'other_bundles_lost',
      `other bundles missing: ${missingOthers.join(',')}`,
      `Restore ${backup.backupRoot}`,
      { backup, bundles },
    );
  }

  const checks = postCheck(args);
  if (!checks.skipped && !checks.ok) {
    return fail(
      'post_check_failed',
      'install ok but post-check failed',
      'Fix host checks; remove plugin or restore backup',
      { backup, checks, bundles },
    );
  }

  return ok({
    action: 'installed',
    profile,
    profileSource,
    dshHome,
    source,
    version: repoVersion,
    bundles,
    preserved_other_bundles: otherBundles,
    backup,
    checks,
    rollback: {
      command: `dsh plugin --profile ${profile} remove ${PKG_NAME}`,
      backup: backup.backupRoot,
      restore_hint: `Copy files from ${backup.backupRoot} back to ${pdir}`,
    },
  });
}

main();
