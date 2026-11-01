/** Minimal Mnemosyne Node REST client (Python SDK parity subset). */

export type MnemosyneClientOptions = {
  baseUrl: string;
  token: string;
  agentId?: string;
  agentToken?: string;
  fetchImpl?: typeof fetch;
};

export class MnemosyneClientError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "MnemosyneClientError";
    this.status = status;
    this.body = body;
  }
}

export class MnemosyneClient {
  private baseUrl: string;
  private token: string;
  private agentId?: string;
  private agentToken?: string;
  private fetchImpl: typeof fetch;

  constructor(opts: MnemosyneClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.token = opts.token;
    this.agentId = opts.agentId;
    this.agentToken = opts.agentToken;
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      "Content-Type": "application/json",
      ...extra,
    };
    if (this.agentId && this.agentToken) {
      h["X-Agent-Id"] = this.agentId;
      h["X-Agent-Token"] = this.agentToken;
    }
    return h;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail =
        typeof data === "object" && data && "detail" in data
          ? JSON.stringify((data as { detail: unknown }).detail)
          : res.statusText;
      throw new MnemosyneClientError(detail || res.statusText, res.status, data);
    }
    return data as T;
  }

  health() {
    return this.request<Record<string, unknown>>("GET", "/api/v1/health");
  }

  contract() {
    return this.request<Record<string, unknown>>("GET", "/api/v1/contract");
  }

  node() {
    return this.request<Record<string, unknown>>("GET", "/api/v1/node");
  }

  observe(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/observations", payload);
  }

  propose(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/memories", payload);
  }

  search(query: string, extra: Record<string, unknown> = {}) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/memories/search", {
      query,
      ...extra,
    });
  }

  feedback(memoryId: string, opts: { useful?: boolean; incorrect?: boolean; note?: string; feedbackKind?: string }) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/feedback", {
      memory_id: memoryId,
      useful: opts.useful,
      incorrect: opts.incorrect,
      note: opts.note ?? "",
      producer: "mnemosyne-ts-sdk",
      feedback_kind: opts.feedbackKind ?? "unclassified",
    });
  }

  selectAnswer(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/answers/select", payload);
  }

  recordSessionMessage(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/sessions/messages", payload);
  }

  recordSessionDelivery(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/sessions/deliveries", payload);
  }

  saveSessionCheckpoint(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/sessions/checkpoints", payload);
  }

  getSessionCheckpoint(checkpointId: string) {
    return this.request<Record<string, unknown>>(
      "GET",
      `/api/v1/cognition/sessions/checkpoints/${encodeURIComponent(checkpointId)}`,
    );
  }

  resumeSessionCheckpoint(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/sessions/checkpoints/resume", payload);
  }

  recordUsageReceipt(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/usage/receipts", payload);
  }

  usageReport(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/usage/report", payload);
  }

  planExecution(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/executions/plan", payload);
  }

  executionSave(proposalId: string) {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/cognition/executions/${encodeURIComponent(proposalId)}/save`,
    );
  }

  executionAdopt(proposalId: string) {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/cognition/executions/${encodeURIComponent(proposalId)}/adopt`,
    );
  }

  executionConfirm(proposalId: string) {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/cognition/executions/${encodeURIComponent(proposalId)}/confirm`,
    );
  }

  executionObserve(proposalId: string, payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>(
      "POST",
      `/api/v1/cognition/executions/${encodeURIComponent(proposalId)}/observe`,
      payload,
    );
  }

  runCognitiveTask(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/tasks/run", payload);
  }

  registerValidationSuite(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/validation-suites", payload);
  }

  recordCognitiveResult(runId: string, payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", `/api/v1/cognition/tasks/${encodeURIComponent(runId)}/feedback`, payload);
  }

  proposeLearningUpdate(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/learning-updates", payload);
  }

  evaluateLearningUpdate(updateId: string) {
    return this.request<Record<string, unknown>>("POST", `/api/v1/cognition/learning-updates/${encodeURIComponent(updateId)}/evaluate`);
  }

  applyLearningUpdate(updateId: string, confirm = false) {
    return this.request<Record<string, unknown>>("POST", `/api/v1/cognition/learning-updates/${encodeURIComponent(updateId)}/apply?confirm=${confirm}`);
  }

  rollbackLearningUpdate(updateId: string, confirm = false) {
    return this.request<Record<string, unknown>>("POST", `/api/v1/cognition/learning-updates/${encodeURIComponent(updateId)}/rollback?confirm=${confirm}`);
  }

  recordProfileHypothesis(payload: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", "/api/v1/cognition/profile-hypotheses", payload);
  }
}
