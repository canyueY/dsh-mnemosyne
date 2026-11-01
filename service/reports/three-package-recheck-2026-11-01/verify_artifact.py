import json, os, subprocess, sys, tarfile, tempfile, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT.parent / '.mnemosyne-recheck-temp-20261101'
OUT.mkdir(exist_ok=True)
env = dict(os.environ, TEMP=str(OUT), TMP=str(OUT), npm_config_cache=str(OUT/'npm-cache'))
sys.stdout.reconfigure(encoding='utf-8')
packed = subprocess.run(['npm.cmd','pack','--json','--ignore-scripts','--pack-destination',str(OUT)], cwd=ROOT, env=env, capture_output=True, timeout=90)
(HERE/'pack-actual.log').write_bytes(packed.stdout+packed.stderr)
assert packed.returncode == 0, packed.stderr.decode('utf-8','replace')
metadata = json.loads(packed.stdout.decode('utf-8'))[0]
target = Path(tempfile.mkdtemp(prefix='unpacked-', dir=OUT))
with tarfile.open(OUT/metadata['filename']) as archive:
    for member in archive.getmembers():
        assert (target/member.name).resolve().is_relative_to(target.resolve())
        assert not member.issym() and not member.islnk()
    archive.extractall(target)
package = target/'package'
results=[]
for script in ['install-dsh.mjs','migrate-dsh.mjs']:
    proc = subprocess.run(['node',str(package/'scripts'/script),'--help'],cwd=package,env=env,capture_output=True,timeout=15)
    results.append(dict(case=script+' --help from tarball', exit=proc.returncode, output=proc.stdout.decode('utf-8','replace')))
module=(package/'src/agent-preset.js').as_uri()
code='const m = await import('+json.dumps(module)+'); console.log(JSON.stringify(m.loadMnemosynePreset()));'
proc=subprocess.run(['node','--input-type=module','-e',code],cwd=package,env=env,capture_output=True,timeout=15)
results.append(dict(case='load preset from tarball',exit=proc.returncode,preset=json.loads(proc.stdout) if proc.returncode==0 else None))
requests=[]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(dict(method='GET',path=self.path,auth_correct=self.headers.get('Authorization')=='Bearer isolated-mcp-token'))
        body=json.dumps({'ok':True,'service':'isolated-review-http-fixture'}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'isolated-review','version':'1'}}}, {'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/list'}, {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'memory_health','arguments':{}}}]
    proc=subprocess.run(['node',str(package/'src/mcp-gateway.js')],input=''.join(json.dumps(x)+'\n' for x in messages).encode(),cwd=package,env=dict(env,MNEMOSYNE_BASE_URL=f'http://127.0.0.1:{server.server_port}',MNEMOSYNE_TOKEN='isolated-mcp-token'),capture_output=True,timeout=15)
    replies=[json.loads(line) for line in proc.stdout.decode().splitlines() if line]
    results.append(dict(case='packed MCP stdio to actual HTTP fixture',exit=proc.returncode,replies=replies,http_requests=requests,limit='Local HTTP fixture, not production Node or real DSH session'))
finally:
    server.shutdown();server.server_close()
files={item['path'] for item in metadata['files']}
evidence=dict(pack_entry_count=len(files),required_files={f:f in files for f in ['scripts/install-dsh.mjs','scripts/migrate-dsh.mjs','dsh/agent-presets/mnemosyne/agent.cordis.yml','src/preset-guard.js']},checks=results)
(HERE/'artifact-and-mcp.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(pack_entry_count=len(files),required_files=evidence['required_files'],checks=[dict(case=x['case'],exit=x['exit']) for x in results],http_requests=requests),ensure_ascii=False,indent=2))
