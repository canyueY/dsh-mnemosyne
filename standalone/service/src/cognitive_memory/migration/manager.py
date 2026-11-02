"""Provider registry and file adoption lifecycle; source data is always read-only."""
import csv
import hashlib
import json
from pathlib import Path
from cognitive_memory.migration.interchange import InterchangeRecord,parse_interchange
from cognitive_memory.migration.importers import iter_markdown,iter_sqlite_table
from cognitive_memory.migration.mem0_adapter import iter_mem0_payloads
from cognitive_memory.migration.staging import StagingStore,preview_batch,approve_batch,commit_batch,rollback_batch

PROVIDERS={
    "json":"JSON array/object export","jsonl":"JSON Lines export","csv":"CSV export",
    "markdown":"Markdown notes","sqlite":"SQLite memories table (read-only)",
    "mem0":"Mem0 record export","zep":"Zep/Graphiti facts export",
    "letta":"Letta blocks/archive export","langchain":"LangChain document export",
}


def read_provider(path,provider):
    path=Path(path).expanduser().resolve()
    if provider not in PROVIDERS:
        raise ValueError("unsupported_provider")
    if provider=="markdown":
        return list(iter_markdown(path))
    if provider=="sqlite":
        return list(iter_sqlite_table(path))
    if provider=="csv":
        with path.open(encoding="utf-8-sig",newline="") as f:
            items=list(csv.DictReader(f))
    else:
        text=path.read_text(encoding="utf-8-sig")
        items=[json.loads(line) for line in text.splitlines() if line.strip()] if provider=="jsonl" else json.loads(text)
        if isinstance(items,dict):
            items=items.get("results",items.get("memories",items.get("blocks",items.get("documents",[items]))))
    if not isinstance(items,list):
        raise ValueError("provider_export_must_contain_records")
    if provider=="mem0":
        return list(iter_mem0_payloads(items))
    records=[]
    source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    for i,item in enumerate(items):
        obj=item if isinstance(item,dict) else {"content":str(item)}
        if "source_system" in obj and "source_record_id" in obj:
            records.append(parse_interchange(obj))
            continue
        content=obj.get("memory") or obj.get("fact") or obj.get("page_content") or obj.get("value") or obj.get("content") or obj.get("text") or ""
        if not isinstance(content,str):
            raise ValueError(f"record_{i}_requires_explicit_content_mapping")
        external_id=obj.get("id") or obj.get("uuid") or obj.get("label")
        source_id=str(external_id) if external_id is not None else hashlib.sha256(
            json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        records.append(InterchangeRecord(
            source_system=provider,source_record_id=source_id,
            content={"text":content,"format":provider},
            provenance={"source_type":"external_memory","importer":f"{provider}.export.v1",
                        "source_file_sha256":source_hash,
                        "original_source_status":"unverified"},
            time={k:obj[k] for k in ("created_at","updated_at","valid_at","invalid_at") if k in obj},
            extensions={"provider_metadata":obj.get("metadata") or {}},
            raw_payload=obj,
        ).with_checksum())
    return records


def inspect_source(path,provider):
    records=read_provider(path,provider)
    return {"provider":provider,"count":len(records),"source_sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "mapping":"export_records_only","requires_review":sum(not r.content.get("text") for r in records),
            "warnings":["Imported records remain candidates; source claims are not verified."]}


def verify_batch(batch,service):
    report=batch.commit_report or {}
    expected={r.to_source_record().source_id:r.to_source_record().content for r in map(parse_interchange,batch.records)}
    checks=[]
    for row in report.get("written",[]):
        mid=row.get("memory_id")
        stored=service.db.connect().execute("SELECT content FROM memories WHERE id=?",(mid,)).fetchone()
        checks.append({"source_id":row["source_id"],"memory_id":mid,
                       "exists":stored is not None,
                       "content_equal":stored is not None and stored[0]==expected.get(row["source_id"])})
    return {"ok":batch.state=="committed" and not report.get("failed") and
            len(checks)==len(expected) and all(c["exists"] and c["content_equal"] for c in checks),
            "batch_id":batch.batch_id,"expected_count":len(expected),"checks":checks,
            "note":"verifies stored content and source-ID mapping; no source system is disabled"}


def cli_action(args):
    from cognitive_memory.bootstrap import build_context
    action=args.memory_action
    if action=="providers":
        return {"ok":True,"providers":PROVIDERS,"online_provider_discovery":False}
    if action=="discover":
        root=Path(args.source or ".").resolve()
        return {"ok":True,"root":str(root),"files":[{"path":str(p),"suffix":p.suffix}
                for p in root.iterdir() if p.is_file() and p.suffix.lower() in {".jsonl",".md",".csv",".sqlite",".db"}],
                "read_only":True}
    if action=="inspect":
        return {"ok":True,**inspect_source(args.source,args.provider)}
    from cognitive_memory.storage.paths import default_data_root
    root=Path(args.data_dir).resolve() if args.data_dir else default_data_root()
    store=StagingStore(root/"migration/staging")
    action=args.migration_action
    if action=="plan":
        batch=preview_batch(read_provider(args.source,args.provider),store=store)
        return {"ok":True,"batch_id":batch.batch_id,"checksum":batch.checksum,"count":len(batch.records),
                "preview":inspect_source(args.source,args.provider),"state":batch.state}
    if action=="history":
        return {"ok":True,"batches":[{"batch_id":b.batch_id,"state":b.state,"report":b.commit_report}
                for p in store.root.glob("*.json") if (b:=store.load(p.stem))]}
    batch=store.load(args.batch)
    if batch is None:
        return {"ok":False,"detail":"batch_not_found"}
    if action in {"run","rollback"} and not args.confirm:
        return {"ok":False,"detail":"approval_required"}
    ctx=build_context(data_dir=str(root),token="migration-local-cli")
    try:
        if action=="run":
            if batch.state!="committed":
                approve_batch(batch,actor="local_operator",expected_checksum=args.checksum,store=store)
                commit_batch(batch,service=ctx.service,store=store)
            return {"ok":batch.state=="committed","batch":batch.to_dict(),"verification":verify_batch(batch,ctx.service)}
        if action=="verify":
            return verify_batch(batch,ctx.service)
        rollback_batch(batch,service=ctx.service,store=store,layer="soft")
        return {"ok":batch.state=="rolled_back","batch":batch.to_dict()}
    finally:
        ctx.db.close()
