/**
 * Mnemosyne agent preset guard.
 * Restricts the preset tool surface to memory tools and harmless model-side
 * helpers; unknown tools fail closed.
 *
 * DSH resolves a preset plugin row by its package specifier from the profile
 * directory, so this module is exported as `dsh-mnemosyne/preset-guard`.
 */
import { loadMnemosynePreset } from './agent-preset.js';

export const name = 'mnemosyne-preset-guard';
export const inject = ['tools'];

const preset = loadMnemosynePreset();
const ALLOW = new Set(preset.tools.allow || []);
const DENY_EXACT = new Set(preset.tools.deny_exact || []);
const DENY_PREFIXES = preset.tools.deny_prefixes || [];

export const RESTRICTED_TOOL_NAMES = Object.freeze([...DENY_EXACT]);

function denial(name) {
  if (DENY_EXACT.has(name)) return `tool "${name}" is denied in mnemosyne preset`;
  for (const prefix of DENY_PREFIXES) {
    if (name === prefix || name.startsWith(prefix)) {
      return `tool "${name}" is denied by prefix "${prefix}" in mnemosyne preset`;
    }
  }
  if (ALLOW.has(name)) return null;
  return `tool "${name}" is not in the mnemosyne preset allowlist`;
}

export function apply(ctx) {
  for (const tool of DENY_EXACT) {
    try {
      ctx.tools.restrict({ deny: [tool] });
    } catch {
      // Current DSH may not register every historical tool name; guard still denies.
    }
  }
  ctx.tools.guard((exec) => {
    const toolName = exec?.name ?? exec?.tool;
    if (typeof toolName !== 'string' || !toolName) return 'invalid tool name';
    return denial(toolName) ?? undefined;
  });
}
