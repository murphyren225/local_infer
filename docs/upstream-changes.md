# 对开源框架的改动清单

原则：开源框架原样使用，只有在它没有留扩展点、而功能又必须在它内部实现时才改源码。能用配置、覆盖文件、
外部服务解决的，一律放在本仓库。下面按框架列出现状，2026-09-27 核对。

## 三个仓库

| 仓库 | 是什么 | 可见性 |
|---|---|---|
| [murphyren225/local_infer](https://github.com/murphyren225/local_infer) | 主仓库：我们自己的全部代码、配置、测试、文档 | 公开 |
| [murphyren225/switchyard-decision](https://github.com/murphyren225/switchyard-decision)，分支 `decision-provider` | NVIDIA NeMo Switchyard 0.2.0 的 fork | 私有 |
| [murphyren225/pair-sglang](https://github.com/murphyren225/pair-sglang)，分支 `sglang-engine` | NVIDIA Personal AI Router 0.1.1 的 fork | 私有 |

两个 fork 的 `main` 分支就是上游原样，和工作分支做 diff 即是全部改动。

## 逐个框架

| 框架 | 用法 | 源码改动 |
|---|---|---|
| Pi（harness） | npm 包原样安装 | 无。接入靠 `~/.pi/agent` 的 provider 配置 |
| SGLang 0.5.20 | pip 包原样安装 | 无。模型、服务名、投机解码全是启动参数 |
| llama.cpp | 原样编译 | 无。同上 |
| LM Studio（LEVI） | 原样安装 | 无 |
| PAIR 0.1.1 | 服务端全部用上游源码构建 | 服务端 **0 行**；桌面界面 8 行（可选） |
| Switchyard 0.2.0 | 官方 wheel + 覆盖 fork 的 Python 包 | 10 个文件，+648 / −37（其中测试 190 行） |

### PAIR：服务端零改动

需要的三件事都没有改 PAIR：

1. **LM 槽位里跑 SGLang / llama-server。** PAIR 启动引擎管理器时会把 `<config>/engines/<engine>.json` 深合并到自带 manifest 上。
   `tools/pair/engine_override.py` 把整个引擎定义（探测路径、进程模式、启动参数、就绪/健康探针、模型列表接口）写进这个覆盖文件。
   在上游原版引擎管理器上验证过：4090 上由它拉起 SGLang，模型进入名单，已加载列表正确。
2. **把云上的 4090 接进来。** 用 PAIR 自带的手动节点（`node/add`），配置写在桌面端的 `configs/manual-nodes.json`；网络靠我们自己的 SSH 隧道（`bin/tunnel-4090`）。
3. **Mac 和隧道共用一台机器时的端口冲突。** PAIR 端口写死，隧道要占原端口，所以 Mac 上的 PAIR 整体 +10000。
   这是**构建时补丁**（`tools/pair/port-shift.patch`，16 个文件里的端口常量），打在上游源码的副本上，不进 fork。真局域网部署不需要它。

fork 里剩下的唯一改动是桌面端 `desktop/src/ui/utils/overview-nodes.ts` 加 8 行：总览页除了集群成员也画出手动节点的卡片。
不加它功能不受影响（路由、账本都正常，我们的管理台也会显示所有节点），只是 PAIR 自己的窗口里看不到手动节点。

曾经改过、现已撤回的：`manifests/lmstudio.json`（改成 SGLang）、`registry.go`（覆盖文件优先级）和相应测试。覆盖文件方案验证通过后全部还原为上游。

### Switchyard：必须改的部分

上游的升级判定是"让一个 LLM 读会话摘要，回答升不升级"。我们要的是可插拔的决策器并为 RL 攒数据，这几点上游没有扩展点：

| 改动 | 文件 | 为什么不能放在外面 |
|---|---|---|
| `DecisionProvider` 接口和四个实现（llm / rules / http / jev） | `switchyard/lib/decision/`（新增） | 判定器在请求处理链内部被调用 |
| 判定处理器改为调用接口；读 `x-task-type`；ε 探索 | `escalation_judge_request_processor.py` | 同上；任务类型来自请求头，只有网关内部拿得到 |
| RL 轨迹里记录 `decision` 和 `served_tier` | `rl_logging_response_processor.py` | 轨迹由 Switchyard 写，外面补不了"实际走了哪一层" |
| YAML 里的 `judge.provider / url / escalate_threshold / explore_epsilon` | `route_bundle.py`、两个 profile 配置 | 配置解析在 Switchyard 内 |
| `reasoning_effort: none` 视为合法值 | `reasoning_effort_normalizer.py` | 上游 bug：把 none 改写成 high，意图相反。应提给上游 |

`provider: llm` 是默认值，行为和上游逐字一致（有测试覆盖），所以这个 fork 对不用决策器的部署是透明的。

不改 Switchyard 的退路也存在：把决策器伪装成一个 OpenAI 接口，让上游的 LLM 判定器去问它。这样能做二值的升/不升，
但拿不到请求头里的任务类型，轨迹里没有概率和实际层，也没有探索标记，RL 训练数据就不完整。所以保留 fork。

## 我们自己的代码在哪（local_infer）

| 目录 | 内容 |
|---|---|
| `client/` | 个人端：本机服务（会话 ID、透传路由提示）、Pi 接入 |
| `cluster/router/` | 路由表生成（池 → 模型 → PAIR 代理）、网关进程管理 |
| `cluster/control/` | 节点注册表、健康检查、watchdog |
| `cluster/access/console/` | 管理台四页：对话、链路总览、架构全貌、批量测试 |
| `cluster/inference/` | 引擎 preset、draft 注册表 |
| `decider/` | 决策器服务：rules / jev / anyjev / rl 后端，RL 训练脚本 |
| `tools/pair/` | PAIR 的外部工具：无头驱动、覆盖文件生成、端口偏移补丁 |
| `bin/` | 安装与运维脚本：install、cluster、client、tunnel、pair-desktop、pair-node-setup |
| `tests/` | 单元测试、逐环节测试、整链测试、手工命令脚本 |
| `docs/` | 设计、测试说明、本清单 |
