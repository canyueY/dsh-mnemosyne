/**
 * DSH 0.2.1 host-compat smoke checks (no dsh-tools install required).
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import * as specs from '../tool-spec.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

test('package.json declares engines.dsh for 0.2.1 admission', () => {
  const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  assert.equal(typeof pkg.engines?.dsh, 'string');
  assert.match(pkg.engines.dsh, /0\.2\.1/);
  assert.equal(pkg.dsh?.manifestVersion, 1);
  assert.match(String(pkg.peerDependencies?.['@deepseek-ai/dsh-tools'] || ''), /0\.2\.1/);
});

test('tool output schemas are 0.2.1 JsonSchemaNode-safe', () => {
  const builders = [
    specs.healthToolSpec,
    specs.searchToolSpec,
    specs.getToolSpec,
    specs.contextToolSpec,
    specs.selectAnswerToolSpec,
    specs.proposeToolSpec,
    specs.feedbackToolSpec,
    specs.updateToolSpec,
    specs.forgetToolSpec,
    specs.taskToolSpec,
    specs.observeToolSpec,
  ];
  const allowed = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null']);
  for (const build of builders) {
    const spec = build();
    const schema = spec.output?.schema;
    assert.ok(schema && typeof schema === 'object', `${spec.name} missing output.schema`);
    assert.notEqual(schema.type, 'json', `${spec.name} must not use type:json in output`);
    assert.ok(schema.type, `${spec.name} output.schema.type required by dsh-tools 0.2.1`);
    assert.ok(allowed.has(schema.type), `${spec.name} has unsupported output type ${schema.type}`);
    if (schema.type === 'object') {
      assert.equal(
        typeof schema.additionalProperties,
        'boolean',
        `${spec.name} object output must set additionalProperties`,
      );
    }
  }
});
