/** Tool specs — host schema subset only. */

export const SEARCH_TOOL = 'memory_search';
export const GET_TOOL = 'memory_get';
export const CONTEXT_TOOL = 'memory_context';
export const PROPOSE_TOOL = 'memory_propose';
export const FEEDBACK_TOOL = 'memory_feedback';
export const UPDATE_TOOL = 'memory_update';
export const FORGET_TOOL = 'memory_forget';
export const TASK_TOOL = 'memory_task_status';
export const OBSERVE_TOOL = 'memory_observe';
export const HEALTH_TOOL = 'memory_health';
export const SELECT_ANSWER_TOOL = 'memory_select_answer';

/**
 * DSH 0.2.1 output.schema is enforced JsonSchemaNode.
 * - `type: 'json'` / bare `{}` are rejected by defineTool
 * - open object accepts arbitrary JSON object payloads from the Memory Service
 */
function jsonOut() {
  return {
    schema: { type: 'object', additionalProperties: true },
    render: (_a, v) => [{ type: 'text', text: typeof v === 'string' ? v : JSON.stringify(v, null, 2) }],
  };
}

export function searchToolSpec() {
  return {
    name: SEARCH_TOOL,
    description: '搜索演化记忆（工作/情节/语义/程序性）。替代旧 A-MEM 检索。',
    parameters: {
      query: { type: 'string', required: true, description: '检索关键字' },
      limit: { type: 'integer', description: '条数，默认 10' },
      types: { type: 'json', description: '可选类型数组：working|episodic|semantic|procedural' },
    },
    output: jsonOut(),
  };
}

export function getToolSpec() {
  return {
    name: GET_TOOL,
    description: '获取单条记忆详情与证据链。',
    parameters: {
      id: { type: 'string', required: true },
      include_evidence: { type: 'boolean' },
    },
    output: jsonOut(),
  };
}

export function contextToolSpec() {
  // envelope / has_accurate_tokenizer: E04 完整预算；无精确 tokenizer 时勿宣称 measured
  return {
    name: CONTEXT_TOOL,
    description: '按 Token 预算组装当前对话可用的记忆上下文（快速通道）。完整信封先预留固定开销再裁剪记忆。',
    parameters: {
      query: { type: 'string', required: true },
      token_budget: { type: 'integer' },
      envelope: { type: 'json', description: 'sections: stable_prefix/tools/history/header_tags/output_reserve' },
      has_accurate_tokenizer: { type: 'boolean', description: '仅在确有实测 tokenizer 时为 true' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_a, v) => [{ type: 'text', text: v?.brief || JSON.stringify(v) }],
    },
  };
}

export function proposeToolSpec() {
  return {
    name: PROPOSE_TOOL,
    description: '提交候选记忆（默认 candidate，由慢速通道决定是否晋升）。',
    parameters: {
      content: { type: 'string', required: true },
      type: { type: 'string', description: 'working|episodic|semantic|procedural' },
      source_ids: { type: 'json' },
      status: { type: 'string' },
    },
    output: jsonOut(),
  };
}

export function feedbackToolSpec() {
  return {
    name: FEEDBACK_TOOL,
    description: '反馈记忆是否有用或有误；incorrect 会触发失效审查。',
    parameters: {
      memory_id: { type: 'string', required: true },
      useful: { type: 'boolean' },
      incorrect: { type: 'boolean' },
      note: { type: 'string' },
    },
    output: jsonOut(),
  };
}

export function updateToolSpec() {
  return {
    name: UPDATE_TOOL,
    description: '更新记忆；可传 expected_version 做乐观锁。',
    parameters: {
      id: { type: 'string', required: true },
      content: { type: 'string' },
      status: { type: 'string' },
      expected_version: { type: 'integer' },
    },
    output: jsonOut(),
  };
}

export function forgetToolSpec() {
  return {
    name: FORGET_TOOL,
    description: '使记忆失效（默认软删除）；hard=true 时硬删主表。',
    parameters: {
      id: { type: 'string', required: true },
      hard: { type: 'boolean' },
    },
    output: jsonOut(),
  };
}

export function taskToolSpec() {
  return {
    name: TASK_TOOL,
    description: '查询后台慢速通道任务状态。',
    parameters: { id: { type: 'string', required: true } },
    output: jsonOut(),
  };
}

export function observeToolSpec() {
  return {
    name: OBSERVE_TOOL,
    description: '提交原始观察（对话/视频/交互）；不等于直接写长期记忆。',
    parameters: {
      content: { type: 'string', required: true },
      source_kind: {
        type: 'string',
        description: 'user_statement|dialog|video_observation|agent_inference|system',
      },
      correlation_id: { type: 'string' },
      producer: { type: 'string' },
    },
    output: jsonOut(),
  };
}

export function healthToolSpec() {
  return {
    name: HEALTH_TOOL,
    description: '检查外置演化记忆服务是否健康。',
    parameters: {},
    output: jsonOut(),
  };
}

export function selectAnswerToolSpec() {
  return {
    name: SELECT_ANSWER_TOOL,
    description:
      '显式调用 Node 当前答案选择（非普通 search Top1）。partial/unknown 带边界交付；故障返回 unavailable，不退回无标签旧事实。',
    parameters: {
      query: { type: 'string', description: '自然语言查询（需可被 parser 解析或配合 target）' },
      target: { type: 'json', description: '{subject_id,predicate,negated?}' },
      bank_id: { type: 'string' },
      mode: { type: 'string', description: 'current|history' },
      context: { type: 'json' },
      at: { type: 'string' },
      budget: { type: 'json' },
      include_search_candidates: { type: 'boolean', description: '附带 search 候选供对照，不作事实权威' },
    },
    output: jsonOut(),
  };
}
