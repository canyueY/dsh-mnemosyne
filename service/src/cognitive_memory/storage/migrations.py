# -*- coding: utf-8 -*-
"""SQLite schema migrations（V1.0 核心表）。"""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_core",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY,
          source_kind TEXT NOT NULL,
          content TEXT NOT NULL,
          occurred_at TEXT,
          created_at TEXT NOT NULL,
          producer TEXT NOT NULL DEFAULT 'dsh',
          correlation_id TEXT,
          data_ref TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          status TEXT NOT NULL,
          content TEXT NOT NULL,
          source_ids_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_confirmed_at TEXT,
          version INTEGER NOT NULL DEFAULT 1,
          confidence REAL,
          expires_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS memory_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          snapshot_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(memory_id, version)
        );

        CREATE TABLE IF NOT EXISTS memory_evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id TEXT NOT NULL,
          observation_id TEXT NOT NULL,
          relation TEXT NOT NULL DEFAULT 'supports',
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          UNIQUE(memory_id, observation_id, relation)
        );

        CREATE TABLE IF NOT EXISTS memory_relations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id TEXT NOT NULL,
          useful INTEGER,
          incorrect INTEGER,
          note TEXT NOT NULL DEFAULT '',
          producer TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          event_type TEXT NOT NULL,
          event_version INTEGER NOT NULL DEFAULT 1,
          occurred_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          producer TEXT NOT NULL,
          correlation_id TEXT,
          causation_id TEXT,
          payload_version INTEGER NOT NULL DEFAULT 1,
          payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TEXT,
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inbox (
          event_id TEXT PRIMARY KEY,
          consumer TEXT NOT NULL,
          processed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY,
          task_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          payload_json TEXT NOT NULL DEFAULT '{}',
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          next_attempt_at TEXT
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
          key TEXT PRIMARY KEY,
          scope TEXT NOT NULL,
          response_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
          memory_id UNINDEXED,
          content,
          type UNINDEXED,
          status UNINDEXED,
          tokenize = 'unicode61'
        );

        CREATE INDEX IF NOT EXISTS idx_memories_type_status ON memories(type, status);
        CREATE INDEX IF NOT EXISTS idx_observations_kind ON observations(source_kind);
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, next_attempt_at);
        """,
    ),
    (
        "002_spatiotemporal",
        """
        -- 记忆间时空/演化关系扩展（仍属同一 Memory DB）
        ALTER TABLE memory_relations ADD COLUMN valid_from TEXT;
        ALTER TABLE memory_relations ADD COLUMN valid_until TEXT;
        ALTER TABLE memory_relations ADD COLUMN confidence REAL;
        ALTER TABLE memory_relations ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

        CREATE INDEX IF NOT EXISTS idx_memory_relations_from ON memory_relations(from_id);
        CREATE INDEX IF NOT EXISTS idx_memory_relations_to ON memory_relations(to_id);
        CREATE INDEX IF NOT EXISTS idx_memory_relations_rel ON memory_relations(relation);

        -- 变化候选审计（运行态，不是第二套个人记忆）
        CREATE TABLE IF NOT EXISTS change_candidates (
          id TEXT PRIMARY KEY,
          change_type TEXT NOT NULL,
          source_observation_id TEXT,
          target_memory_id TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          dry_run INTEGER NOT NULL DEFAULT 1,
          applied INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_change_candidates_applied ON change_candidates(applied, created_at);
        """,
    ),
    (
        "003_mnemosyne_emotion",
        """
        -- 情绪候选审计（运行态 shadow；非正式个人长期记忆）
        CREATE TABLE IF NOT EXISTS emotion_candidates (
          id TEXT PRIMARY KEY,
          observation_id TEXT,
          source_kind TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          shadow_only INTEGER NOT NULL DEFAULT 1,
          written INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_emotion_candidates_obs ON emotion_candidates(observation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_emotion_candidates_written ON emotion_candidates(written, created_at);
        """,
    ),
    (
        "004_change_plans",
        """
        -- ChangePlan：治理审计（非第二套个人记忆正文）
        CREATE TABLE IF NOT EXISTS change_plans (
          id TEXT PRIMARY KEY,
          observation_id TEXT,
          change_type TEXT NOT NULL,
          risk_level TEXT NOT NULL DEFAULT 'medium',
          confidence REAL,
          approval_requirement TEXT NOT NULL DEFAULT 'human',
          execution_status TEXT NOT NULL DEFAULT 'proposed',
          verification_status TEXT NOT NULL DEFAULT 'pending',
          payload_json TEXT NOT NULL DEFAULT '{}',
          rollback_reference TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_change_plans_status ON change_plans(execution_status, created_at);
        CREATE INDEX IF NOT EXISTS idx_change_plans_obs ON change_plans(observation_id, created_at);
        """,
    ),
    (
        "005_cognitive_schema_v2",
        """
        -- 认知图式：规律/假设（引用记忆证据，非正式个人 LT 正文）
        CREATE TABLE IF NOT EXISTS cognitive_schemas (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          schema_type TEXT NOT NULL DEFAULT 'KNOWLEDGE',
          state TEXT NOT NULL DEFAULT 'PROPOSED',
          confidence REAL NOT NULL DEFAULT 0.4,
          version INTEGER NOT NULL DEFAULT 1,
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cognitive_schemas_state ON cognitive_schemas(state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_cognitive_schemas_type ON cognitive_schemas(schema_type, state);

        CREATE TABLE IF NOT EXISTS schema_versions (
          id TEXT PRIMARY KEY,
          schema_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          change_type TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_schema_versions_sid ON schema_versions(schema_id, version);

        CREATE TABLE IF NOT EXISTS cognitive_change_plans (
          id TEXT PRIMARY KEY,
          operation TEXT NOT NULL,
          risk_level TEXT NOT NULL DEFAULT 'MEDIUM',
          approval_required INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'PROPOSED',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          applied_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cog_plans_status ON cognitive_change_plans(status, created_at);

        CREATE TABLE IF NOT EXISTS capability_permissions (
          capability TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 0,
          level TEXT NOT NULL DEFAULT 'LOCKED',
          requirements_json TEXT NOT NULL DEFAULT '[]',
          approved_at TEXT,
          expires_at TEXT,
          version INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS capability_audit (
          id TEXT PRIMARY KEY,
          capability TEXT NOT NULL,
          action TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_capability_audit_cap ON capability_audit(capability, created_at);
        """,
    ),
    (
        "006_identity_principals",
        """
        -- 单 Node Identity 最小模型（User / Node / Agent / External）
        CREATE TABLE IF NOT EXISTS identity_principals (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',
          external_ref TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_identity_principals_kind
          ON identity_principals(kind, id);
        """,
    ),
    (
        "007_evidence_bank_authority",
        """
        -- Evidence provenance 加固
        ALTER TABLE memory_evidence ADD COLUMN confidence REAL;
        ALTER TABLE memory_evidence ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT 'observation';
        ALTER TABLE memory_evidence ADD COLUMN validation_state TEXT NOT NULL DEFAULT 'unverified';
        ALTER TABLE memory_evidence ADD COLUMN producer TEXT NOT NULL DEFAULT '';
        ALTER TABLE memory_evidence ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

        -- Memory Bank 逻辑空间
        CREATE TABLE IF NOT EXISTS memory_banks (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        ALTER TABLE memories ADD COLUMN bank_id TEXT NOT NULL DEFAULT 'personal';
        CREATE INDEX IF NOT EXISTS idx_memories_bank ON memories(bank_id, status);

        -- Adoption Authority 五态
        CREATE TABLE IF NOT EXISTS authority_state (
          id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          previous_state TEXT,
          reason TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """,
    ),
    (
        "008_agent_grants_audit",
        """
        -- 同 Node 多 Agent：授权（权限/Bank/有效期/撤销）+ 审计
        CREATE TABLE IF NOT EXISTS agent_grants (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          token_hash TEXT NOT NULL,
          permissions_json TEXT NOT NULL DEFAULT '[]',
          banks_json TEXT NOT NULL DEFAULT '[]',
          consent_json TEXT NOT NULL DEFAULT '{}',
          expires_at TEXT,
          revoked_at TEXT,
          revoke_reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_grants_agent
          ON agent_grants(agent_id, revoked_at, expires_at);

        CREATE TABLE IF NOT EXISTS agent_audit (
          id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          action TEXT NOT NULL,
          resource TEXT NOT NULL DEFAULT '',
          decision TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_audit_agent
          ON agent_audit(agent_id, created_at);
        """,
    ),
    (
        "009_claims_v1",
        """
        -- P2 Claim vertical slice: first-class Claim rows (not Memory projections)
        CREATE TABLE IF NOT EXISTS claims (
          id TEXT PRIMARY KEY,
          bank_id TEXT NOT NULL DEFAULT 'personal',
          ast_version TEXT NOT NULL,
          ast_json TEXT NOT NULL,
          surface TEXT NOT NULL DEFAULT '',
          normalized_hash TEXT NOT NULL,
          lifecycle TEXT NOT NULL DEFAULT 'proposed',
          verification_status TEXT NOT NULL DEFAULT 'unverified',
          scope_json TEXT NOT NULL DEFAULT '{}',
          source_memory_id TEXT,
          redirects_to TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_claims_bank_life
          ON claims(bank_id, lifecycle, updated_at);
        CREATE INDEX IF NOT EXISTS idx_claims_hash
          ON claims(normalized_hash);
        CREATE INDEX IF NOT EXISTS idx_claims_source_mem
          ON claims(source_memory_id);
        CREATE INDEX IF NOT EXISTS idx_claims_redirect
          ON claims(redirects_to);

        CREATE TABLE IF NOT EXISTS claim_evidence (
          id TEXT PRIMARY KEY,
          claim_id TEXT NOT NULL,
          evidence_ref TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'support',
          independent INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim
          ON claim_evidence(claim_id, role);

        CREATE TABLE IF NOT EXISTS claim_lifecycle_events (
          id TEXT PRIMARY KEY,
          claim_id TEXT NOT NULL,
          from_status TEXT NOT NULL,
          to_status TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '',
          actor TEXT NOT NULL DEFAULT '',
          policy_version TEXT NOT NULL DEFAULT 'claim.lifecycle.v1',
          evidence_refs_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_claim_life_events
          ON claim_lifecycle_events(claim_id, created_at);

        CREATE TABLE IF NOT EXISTS verification_records (
          id TEXT PRIMARY KEY,
          claim_id TEXT NOT NULL,
          status TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL DEFAULT '[]',
          actor TEXT NOT NULL DEFAULT '',
          verified_at TEXT,
          expired_at TEXT,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_verification_claim
          ON verification_records(claim_id, created_at);
        """,
    ),
    (
        "010_evidence_objects_v1",
        """
        -- P3: first-class Evidence objects (memory_evidence remains legacy bridge)
        CREATE TABLE IF NOT EXISTS evidences (
          id TEXT PRIMARY KEY,
          bank_id TEXT NOT NULL DEFAULT 'personal',
          source_type TEXT NOT NULL,
          source_ref TEXT NOT NULL DEFAULT '',
          fingerprint TEXT NOT NULL,
          provenance_status TEXT NOT NULL DEFAULT 'unknown',
          independence_key TEXT NOT NULL DEFAULT '',
          root_source_id TEXT NOT NULL DEFAULT '',
          producer TEXT NOT NULL DEFAULT '',
          role_hint TEXT NOT NULL DEFAULT 'support',
          validation_state TEXT NOT NULL DEFAULT 'unverified',
          security_json TEXT NOT NULL DEFAULT '{}',
          chain_json TEXT NOT NULL DEFAULT '[]',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          legacy_memory_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_evidences_fingerprint
          ON evidences(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_evidences_source
          ON evidences(source_type, source_ref);
        CREATE INDEX IF NOT EXISTS idx_evidences_indep
          ON evidences(independence_key);
        CREATE INDEX IF NOT EXISTS idx_evidences_bank
          ON evidences(bank_id, created_at);
        """,
    ),
    (
        "011_tms_v1",
        """
        -- P4 Truth Maintenance: justifications + belief snapshots
        CREATE TABLE IF NOT EXISTS tms_justifications (
          id TEXT PRIMARY KEY,
          conclusion_id TEXT NOT NULL,
          premises_json TEXT NOT NULL DEFAULT '[]',
          kind TEXT NOT NULL DEFAULT 'support',
          valid INTEGER NOT NULL DEFAULT 1,
          reason TEXT NOT NULL DEFAULT '',
          actor TEXT NOT NULL DEFAULT '',
          policy_version TEXT NOT NULL DEFAULT 'tms.v1',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tms_just_conclusion
          ON tms_justifications(conclusion_id, valid);
        CREATE TABLE IF NOT EXISTS tms_beliefs (
          node_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          reason_codes_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        "012_strategy_v1",
        """
        -- P5 Experience / Strategy / StrategyLog
        CREATE TABLE IF NOT EXISTS experiences (
          id TEXT PRIMARY KEY,
          bank_id TEXT NOT NULL DEFAULT 'personal',
          context_json TEXT NOT NULL DEFAULT '{}',
          action_json TEXT NOT NULL DEFAULT '{}',
          outcome_json TEXT NOT NULL DEFAULT '{}',
          memory_ids_json TEXT NOT NULL DEFAULT '[]',
          claim_ids_json TEXT NOT NULL DEFAULT '[]',
          reward REAL,
          source TEXT NOT NULL DEFAULT 'manual',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_experiences_bank
          ON experiences(bank_id, created_at);

        CREATE TABLE IF NOT EXISTS strategies (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          scope_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'proposed',
          propensity_json TEXT NOT NULL DEFAULT '{}',
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_strategies_status
          ON strategies(status, updated_at);

        CREATE TABLE IF NOT EXISTS strategy_logs (
          id TEXT PRIMARY KEY,
          strategy_id TEXT NOT NULL,
          experience_id TEXT,
          decision TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL DEFAULT '',
          propensity REAL,
          overlap REAL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_logs_sid
          ON strategy_logs(strategy_id, created_at);
        """,
    ),
    (
        "013_ab_v1",
        """
        -- P5 online A/B metrics (exposures/outcomes only; no auto-deploy)
        CREATE TABLE IF NOT EXISTS ab_exposures (
          id TEXT PRIMARY KEY,
          experiment_id TEXT NOT NULL,
          arm TEXT NOT NULL,
          subject_key TEXT NOT NULL DEFAULT '',
          context_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ab_exposures_exp
          ON ab_exposures(experiment_id, created_at);

        CREATE TABLE IF NOT EXISTS ab_outcomes (
          id TEXT PRIMARY KEY,
          exposure_id TEXT NOT NULL,
          experiment_id TEXT NOT NULL,
          reward REAL,
          label TEXT NOT NULL DEFAULT '',
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ab_outcomes_exp
          ON ab_outcomes(experiment_id, created_at);
        """,
    ),
    (
        "014_knowledge_v1",
        """
        -- Post-P5 Knowledge (Claim/Schema/Strategy downstream)
        CREATE TABLE IF NOT EXISTS knowledge_nodes (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'proposed',
          claim_ids_json TEXT NOT NULL DEFAULT '[]',
          schema_id TEXT,
          strategy_id TEXT,
          payload_json TEXT NOT NULL DEFAULT '{}',
          scope_json TEXT NOT NULL DEFAULT '{}',
          security_json TEXT NOT NULL DEFAULT '{}',
          validation_json TEXT NOT NULL DEFAULT '{}',
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_kind
          ON knowledge_nodes(kind, status, updated_at);

        CREATE TABLE IF NOT EXISTS value_soft_prefs (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL DEFAULT 'null',
          updated_at TEXT NOT NULL,
          actor TEXT NOT NULL DEFAULT ''
        );
        """,
    ),
    (
        "015_fault_v1",
        """
        -- Task-book P7 Fault Localization & Repair Validation
        CREATE TABLE IF NOT EXISTS fault_cases (
          id TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'open',
          invariant_code TEXT NOT NULL,
          subject_refs_json TEXT NOT NULL DEFAULT '[]',
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fault_cases_status
          ON fault_cases(status, updated_at);

        CREATE TABLE IF NOT EXISTS fault_stages (
          id TEXT PRIMARY KEY,
          fault_id TEXT NOT NULL,
          stage TEXT NOT NULL,
          ok INTEGER NOT NULL DEFAULT 1,
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fault_stages_fault
          ON fault_stages(fault_id, created_at);
        """,
    ),
    (
        "016_judgment_temporal_v1",
        """
        -- Judgment supersession / retraction / expiry temporal traceability
        ALTER TABLE claims ADD COLUMN validity_from TEXT;
        ALTER TABLE claims ADD COLUMN validity_to TEXT;
        ALTER TABLE claims ADD COLUMN superseded_at TEXT;
        ALTER TABLE claims ADD COLUMN retracted_at TEXT;
        ALTER TABLE claims ADD COLUMN expired_at TEXT;
        ALTER TABLE claims ADD COLUMN last_confirmed_at TEXT;
        ALTER TABLE claims ADD COLUMN superseded_by TEXT;

        ALTER TABLE claim_lifecycle_events ADD COLUMN trace_id TEXT;
        ALTER TABLE claim_lifecycle_events ADD COLUMN decision_id TEXT;

        CREATE TABLE IF NOT EXISTS claim_decision_records (
          id TEXT PRIMARY KEY,
          claim_id TEXT NOT NULL,
          operation TEXT NOT NULL,
          previous_status TEXT NOT NULL,
          next_status TEXT NOT NULL,
          trigger_evidence_json TEXT NOT NULL DEFAULT '[]',
          replacement_claim_id TEXT,
          reason TEXT NOT NULL DEFAULT '',
          decision_at TEXT NOT NULL,
          actor TEXT NOT NULL DEFAULT '',
          confidence REAL,
          trace_id TEXT,
          schema_version TEXT NOT NULL DEFAULT 'claim.decision.v1',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_claim_decisions_claim
          ON claim_decision_records(claim_id, decision_at);
        CREATE INDEX IF NOT EXISTS idx_claim_decisions_trace
          ON claim_decision_records(trace_id);
        """,
    ),
    (
        "016_audit_hardening",
        """
        CREATE TABLE IF NOT EXISTS judgment_traces (
          id TEXT PRIMARY KEY,
          judgment_type TEXT NOT NULL,
          governance_critical INTEGER NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX idx_judgment_traces_type ON judgment_traces(judgment_type, created_at);
        ALTER TABLE claims ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE claims ADD COLUMN normalization_version TEXT NOT NULL DEFAULT 'claim.normalize.v1';
        CREATE TABLE IF NOT EXISTS schema_proposal_commits (
          plan_id TEXT PRIMARY KEY,
          result_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        """,
    ),
]


MIGRATIONS.append(("017_theory_loops", """
ALTER TABLE claims ADD COLUMN parser_version TEXT NOT NULL DEFAULT 'claim.parser.legacy';
ALTER TABLE memory_feedback ADD COLUMN feedback_kind TEXT NOT NULL DEFAULT 'unclassified';
CREATE TABLE cognitive_task_runs (
  id TEXT PRIMARY KEY, bank_id TEXT NOT NULL, request_json TEXT NOT NULL,
  result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE cognitive_validation_suites (
  id TEXT PRIMARY KEY, bank_id TEXT NOT NULL, digest TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE cognitive_learning_updates (
  id TEXT PRIMARY KEY, bank_id TEXT NOT NULL, schema_id TEXT NOT NULL,
  suite_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE cognitive_result_cache (
  cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE cognitive_task_feedback (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES cognitive_task_runs(id),
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""))

MIGRATIONS.append(("018_derivation_manifest", """
CREATE TABLE IF NOT EXISTS derivation_manifests (
  derived_id TEXT PRIMARY KEY,
  bank_id TEXT NOT NULL,
  artifact_kind TEXT NOT NULL,
  dependencies_json TEXT NOT NULL,
  processor_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  cursor TEXT,
  freshness TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_derivation_freshness
  ON derivation_manifests(freshness);
CREATE TABLE IF NOT EXISTS source_revision_tombstones (
  bank_id TEXT NOT NULL,
  root_source_id TEXT NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (bank_id, root_source_id, content_hash)
);
"""))

MIGRATIONS.append(("019_session_checkpoint", """
CREATE TABLE IF NOT EXISTS session_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  bank_id TEXT NOT NULL,
  stable_key TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  producer TEXT NOT NULL DEFAULT 'host',
  exposure_count INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_messages_session
  ON session_messages(session_id, bank_id);
CREATE TABLE IF NOT EXISTS session_deliveries (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  bank_id TEXT NOT NULL,
  stable_key TEXT NOT NULL UNIQUE,
  card_id TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_deliveries_session
  ON session_deliveries(session_id, bank_id);
CREATE TABLE IF NOT EXISTS session_checkpoints (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  bank_id TEXT NOT NULL,
  task_id TEXT,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_checkpoints_session
  ON session_checkpoints(session_id, bank_id);
"""))

MIGRATIONS.append(("020_usage_ledger", """
CREATE TABLE IF NOT EXISTS usage_ledger_entries (
  id TEXT PRIMARY KEY,
  correlation_id TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  provider TEXT NOT NULL,
  meter_scope TEXT NOT NULL,
  stable_key TEXT NOT NULL UNIQUE,
  phase TEXT NOT NULL,
  measurement TEXT NOT NULL,
  amount REAL,
  currency TEXT NOT NULL DEFAULT 'USD',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_corr
  ON usage_ledger_entries(correlation_id, attempt);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_scope
  ON usage_ledger_entries(meter_scope, phase);
"""))

MIGRATIONS.append(("021_consumer_watermarks_fencing", """
CREATE TABLE IF NOT EXISTS consumer_watermarks (
  consumer TEXT NOT NULL,
  stream TEXT NOT NULL DEFAULT 'default',
  position INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (consumer, stream)
);
CREATE TABLE IF NOT EXISTS consumer_event_dedupe (
  consumer TEXT NOT NULL,
  event_id TEXT NOT NULL,
  effect_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (consumer, event_id)
);
CREATE TABLE IF NOT EXISTS consumer_effects (
  id TEXT PRIMARY KEY,
  consumer TEXT NOT NULL,
  event_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consumer_effects_c
  ON consumer_effects(consumer, created_at);
CREATE TABLE IF NOT EXISTS worker_leases (
  resource TEXT PRIMARY KEY,
  worker_id TEXT NOT NULL,
  fencing_token TEXT NOT NULL,
  generation INTEGER NOT NULL,
  status TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_artifacts (
  id TEXT PRIMARY KEY,
  resource TEXT NOT NULL,
  fencing_token TEXT NOT NULL,
  generation INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""))

MIGRATIONS.append(("022_execution_receipts", """
CREATE TABLE IF NOT EXISTS execution_actions (
  proposal_id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  bank_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  expected_state_version INTEGER NOT NULL,
  state_version INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  phase TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  receipt_json TEXT NOT NULL DEFAULT '{}',
  outcome_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_actions_corr
  ON execution_actions(correlation_id);
CREATE INDEX IF NOT EXISTS idx_execution_actions_phase
  ON execution_actions(phase);
"""))

MIGRATIONS.append(("023_strategy_learning_candidates", """
CREATE TABLE IF NOT EXISTS strategy_learning_candidates (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL,
  actor TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_learning_status
  ON strategy_learning_candidates(strategy_id, status);
"""))

MIGRATIONS.append(("024_ingestion_governance_v1", """
CREATE TABLE IF NOT EXISTS ingestion_profiles (
  profile_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 1,
  name TEXT NOT NULL,
  mode TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_rules (
  rule_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_decisions (
  decision_id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL,
  producer TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  profile_version INTEGER NOT NULL,
  action TEXT NOT NULL,
  content_type TEXT NOT NULL,
  threat_level TEXT NOT NULL,
  threat_codes_json TEXT NOT NULL DEFAULT '[]',
  matched_rules_json TEXT NOT NULL DEFAULT '[]',
  security_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '',
  review_required INTEGER NOT NULL DEFAULT 0,
  observation_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestion_decisions_created ON ingestion_decisions(created_at);
CREATE TABLE IF NOT EXISTS ingestion_quarantine (
  quarantine_id TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_by TEXT,
  review_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  reviewed_at TEXT
);
"""))

MIGRATIONS.append(("026_conflict_governance", """
CREATE TABLE IF NOT EXISTS conflict_cases (
  id TEXT PRIMARY KEY,
  relation_id INTEGER NOT NULL,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'semantic',
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  snapshot_json TEXT NOT NULL DEFAULT '{}',
  last_resolution_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conflict_cases_status ON conflict_cases(status);
CREATE TABLE IF NOT EXISTS conflict_events (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  scheme TEXT NOT NULL DEFAULT '',
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  tms_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conflict_events_case ON conflict_events(case_id, created_at);
"""))

MIGRATIONS.append(("025_ingestion_profile_versions", """
CREATE TABLE IF NOT EXISTS ingestion_profile_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  changed_by TEXT NOT NULL,
  change_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(profile_id, version)
);
"""))

MIGRATIONS.append(("027_metacognition_v1", """
CREATE TABLE IF NOT EXISTS meta_experiences (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL DEFAULT '',
  request_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  principal_id TEXT,
  bank_id TEXT NOT NULL DEFAULT 'personal',
  scope TEXT,
  memory_snapshot_id TEXT,
  strategy_snapshot_id TEXT,
  self_model_snapshot_id TEXT,
  selected_strategy_ids_json TEXT NOT NULL DEFAULT '[]',
  context_refs_json TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  tool_calls_json TEXT NOT NULL DEFAULT '[]',
  actions_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT NOT NULL DEFAULT '{}',
  outcome_status TEXT NOT NULL DEFAULT 'unknown',
  user_feedback_refs_json TEXT NOT NULL DEFAULT '[]',
  latency_ms REAL,
  token_cost REAL,
  resource_cost REAL,
  uncertainty_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_traces (
  id TEXT PRIMARY KEY,
  experience_id TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_outcomes (
  id TEXT PRIMARY KEY,
  experience_id TEXT NOT NULL,
  body_json TEXT NOT NULL,
  user_feedback_state TEXT NOT NULL DEFAULT 'unknown',
  outcome_status TEXT NOT NULL DEFAULT 'unknown',
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_rubrics (
  id TEXT PRIMARY KEY,
  rubric_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  body_json TEXT NOT NULL,
  created_by TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  audit_ref TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(rubric_id, version)
);
CREATE TABLE IF NOT EXISTS meta_evaluations (
  id TEXT PRIMARY KEY,
  experience_id TEXT NOT NULL,
  evaluator_id TEXT,
  evaluator_kind TEXT,
  rubric_id TEXT,
  rubric_version INTEGER,
  body_json TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT 'inconclusive',
  user_feedback_state TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_hypotheses (
  id TEXT PRIMARY KEY,
  statement TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Candidate',
  body_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_hypothesis_relations (
  id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_strategy_candidates (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Candidate',
  publication_state TEXT NOT NULL DEFAULT 'draft',
  body_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_change_plans (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  body_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_experiments (
  id TEXT PRIMARY KEY,
  strategy_candidate_id TEXT,
  baseline_strategy_id TEXT,
  bank_id TEXT NOT NULL DEFAULT 'personal',
  status TEXT NOT NULL DEFAULT 'proposed',
  body_json TEXT NOT NULL,
  created_by TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_self_models (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'Candidate',
  publication_state TEXT NOT NULL DEFAULT 'draft',
  body_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_snapshots (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  bank_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  manifest_hash TEXT NOT NULL,
  publication_state TEXT NOT NULL DEFAULT 'draft',
  body_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_snapshot_heads (
  bank_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (bank_id, kind)
);
CREATE TABLE IF NOT EXISTS meta_snapshot_audit (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_external_bundles (
  id TEXT PRIMARY KEY,
  body_json TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_revocations (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  revoked_by TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta_audit (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  object_id TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meta_exp_bank ON meta_experiences(bank_id, created_at);
CREATE INDEX IF NOT EXISTS idx_meta_eval_exp ON meta_evaluations(experience_id, created_at);
"""))

MIGRATIONS.append(("029_scenario_task_context_v1", """
CREATE TABLE IF NOT EXISTS scenarios (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  version INTEGER NOT NULL DEFAULT 1,
  owner TEXT NOT NULL DEFAULT '',
  policy_json TEXT NOT NULL DEFAULT '{}',
  audit_ref TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_memberships (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_kind TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'candidate',
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(scenario_id, object_id)
);
CREATE INDEX IF NOT EXISTS idx_scn_mem_scn ON scenario_memberships(scenario_id, status);
CREATE TABLE IF NOT EXISTS scenario_relations (
  id TEXT PRIMARY KEY,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_candidate_rejects (
  fingerprint TEXT PRIMARY KEY,
  scenario_id TEXT,
  reason TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_contexts (
  id TEXT PRIMARY KEY,
  principal TEXT NOT NULL DEFAULT '',
  scenario_id TEXT,
  project_id TEXT,
  role_id TEXT,
  allowed_banks_json TEXT NOT NULL DEFAULT '[]',
  allowed_scopes_json TEXT NOT NULL DEFAULT '[]',
  memory_allowlist_json TEXT NOT NULL DEFAULT '[]',
  memory_denylist_json TEXT NOT NULL DEFAULT '[]',
  sharing_policy TEXT NOT NULL DEFAULT 'isolated',
  authorization_level TEXT NOT NULL DEFAULT 'minimal',
  context_budget INTEGER NOT NULL DEFAULT 32,
  expires_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_ctx_principal ON task_contexts(principal, created_at);
"""))

MIGRATIONS.append(("028_relation_governance", """
ALTER TABLE memory_relations ADD COLUMN status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE memory_relations ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memory_relations ADD COLUMN audit_ref TEXT;
ALTER TABLE memory_relations ADD COLUMN scope TEXT;
ALTER TABLE memory_relations ADD COLUMN deleted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_memory_relations_status ON memory_relations(status, deleted_at);
UPDATE memory_relations
   SET status = COALESCE(json_extract(metadata_json, '$.status'), status),
       version = CASE
         WHEN typeof(json_extract(metadata_json, '$.version')) IN ('integer','real')
           THEN CAST(json_extract(metadata_json, '$.version') AS INTEGER)
         ELSE version
       END,
       audit_ref = json_extract(metadata_json, '$.audit_ref'),
       scope = json_extract(metadata_json, '$.scope')
 WHERE metadata_json IS NOT NULL AND metadata_json != '{}';
"""))

MIGRATIONS.append(("030_media_assets_v1", """
CREATE TABLE IF NOT EXISTS media_assets (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL DEFAULT '',
  bank_id TEXT NOT NULL DEFAULT 'unknown',
  scope TEXT,
  lifecycle TEXT NOT NULL DEFAULT 'active',
  uri TEXT NOT NULL DEFAULT '',
  sensitivity TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL,
  archived_at TEXT,
  deleted_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_media_hash ON media_assets(content_hash, bank_id);
CREATE TABLE IF NOT EXISTS parsed_artifacts (
  id TEXT PRIMARY KEY,
  media_asset_id TEXT NOT NULL,
  parser TEXT NOT NULL,
  parser_version TEXT NOT NULL DEFAULT '',
  params_json TEXT NOT NULL DEFAULT '{}',
  quality TEXT,
  uncertainty TEXT,
  created_at TEXT NOT NULL,
  lineage_parent_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE INDEX IF NOT EXISTS idx_parsed_asset ON parsed_artifacts(media_asset_id, created_at);
CREATE TABLE IF NOT EXISTS source_locators (
  id TEXT PRIMARY KEY,
  media_asset_id TEXT NOT NULL,
  parsed_artifact_id TEXT,
  kind TEXT NOT NULL DEFAULT 'unknown',
  locator_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_locator_asset ON source_locators(media_asset_id);
"""))

MIGRATIONS.append(("031_goal_attention_events_v1", """
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'user',
  owner TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  version INTEGER NOT NULL DEFAULT 1,
  parent_id TEXT,
  bank_id TEXT,
  scenario_id TEXT,
  authorization_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_plans (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  depends_on_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_actions (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  plan_id TEXT,
  kind TEXT NOT NULL DEFAULT 'note',
  authorization TEXT NOT NULL DEFAULT '',
  receipt_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS env_events (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'discovered',
  response_level TEXT NOT NULL DEFAULT 'E0',
  status TEXT NOT NULL DEFAULT 'open',
  payload_json TEXT NOT NULL DEFAULT '{}',
  impact_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(fingerprint)
);
CREATE TABLE IF NOT EXISTS attention_items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  deadline TEXT,
  resource_budget INTEGER NOT NULL DEFAULT 0,
  aging INTEGER NOT NULL DEFAULT 0,
  checkpoint TEXT,
  disposition TEXT NOT NULL DEFAULT 'Deferred',
  status TEXT NOT NULL DEFAULT 'open',
  goal_id TEXT,
  event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attn_status ON attention_items(status, priority, created_at);
"""))

MIGRATIONS.append(("032_evolution_governance_v1", """
CREATE TABLE IF NOT EXISTS repair_proposals (
  id TEXT PRIMARY KEY,
  fault_id TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  impact_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL DEFAULT 'high',
  change_set_json TEXT NOT NULL DEFAULT '[]',
  tests_json TEXT NOT NULL DEFAULT '[]',
  rollback_json TEXT NOT NULL DEFAULT '{}',
  sandbox_json TEXT NOT NULL DEFAULT '{}',
  expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tech_debt (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  area TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL DEFAULT 'open',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evolution_experiments (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT '1',
  control_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  bank_id TEXT NOT NULL DEFAULT 'sandbox',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capability_gates (
  id TEXT PRIMARY KEY,
  frozen INTEGER NOT NULL DEFAULT 0,
  autonomy_level TEXT NOT NULL DEFAULT 'none',
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO capability_gates(id, frozen, autonomy_level, note, updated_at) VALUES
 ('user_final_authority', 1, 'none', 'user retains final decision', '1970-01-01T00:00:00Z'),
 ('identity_auth_base', 1, 'none', 'identity and authorization base', '1970-01-01T00:00:00Z'),
 ('approval_audit', 1, 'none', 'approval and audit are mandatory', '1970-01-01T00:00:00Z'),
 ('production_data_protection', 1, 'none', 'production data protection', '1970-01-01T00:00:00Z'),
 ('bank_scenario_isolation', 1, 'none', 'cross bank/scenario isolation', '1970-01-01T00:00:00Z'),
 ('evolution_safety_gate', 1, 'none', 'self-evolution safety gate', '1970-01-01T00:00:00Z');
"""))

ROLLBACK_SQL = {
    "027_metacognition_v1": """
DROP TABLE IF EXISTS meta_audit;
DROP TABLE IF EXISTS meta_revocations;
DROP TABLE IF EXISTS meta_external_bundles;
DROP TABLE IF EXISTS meta_snapshot_audit;
DROP TABLE IF EXISTS meta_snapshot_heads;
DROP TABLE IF EXISTS meta_snapshots;
DROP TABLE IF EXISTS meta_self_models;
DROP TABLE IF EXISTS meta_experiments;
DROP TABLE IF EXISTS meta_change_plans;
DROP TABLE IF EXISTS meta_strategy_candidates;
DROP TABLE IF EXISTS meta_hypothesis_relations;
DROP TABLE IF EXISTS meta_hypotheses;
DROP TABLE IF EXISTS meta_evaluations;
DROP TABLE IF EXISTS meta_rubrics;
DROP TABLE IF EXISTS meta_outcomes;
DROP TABLE IF EXISTS meta_traces;
DROP TABLE IF EXISTS meta_experiences;
DELETE FROM schema_migrations WHERE id='027_metacognition_v1';
""",
    "029_scenario_task_context_v1": """
DROP TABLE IF EXISTS task_contexts;
DROP TABLE IF EXISTS scenario_candidate_rejects;
DROP TABLE IF EXISTS scenario_relations;
DROP TABLE IF EXISTS scenario_memberships;
DROP TABLE IF EXISTS scenarios;
DELETE FROM schema_migrations WHERE id='029_scenario_task_context_v1';
""",
    "028_relation_governance": """
-- columns cannot be dropped portably; mark migration unapplied only after backup restore
DELETE FROM schema_migrations WHERE id='028_relation_governance';
""",
    "030_media_assets_v1": """
DROP TABLE IF EXISTS source_locators;
DROP TABLE IF EXISTS parsed_artifacts;
DROP TABLE IF EXISTS media_assets;
DELETE FROM schema_migrations WHERE id='030_media_assets_v1';
""",
    "031_goal_attention_events_v1": """
DROP TABLE IF EXISTS attention_items;
DROP TABLE IF EXISTS env_events;
DROP TABLE IF EXISTS goal_actions;
DROP TABLE IF EXISTS goal_plans;
DROP TABLE IF EXISTS goals;
DELETE FROM schema_migrations WHERE id='031_goal_attention_events_v1';
""",
    "032_evolution_governance_v1": """
DROP TABLE IF EXISTS capability_gates;
DROP TABLE IF EXISTS evolution_experiments;
DROP TABLE IF EXISTS tech_debt;
DROP TABLE IF EXISTS repair_proposals;
DELETE FROM schema_migrations WHERE id='032_evolution_governance_v1';
""",
}



def apply_migrations(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row[0]
        for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }
    done: list[str] = []
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for mid, sql in MIGRATIONS:
        if mid in applied:
            continue
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n" + sql
                + "\nINSERT OR IGNORE INTO schema_migrations(id, applied_at) VALUES ('"
                + mid.replace("'", "''") + "','" + now + "');\nCOMMIT;"
            )
        except Exception:
            conn.rollback()
            raise
        done.append(mid)
    conn.commit()
    return done
