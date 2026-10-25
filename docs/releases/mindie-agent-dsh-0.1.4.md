# mindie-agent-dsh 0.1.4

本版本固定 remote-dev `1fefcc38`，对齐 diagnostics `0.5.3`，并保留 DSH per-session 隔离、660000ms 宿主超时与大日志有界头尾/完整引用。预览失败保留真实命令结果并显式标记 degraded。

DSH 的 `host.mjs` 是宿主启动时加载的 launcher；更新器安装新 native package 后设置 `needs_host_reload=true`。已有会话继续使用旧已加载代码且不被杀；新 generation 的 `host3` 只在空闲切换，只有宿主自然重载/新进程后 `host.mjs` 本体才切换。更新回执不把“已安装”冒充“已 active”。

无正式 tag/release；历史 verified 证据保持原版本，不伪造成 0.1.4 实测。
