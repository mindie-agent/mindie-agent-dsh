# mindie-agent-dsh 0.1.25：知识工具全会话默认常驻、静默语义 Skill 与 `Stop` 前置授权门禁

[English](mindie-agent-dsh-0.1.25.en.md)

- **全会话默认启用 `knowledge_*` 工具**：`host3.mjs`、`scripts/knowledge_tools.mjs`、`scripts/mindie_sidecar.py` 与 `scripts/knowledge_mcp.py` 默认向所有会话暴露 `knowledge_query`、`knowledge_explain`、`knowledge_feedback`、`knowledge_distill`、`knowledge_sync` 与 `mindie_entry`，无需前置手动激活；只读检索与同步零租约直通，`knowledge_feedback` 与 `knowledge_distill` 在默认域或目标域 `admission_path` 按需签发默认会话租约（`not_before=0.0`），并严格遵守显式 `deactivate` 撤销状态。
- **`Stop` 采集前置授权与工程范围门禁**：`host3.mjs`（`agent/turn-stopping`）、`scripts/capture_stop.py` 与 `scripts/mindie_launch.py` 在打开 SQLite 或派发子进程前先校验 `mindie-consent.json`（`choice == "contribute"`）及 `mindie-community.json` 的 `project_roots` 范围；关闭或目录越界时零 SQLite 开销快速跳过（`< 100ms`），已授权且在范围内时自动对齐会话 `project_root` 租约、按需初始化 `state-v4.sqlite3` 并完成公开对话脱敏采集。
- **静默语义 `mindie-agent` Skill**：移除 `skills/mindie-agent/SKILL.md` 的 `disable-model-invocation: true` 限制并精简为 `<= 30` 行领域工作流参考，统一清理插件说明与文档中的斜杠命令入口表述。
