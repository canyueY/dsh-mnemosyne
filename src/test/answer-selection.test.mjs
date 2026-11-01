/**
 * E05：宿主答案选择工具与故障信封。
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MemoryClient,
  normalizeSelectionDelivery,
  unavailableSelection,
} from '../memory-client.js';
import { selectAnswerToolSpec, SEARCH_TOOL, SELECT_ANSWER_TOOL } from '../tool-spec.js';

test('selectAnswer tool spec is 0.2.1 schema-safe and distinct from search', () => {
  const spec = selectAnswerToolSpec();
  assert.equal(spec.name, SELECT_ANSWER_TOOL);
  assert.notEqual(spec.name, SEARCH_TOOL);
  assert.equal(spec.output.schema.type, 'object');
  assert.equal(spec.output.schema.additionalProperties, true);
  assert.match(spec.description, /选择|select|Top1/i);
});

test('normalizeSelectionDelivery labels partial/unknown boundaries', () => {
  const partial = normalizeSelectionDelivery({
    status: 'partial',
    decision_id: 'ans_1',
    selected: [{ ref: 'c1' }],
    reason_codes: ['budget_exhausted'],
    checks_complete: false,
  });
  assert.equal(partial.availability, 'available');
  assert.equal(partial.status, 'partial');
  assert.equal(partial.boundary.not_top1_fact, true);
  assert.equal(partial.wrote_verified, false);

  const unknown = normalizeSelectionDelivery({ status: 'unknown', reason_codes: ['source_missing'] });
  assert.equal(unknown.boundary.not_top1_fact, true);
});

test('unavailableSelection does not invent Top1 fact', () => {
  const err = new Error('HTTP 503');
  err.status = 503;
  const out = unavailableSelection(err);
  assert.equal(out.ok, false);
  assert.equal(out.availability, 'unavailable');
  assert.equal(out.status, 'unavailable');
  assert.deepEqual(out.selected, []);
  assert.equal(out.boundary.not_evidence_absence, true);
  assert.equal(out.boundary.not_top1_fact, true);
  assert.ok(out.reason_codes.includes('node_unavailable'));
});

test('MemoryClient.selectAnswer maps transport failure to unavailable', async () => {
  const client = new MemoryClient({ baseUrl: 'http://127.0.0.1:9', token: 't', timeoutMs: 50 });
  const out = await client.selectAnswer({ query: 'x', target: { subject_id: 'A', predicate: '喜欢' } });
  assert.equal(out.availability, 'unavailable');
  assert.equal(out.status, 'unavailable');
  assert.equal(out.selected.length, 0);
  assert.ok(out.boundary.not_top1_fact);
});
