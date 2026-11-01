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

/** Try load real Config; tests may use metadata-only fallback. */
export async function resolveConfigExport() {
  try {
    const mod = await import('@deepseek-ai/schemastery');
    const Schema = mod.default || mod.Schema || mod;
    return buildConfig(Schema);
  } catch {
    return null;
  }
}
