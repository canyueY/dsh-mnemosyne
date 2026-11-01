import concurrent.futures, json, os, subprocess, time, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PY = ROOT / 'service/.venv/Scripts/python.exe'
env = dict(os.environ, PYTHONIOENCODING='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
TEMP = ROOT.parent / '.mnemosyne-recheck-temp-20261101'
TEMP.mkdir(exist_ok=True)
env['TEMP'] = env['TMP'] = str(TEMP)
env['npm_config_cache'] = str(TEMP / 'npm-cache')
for key in ['MNEMOSYNE_MEMORY_BANK', 'MNEMOSYNE_DATA_DIR', 'DSH_MEMORY_DATA_DIR']:
    env[key] = str(HERE / 'isolated-bank')
env['DSH_HOME'] = str(HERE / 'isolated-dsh-home')
commands = [
 ('python-full', [str(PY), '-m', 'pytest', 'tests', '-q'], ROOT / 'service'),
 ('node-full', ['npm.cmd', 'test'], ROOT),
 ('node-check', ['npm.cmd', 'run', 'check'], ROOT),
 ('sdk-build', ['npm.cmd', 'run', 'build'], ROOT / 'service/sdk-ts'),
 ('diff-check', ['git', 'diff', '--check', 'HEAD~1', 'HEAD'], ROOT),
 ('original-python-counterexamples', [str(PY), str(HERE / 'reproduce_review.py')], ROOT),
 ('original-dsh-counterexamples', ['node', str(HERE / 'reproduce_dsh.mjs')], ROOT),
]
def run(item):
    name, cmd, cwd = item
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=240)
        output = (proc.stdout + proc.stderr).decode('utf-8', 'replace')
        result = dict(name=name, cmd=cmd, exit=proc.returncode, seconds=round(time.monotonic()-start, 2), tail=output[-2400:])
    except Exception as exc:
        output = str(exc)
        result = dict(name=name, error=output)
    (HERE / (name + '.log')).write_text(output, encoding='utf-8')
    (HERE / (name + '.result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(run, [item for item in commands if item[0] != 'node-full']))
results.append(run(next(item for item in commands if item[0] == 'node-full')))
results.append(run(('pack-list', ['npm.cmd', 'pack', '--dry-run', '--json', '--ignore-scripts'], ROOT)))
(HERE / 'checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
