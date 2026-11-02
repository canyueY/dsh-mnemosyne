---
name: mnemosyne-onboarding
description: Discover, assess, configure, verify, upgrade and recover a local Mnemosyne Node, or adopt exported records from another memory system using reviewed migration batches.
---

# Mnemosyne onboarding

Locate the installed `mnemosyne` CLI or run `python -m cognitive_memory.node_cli` from the service environment. Read `discover --json` before choosing a target. The bootstrap manifest and the component manifest describe different contracts.

Use `mnemosyne --data-dir BANK diagnose --target standalone --json` for read-only checks. This does not open or migrate the target database. `doctor` performs deeper local initialization and is not a read-only substitute.

Generate a concrete plan with `mnemosyne --data-dir BANK plan --target standalone -o PLAN.json`. Supported targets are standalone, rest, mcp and dsh. Plan output describes local runtime configuration; installing missing Python or Node dependencies remains an environment setup action. A DSH target requires the bundled root adapter. No cloud account is required.

Review the plan hash, source checksum, target directory, checks and permissions against the user's authorization. Apply an authorized plan with `install --plan PLAN.json --confirm`. Source or configuration changes invalidate the plan. Existing databases require a fresh `plan --upgrade` followed by `upgrade --plan PLAN.json --confirm`. Keep the returned recovery receipt.

Run `verify --json`. It checks the selected bank read-only and exercises persistence, isolation and backup in a disposable bank. It does not prove that a remote Agent or the DSH GUI is connected. Use `connect --url URL` for an authenticated health handshake, and `--confirm` only when saving that connection is authorized. Tokens should be supplied through the configured token environment; do not print or include them in reports.

For failed upgrades, stop the Node and use `rollback --receipt RECEIPT --confirm` with the same bank. Existing database/configuration snapshots are restored; a failed fresh bank is retained for inspection. Do not treat rollback as uninstall or delete a user's data directory.

For external memory, use `memory providers` and `memory inspect --provider PROVIDER --source FILE`. Provider support is for exported records, not an assertion of full online vendor API compatibility. Unknown source facts remain unverified. Raw provider fields and external IDs are retained.

Generate `memory migrate plan --provider PROVIDER --source FILE`. Review the returned batch ID, count and checksum. Execute an authorized batch with `memory migrate run --batch ID --checksum CHECKSUM --confirm`, then `memory migrate verify --batch ID`. A false verification result, including truncation or conflicting revisions, prevents declaring migration complete. `memory migrate rollback --batch ID --confirm` invalidates only memories created by that batch; previously present memories are preserved.

The Mem0 compatibility surface is explicitly a record subset under `/api/v1/compat/mem0`. Soft invalidation is not physical erasure. Cross-node federation and vendor-specific online synchronization require separately validated integrations; never infer them from export import success.
