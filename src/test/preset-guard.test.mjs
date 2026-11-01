/**
 * Guard unit test: memory tools allowed; local execution and unknown tools
 * fail closed; restrict is attempted but absence is tolerated.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { apply, RESTRICTED_TOOL_NAMES } from '../preset-guard.js';

function fakeTools() {
  const restricted = [];
  let guard = null;
  return {
    restricted,
    tools: {
      restrict(patch) { restricted.push(patch); },
      guard(fn) { guard = fn; },
    },
    check(name) { return guard?.({ name }); },
  };
}

test('DA12: guard allows memory/ask/todo and denies local/unknown tools', () => {
  const f = fakeTools();
  apply(f);
  assert.ok(f.restricted.length > 0);
  assert.ok(RESTRICTED_TOOL_NAMES.includes('read_file'));

  for (const name of ['memory_search', 'memory_observe', 'memory_select_answer', 'ask_user_question', 'todo_write']) {
    assert.equal(f.check(name), undefined, name);
  }
  for (const name of ['bash', 'pwsh', 'read', 'write', 'edit', 'subagent', 'workflow', 'plugin_manager', 'totally_unknown']) {
    assert.equal(typeof f.check(name), 'string', name);
  }
});

test('DA12: restrict failures do not disable the execution guard', () => {
  const calls = [];
  let guard = null;
  apply({
    tools: {
      restrict() { throw new Error('unknown tool in this DSH'); },
      guard(fn) { guard = fn; },
    },
  });
  assert.equal(typeof guard, 'function');
  assert.equal(typeof guard({ name: 'memory_search' }), 'undefined');
  assert.equal(typeof guard({ name: 'bash' }), 'string');
});
