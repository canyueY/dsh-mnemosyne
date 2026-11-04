# DSH upstream issue draft: settings.describe crashes on null plugin Config

> Status: draft, not filed. Host: DeepSeek Harness Desktop 0.2.1-rc.5.

## Title

`settings.describe()` throws `TypeError` when a plugin exports `Config = null`

## Environment

- DSH Desktop `0.2.1-rc.5`
- Node `v24.21.0`
- `@deepseek-ai/dsh-settings` bundled with rc.5

## Minimal reproduction

A Host plugin that exports a null Config:

```js
export const name = 'null-config-plugin'
export const inject = ['tools']
export const Config = null
export function apply() {}
```

Install it, boot any web profile, then call the real settings service:

```js
ctx.settings.describe({ redactSecrets: true })
```

## Actual

```text
TypeError: Cannot use 'in' operator to search for 'toJSON' in null
  at Proxy.schema (.../dsh-settings/lib/index.js:540:40)
  at Proxy.describe (.../dsh-settings/lib/index.js:415:70)
```

`settings.describe()` iterates all entries. `schema(entry)` is:

```js
schema(entry) {
  const schema = entry.fiber?.runtime?.Config;
  return schema !== undefined && "toJSON" in schema ? schema : undefined;
}
```

`null !== undefined` is true, then `'toJSON' in null` throws. The same null passes
`cordis.resolveConfig()` because it checks `if (!runtime.Config)`, so activation
succeeds and the failure appears later on the welcome/settings page.

## Expected

A plugin without a valid Config schema should be skipped by `describe()`; a null
value should not crash the settings service or the welcome page.

## Suggested fix

```js
schema(entry) {
  const schema = entry.fiber?.runtime?.Config;
  return schema != null && typeof schema.toJSON === 'function' ? schema : undefined;
}
```

Consider the same guard in any other path that does `'toJSON' in schema`, and
validate `~standard.validate` before treating the export as a Config.

## Impact

One broken/optional plugin makes settings/describe and the welcome page fail for
the whole profile. A plugin can work around it by not exporting Config, but the
host should isolate invalid plugin metadata.

## Notes

- No token, memory content, or user path is included in this draft.
- A local plugin-side workaround (no Config export + row config) is documented
  separately; this issue is about host robustness.
