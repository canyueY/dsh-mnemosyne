/**
 * DSH settings Config schema for namespace `dsh-mnemosyne`.
 * Derived by @deepseek-ai/dsh-settings from entry id + Config (no ctx.settings.register).
 */
export const SETTINGS_NAMESPACE = 'dsh-mnemosyne';
export const LEGACY_NAMESPACE = 'dsh-cognitive-memory';

export const CONFIG_DEFAULTS = Object.freeze({
  auto_start: true,
  service_root: '',
  data_dir: '',
  python: '',
  base_url: '',
  token: '',
  auto_capture: true,
});

/** Field metadata for describe() when schemastery is unavailable. */
export const CONFIG_FIELD_SCHEMA = Object.freeze({
  auto_start: { type: 'boolean', default: true, secret: false },
  service_root: { type: 'string', default: '', secret: false },
  data_dir: { type: 'string', default: '', secret: false },
  python: { type: 'string', default: '', secret: false },
  base_url: { type: 'string', default: '', secret: false },
  token: { type: 'string', default: '', secret: true, role: 'secret' },
  auto_capture: { type: 'boolean', default: true, secret: false },
});

/**
 * Build Cordis Config via schemastery when the package is resolvable.
 * @param {any} Schema schemastery default export
 */
export function buildConfig(Schema) {
  return Schema.object({
    auto_start: Schema.boolean().default(true).volatile(),
    service_root: Schema.string().default('').volatile(),
    data_dir: Schema.string().default('').volatile(),
    python: Schema.string().default('').volatile(),
    base_url: Schema.string().default('').volatile(),
    token: Schema.string().role('secret').default('').volatile(),
    auto_capture: Schema.boolean().default(true).volatile(),
  });
}

/**
 * Load a schemastery schema for diagnostics.
 * DSH 0.2.1-rc.5 only accepts a Config export that implements
 * `~standard.validate` and `toJSON`. `null` passes activation
 * (`if (!runtime.Config)`) and then crashes settings.describe,
 * because that path evaluates `'toJSON' in schema` after rejecting
 * only `undefined`. This entry therefore does not export Config.
 * A failed load throws; it must not be published as `null`.
 */
export async function resolveConfigExport() {
  let mod;
  try {
    mod = await import('@deepseek-ai/schemastery');
  } catch (error) {
    const err = new Error('schemastery is not resolvable; refusing to export Config');
    err.code = 'config_schema_unavailable';
    err.cause = error;
    throw err;
  }
  const Schema = mod.default || mod.Schema || mod;
  const schema = buildConfig(Schema);
  const validate = schema?.['~standard']?.validate;
  if (typeof schema?.toJSON !== 'function' || typeof validate !== 'function') {
    const err = new Error('schemastery did not produce a host Config schema; refusing to export Config');
    err.code = 'config_schema_invalid';
    throw err;
  }
  return schema;
}
