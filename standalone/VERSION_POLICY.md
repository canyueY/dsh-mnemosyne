# 版本冻结声明（Version Policy）

**当前版本：`2.0.0`（Mnemosyne 2.0），已冻结。**

未经用户**明确声明**，不得改动任何版本信息，包括但不限于：

- `package.json` / `pyproject.toml` 的 `version`
- Git tag、GitHub Release 的 tag / 名称
- 发布资产文件名（如 `dsh-mnemosyne-standalone-2.0.0.tgz`）
- README、CHANGELOG、RELEASE_NOTES、BENCHMARK、发布说明中的版本号
- 运行时 `/health` 中的 `version` / `mnemosyne_version`
- 任何“最新版本”“全新版本”等版本性措辞

历史 release（如 `v2.0.0`、`v3.0.0`）只作为历史保留，不修改、不删除。

## 变更流程

只有用户明确说出要改到哪个版本、改哪些位置时，才允许变更；变更必须同时更新本文件并记录日期与授权来源。否则一律保持 `2.0.0`。
