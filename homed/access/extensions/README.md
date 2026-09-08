# Pi 扩展（接内部系统的地方）

往 Pi 里加能力不改 `pi.py`，而是在本目录加文件。`homed init` 时 `access/pi.py`
把这里的 `*.ts` 同步到 `~/.pi/agent/extensions/`，Pi 启动时自动加载。

一个扩展 = 一个 TypeScript 文件，向 Pi 注册一个或多个工具（`pi.registerTool`）。
工具的 `execute` 可以任意调用 HTTP（`fetch`）、文件系统或子进程，权限等于运行 Pi
的用户。接内部系统的模式：一个系统一个文件，每个文件里是「查询 / 创建 / 更新」
几个工具，参数用 JSON Schema 声明，Pi 会把声明原样交给模型做工具调用。

`cluster-status.ts` 是模板：让 agent 能查询本集群的运行状态（调控制台的
`/api/status`）。照它的形状写第二个文件就是接第二个系统。

约束：
- 凭据不写进扩展文件；从环境变量读（`process.env.X`），环境变量只在运行 Pi 的机器上设置。
- 扩展在 Pi 进程里执行，不在网关里；网关只透传 `tool_calls`（design.md 第一部分 §8）。
- 这里的示例按 pi 0.74 的扩展 API 编写，升级 Pi 后以 pi.dev 文档为准。
