# Third-Party Notices

Mnemosyne current code is Apache-2.0. The historical
`legacy/dsh-plugin/` tree remains AGPL-3.0-or-later; see
`legacy/dsh-plugin/LICENSE`.

## Runtime and development dependencies

- FastAPI — MIT
- Uvicorn — BSD-3-Clause
- Pydantic — MIT
- pydantic-settings — MIT
- HTTPX — BSD-3-Clause
- pytest — MIT
- TypeScript — Apache-2.0
- `@deepseek-ai/dsh-tools` — host-provided optional peer; confirm with the
  DeepSeek Harness distribution actually used.

## Provenance note

No Murasame source file was found byte-identical in this repository.
The legacy CLI contains an optional runtime import for old data migration.
This notice is not a legal opinion; confirm provenance before relicensing.
