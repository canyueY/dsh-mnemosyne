# Mnemosyne 2.0 冷启动故障：用户恢复说明

适用：安装 `dsh-mnemosyne` 2.0.0 后，DSH `0.2.1-rc.5` 重启崩溃，或设置/欢迎页报
`'toJSON' in null` / `Cannot use 'in' operator`。

---

## 1. 先备份

关闭 DSH 后，复制整个 profile 和 Memory Bank：

```powershell
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"
Copy-Item $profile "$profile.backup-20261104" -Recurse
Copy-Item "$env:LOCALAPPDATA\Mnemosyne DSH Memory Bank" "$env:LOCALAPPDATA\Mnemosyne DSH Memory Bank.backup-20261104" -Recurse
```

不要把 `memory.db` 换成新文件；备份只是保险。

## 2. 如果现在 DSH 打不开

先临时停用故障插件（不要删 Bank）：

```powershell
dsh plugin --profile desktop remove dsh-mnemosyne
```

或在 DSH profile 的 `package.json` 的 `dsh.profile.bundles` 中临时移除
`dsh-mnemosyne`，然后重启 DSH。Memory Bank、会话和配置都保留在磁盘上。

## 3. 安装修复包

使用修复后的 `dsh-mnemosyne-2.0.0.tgz`：

```powershell
dsh plugin --profile desktop add "file:<绝对路径>\dsh-mnemosyne-2.0.0.tgz"
```

重启 DSH。首次启动后确认没有 `'toJSON' in null`。

## 4. 把原配置写回插件行

修复版不再提供自动设置表单，配置写在 profile 的 `cordis.patch.yml`（或插件自带
`cordis.patch.yml` 的插件行）里：

```yaml
- id: dsh-mnemosyne
  config:
    auto_start: true
    auto_capture: true
    # data_dir: D:/你的/Mnemosyne DSH Memory Bank
    # service_root: C:/.../dsh-mnemosyne/service
    # python: C:/.../venv/Scripts/python.exe
    # base_url: http://127.0.0.1:端口
    # token: 你的 token
```

注意：

- 原来 `data_dir` 有值的，**必须原样保留**，不要留空；留空会使用默认 Bank。
- 远程模式 `auto_start: false` 时，必须同时给 `base_url` 和 `token`。
- 不要把 token 贴到日志、截图或 issue。
- 旧 `settings.yaml` 中的非默认值会被兼容读取；但建议按上面显式写一次，后续最稳。

## 5. 检查

1. 重启 DSH 两次，确认都能启动；
2. 打开设置页/欢迎页，不再报错；
3. 让 Agent 调一次 `memory_health`，确认 `memories` / `observations` 与原来一致；
4. 检查 `data_dir` 仍指向原 Bank，没有出现新的空库。

如果仍有问题，保留 DSH 启动日志和 `memory_health` 返回（先脱敏 token），不要删除
Bank，直接反馈。
