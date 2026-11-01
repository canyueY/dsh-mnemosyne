/**
 * REST client for mnemosyne service (127.0.0.1).
 * SPDX-License-Identifier: Apache-2.0
 */

export class MemoryClient {
  /**
   * @param {{ baseUrl: string, token: string, timeoutMs?: number }} opts
   */
  constructor(opts) {
    this.baseUrl = String(opts.baseUrl || '').replace(/\/$/, '');
    this.token = String(opts.token || '');
    this.timeoutMs = Number(opts.timeoutMs || 15000);
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {unknown} [body]
   * @param {{ signal?: AbortSignal }} [opts]
   */
  async request(method, path, body, opts = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    const outer = opts.signal;
    const onOuterAbort = () => ctrl.abort(outer?.reason);
    if (outer) {
      if (outer.aborted) ctrl.abort(outer.reason);
      else outer.addEventListener('abort', onOuterAbort, { once: true });
    }
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.token}`,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
      if (!res.ok) {
        const err = new Error(data?.message || `HTTP ${res.status}`);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } finally {
      clearTimeout(timer);
      if (outer) outer.removeEventListener('abort', onOuterAbort);
    }
  }

  health(opts) { return this.request('GET', '/api/v1/health', undefined, opts); }
  search(body, opts) { return this.request('POST', '/api/v1/memories/search', body, opts); }
  get(id, includeEvidence = false, opts) {
    const q = includeEvidence ? '?include_evidence=true' : '';
    return this.request('GET', `/api/v1/memories/${encodeURIComponent(id)}${q}`, undefined, opts);
  }
  propose(body, opts) { return this.request('POST', '/api/v1/memories', body, opts); }
  update(id, body, opts) { return this.request('PATCH', `/api/v1/memories/${encodeURIComponent(id)}`, body, opts); }
  forget(id, hard = false, opts) {
    const q = hard ? '?hard=true' : '';
    return this.request('DELETE', `/api/v1/memories/${encodeURIComponent(id)}${q}`, undefined, opts);
  }
  observe(body, opts) { return this.request('POST', '/api/v1/observations', body, opts); }
  feedback(body, opts) { return this.request('POST', '/api/v1/feedback', body, opts); }
  context(body, opts) { return this.request('POST', '/api/v1/contexts/build', body, opts); }
  task(id, opts) { return this.request('GET', `/api/v1/tasks/${encodeURIComponent(id)}`, undefined, opts); }

  /**
   * 显式答案选择。故障时返回 unavailable 信封，不把 Top1 search 伪造成 chosen。
   */
  async selectAnswer(body = {}, opts = {}) {
    try {
      const result = await this.request('POST', '/api/v1/cognition/answers/select', body, opts);
      return normalizeSelectionDelivery(result);
    } catch (err) {
      return unavailableSelection(err);
    }
  }

  /** 会话消息账本：稳定键去重；重放不增加事实支持。 */
  recordSessionMessage(body, opts) {
    return this.request('POST', '/api/v1/cognition/sessions/messages', body, opts);
  }

  /** 交付账本：只证明宿主交付，不证明模型理解。 */
  recordSessionDelivery(body, opts) {
    return this.request('POST', '/api/v1/cognition/sessions/deliveries', body, opts);
  }

  saveSessionCheckpoint(body, opts) {
    return this.request('POST', '/api/v1/cognition/sessions/checkpoints', body, opts);
  }

  getSessionCheckpoint(checkpointId, opts) {
    return this.request(
      'GET',
      `/api/v1/cognition/sessions/checkpoints/${encodeURIComponent(checkpointId)}`,
      undefined,
      opts,
    );
  }

  resumeSessionCheckpoint(body, opts) {
    return this.request('POST', '/api/v1/cognition/sessions/checkpoints/resume', body, opts);
  }

  /** 宿主 usage 回执；缺省时 Node 侧记 unknown，不转零。 */
  recordUsageReceipt(body, opts) {
    return this.request('POST', '/api/v1/cognition/usage/receipts', body, opts);
  }

  usageReport(body, opts) {
    return this.request('POST', '/api/v1/cognition/usage/report', body, opts);
  }
}

export function normalizeSelectionDelivery(result) {
  const status = String(result?.status || 'unknown');
  const delivery = {
    ok: true,
    availability: 'available',
    status,
    decision_id: result?.decision_id || null,
    selected: Array.isArray(result?.selected) ? result.selected : [],
    alternatives: Array.isArray(result?.alternatives) ? result.alternatives : [],
    evidence_refs: Array.isArray(result?.evidence_refs) ? result.evidence_refs : [],
    counterevidence_refs: Array.isArray(result?.counterevidence_refs) ? result.counterevidence_refs : [],
    applicability: result?.applicability || {},
    reason_codes: Array.isArray(result?.reason_codes) ? result.reason_codes : [],
    checks_complete: Boolean(result?.checks_complete),
    wrote_verified: false,
    budget_usage: result?.budget_usage || {},
    trace_id: result?.trace_id || null,
    raw: result,
  };
  if (status === 'partial' || status === 'unknown') {
    delivery.boundary = {
      not_top1_fact: true,
      labeled: true,
      message: status === 'partial'
        ? 'partial selection: candidates labeled, not definitive fact'
        : 'unknown: insufficient evidence or unsupported/budget; do not invent Top1 fact',
    };
  }
  return delivery;
}

export function unavailableSelection(err) {
  const status = err?.status || err?.statusCode || null;
  const message = String(err?.message || err || 'memory node unavailable');
  return {
    ok: false,
    availability: 'unavailable',
    status: 'unavailable',
    selected: [],
    alternatives: [],
    evidence_refs: [],
    reason_codes: ['node_unavailable'],
    wrote_verified: false,
    checks_complete: false,
    error: {
      http_status: status,
      message,
      kind: status === 503 || /timeout|abort|network|ECONN|503/i.test(message)
        ? 'transport_or_service_failure'
        : 'request_failed',
    },
    boundary: {
      not_top1_fact: true,
      not_evidence_absence: true,
      message: 'Node unavailable — do not treat as missing counterevidence or fall back to unlabeled Top1 search hit as fact',
    },
  };
}
