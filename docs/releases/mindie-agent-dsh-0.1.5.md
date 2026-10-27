# mindie-agent-dsh 0.1.5

本版本固定正式 remote-dev 0.10.8（`4b1c5cab5d36cdaff79d27e4288c6d5f60667c72`），使 DSH 直接挂载的共享 MCP 获得完整的 host/port 参数说明与缺参修复元数据。无需新增 DSH 侧错误状态机。

直接 SSH 端点需要同时提供 host 与 port；22 是示例端口。工具说明同时保留 alias、已注册 resolver 与省略端点字段时的配置默认端点规则。共享组件只在确认请求未提交的 caller 错误中返回有界 `input_repair`；不会自动重放请求，也不会把不确定的远端副作用改成缺参失败。

macOS 上的真实 DSH + DeepSeek Flash 原生 MCP 客户端验收中，第一次调用故意省略 port，客户端显示共享 MCP 的端点错误；模型第二次仅补入 port=22，通过真实 A3 SSH 返回唯一 stdout 标记，耗时 10.323 秒。原始 job record 为 succeeded。该客户端把 RPC error.data 省略为错误文本，成功文本也不包含退出码，因此验收不采用模型自行声称的 exit code。该结果验证原生客户端与候选共享 MCP 的参数纠正链路，不代表用户现有 DSH 插件已安装或完成宿主重载。

运行时仍锁定 knowledge 0.13.3（`38a42f6f778cfa555d8918fdcacd12cee4a181f5`）与 diagnostics 0.5.4（`5ae643e19223f2181ecdbaa3edaf464137c4805e`）。Python/JavaScript 合同、三系统 CI 与正式发布流水线分别验证最终提交。既有 host.mjs 原生加载边界保持不变；候选准备、绑定生效与宿主加载分别记录，历史平台验收不改标为本版本。
