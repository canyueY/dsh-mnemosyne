# Bundled Mnemosyne Node

本目录是 `dsh-mnemosyne` 的 Python Memory Node 本体，来自
`桌宠/dsh-mnemosyne/service`（Apache-2.0），去掉了测试、报告、构建产物与 `.venv`。

启动（由 Host 适配层自动执行，一般不需要手动运行）：

```powershell
python src/cognitive_memory/main.py --host 127.0.0.1 --port 0 `
  --data-dir "<Memory Bank>" --token "<token>"
```

依赖见 `requirements.txt`；Python ≥ 3.10。REST 前缀 `/api/v1`，服务启动后在 stdout
输出 `DSH_MEMORY_BOUND <host>:<port>` 并写入 `<bank>/runtime/service.json`。
