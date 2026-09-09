# AI 集群 — 自己的硬件上的完整本地 AI 栈

[English](README.md) | **中文**

> 把「Claude Code 级的 agent 体验」装进自己的硬件：每个人机器上的 Pi 当 harness，
> Switchyard 智能分诊，大小两个开源模型共享一张卡，模型崩了自动切云端 API 兜底。
> 全部用现成开源件，我们只造胶水层。已在 RTX 4090D 真机端到端验证。

两套系统，形态和 Claude Code 一样：harness 在用户这边，模型在另一边，两者之间
唯一的契约是一个网关地址：

| 系统 | 装在哪 | 目录 | 入口 |
|---|---|---|---|
| **个人端** | 每个人自己的机器，一人一套 | `client/` | `bin/install-client.sh URL`（一条命令：装 Pi、接线、起本机服务） |
| **模型端** | 集群里的设备（GPU 机、Mac、大内存主机） | `cluster/` | `bin/install.sh`（一条命令：装依赖、下模型、开始服务） |

## 一、界面介绍

**1. `pi` 终端（个人端）——给开发者**

在自己电脑上敲 `pi`，体验等同 Claude Code：聊天、写代码、在本机真实执行工具
（建文件、跑命令），模型跑在集群上。`/model` 随时切换车道。接内部系统 = 在
[client/pi/extensions/](client/pi/extensions/) 加一个 TypeScript 文件。

**2. 本机服务（:7000，个人端）——给普通用户和脚本**

`bin/client up` 在自己电脑上起一个小服务：网页有两个页签——**Chat**（一次推理，
本机什么都不跑）和 **Agent**（任务交给本机的 Pi，在你指定的目录里读写文件、跑命令，
模型端只出答案）；脚本用 `client ask` / `client batch` 直接提交推理任务。车道下拉
`auto` / `small` / `large` / `cloud`，每条回答标注「干活的模型 · 端到端延迟」。
除 Python 外零依赖。

**3. 集群管理台（:6006，Hub）——给管理员**

设备卡片（硬件档案、在线状态、最近解码指标）、路由器当前策略、**升级事件流**
（judge 每次把任务升级到大模型的理由原文）、累计统计；顶栏徽章显示集群状态，
降级时变橙并说明流量去向。另保留一个带文件上传的对话区做测试。

**4. OpenAI 兼容 API——给一切现有工具**

```
POST http://<Hub>:4000/v1/chat/completions    model: auto | small | large | cloud
```

Anthropic Messages 格式同样支持（Claude 系客户端可直连）。整个 API 表面积就这么大。

## 二、产品性能（RTX 4090D 24GB 真机实测）

| 指标 | 实测值 |
|---|---|
| 双模型共存显存 | 23.45 / 24.56 GB（安全余量 1.1GiB，经 OOM 演练校准） |
| 小车道（Qwen3-1.7B-FP8） | 首 token 0.15s，108 tok/s（4 并发合计） |
| 大车道（Qwen3-32B-AWQ） | 首 token 0.17s，38 tok/s（2 并发合计） |
| 故障检测 → 路由切换 | ~30–40 秒（看门狗 10s 间隔 × 连续 2 次失败） |
| 大模型崩溃自愈 | 2–5 分钟自动恢复（分段重启；配云端 key 期间零中断） |
| 升级判决 | judge 真实触发过，理由可在管理台查看 |

## 三、怎么使用

### 模型端：空白机器一条命令

前置：装好驱动的 NVIDIA 卡（24GB 档已验证），或 Mac/CPU 机；Python 3.12+。

```bash
curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash
```

它克隆仓库并执行 `bin/install.sh`：服务 venv + Switchyard；GPU 机装 vLLM 并从
ModelScope 下载 Qwen3-32B-AWQ 与 Qwen3-1.7B-FP8（`--hf` 走 Hugging Face），Mac 编译
llama.cpp 并下载 1.7B GGUF；然后自动 `cluster init`——命令返回时机器已在服务
（32B 加载约 4 分钟）。后续设备：`bin/install.sh --join http://<Hub>:6006 --token JOIN-xxxx`；
远端 GPU 经 SSH：`bin/cluster link-gpu "ssh -p 43314 root@gpu-host"`。
`./cluster/test.sh all` 做分层测试。

### 个人端：自己电脑一条命令

前置：Node 22+（Pi 装进用户目录，不需要 sudo）。本机服务只需要 Python 3。

```bash
curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash -s -- personal http://<Hub>:4000
```

然后：

```bash
bin/client pi                                  # Pi harness:工具在本机执行,模型在集群上
open http://127.0.0.1:7000                     # 个人网页:Chat(只推理) / Agent(本机 Pi 干活)
bin/client ask "翻译成英文:今天很忙" --lane small  # 一次推理,不经 harness
bin/client batch prompts.txt -c 4 -o out.jsonl # 一批推理任务并发提交
```

- 管理台：http://<Hub>:6006（AutoDL 点「自定义服务」；其他环境
  `ssh -L 6006:127.0.0.1:6006 <主机> -N`）
- 云端兜底：Hub 的 `.env` 里写 `TOGETHER_API_KEY=...`（不配则降级到幸存车道）
- 换模型：`bin/cluster init --preset <preset名>`，
  见 [cluster/inference/](cluster/inference/)（新模型接入 = 加一个 preset 文件）
- 兜底演练：`./cluster/test.sh failover`（故意杀掉 32B，看自动切换和自愈全程）

## 四、能做什么任务

- **前台类（小模型秒回，成本≈0）**：翻译、总结、改写润色、纠错、分类判断、
  信息提取、格式转换、起名、一句话文案
- **专家类（32B）**：技术方案分析、代码评审与调试、合同条款风险、根因分析、
  数学推理、长文写作
- **Agent 类（pi）**：在自己电脑上写脚本并运行、批量处理目录文件、真实工具调用的多步任务
- `auto` 车道会自动判断以上任务该给谁干；答砸了 judge 会升级重试

## 组件架构

模型端按设计文档的层分包，层间只通过 HTTP 和文件通信；个人端是独立的包，两者互不
import（详见 [docs/repo-layout.md](docs/repo-layout.md)）：

| 包 | 层 | 用的现成件 | 我们写的部分 |
|---|---|---|---|
| `client/pi/`、`client/web/` | 个人端 · harness | [Pi](https://pi.dev/) | Pi 接线与扩展；个人网页与本机代理 |
| `cluster/router/` | 调度层 | [NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard) | 路由表生成；网关进程管理 |
| `cluster/node/` | 资源层 | vLLM / llama.cpp | 硬件探测与定池；preset 解析；引擎进程 |
| `cluster/control/` | 控制平面 | — | 节点注册表、健康、看门狗、分段自愈 |
| `cluster/access/` | 管理台 | FastAPI | 集群管理台与节点登记接口 |

## 模型支持

| 模型 | 状态 |
|---|---|
| Qwen3-32B-AWQ + Qwen3-1.7B-FP8 | ✅ 24GB 真机验证（当前默认） |
| Qwen3.8-27B | ⏳ 等 4-bit 量化（现有 bf16 56GB / FP8 28GB 均超 24GB） |
| GLM-5.3-Flash | 📋 preset 已预留：320B/18B MoE，最小量化 ~93GB，需 DGX Spark/128GB 级设备 |

## 项目状态

| 部分 | 状态 |
|---|---|
| 单机全栈（vLLM 双池 + Switchyard + 管理台 + 兜底） | 2026-09 真机验证 |
| 故障切换 + 分段自愈 | 破坏性演练通过（杀 32B → 40s 切换 → 自动复活） |
| Mac Hub + 远端 GPU 联动 | 真机验证；断链自动降级、重连恢复 |
| 注册表驱动的热插拔 | `init` / `link-gpu` 已验证；`join` 已实现待双机验证 |
| 个人端（接线 + 个人网页） | 本机模式可用；Hub 托管零安装模式在路线图 |
| 云端兜底走真实 API | 逻辑已通，真实 key 待插 |
| 池内多副本负载均衡、自动发现、过载保护 | 路线图 |

## 文档

- [docs/design.md](docs/design.md) — **系统主文档**（三部分：系统设计 / 接口契约 / 技术实现与运维）
- [docs/hardware-model-matrix.md](docs/hardware-model-matrix.md) — 产品页（硬件清单 / 用户配置 / 设备联动 / 任务示例）
- [docs/roadmap.md](docs/roadmap.md) — 路线图
- [docs/repo-layout.md](docs/repo-layout.md) — 仓库结构与设计文档的对应（每个目录为什么存在）
- [cluster/README.md](cluster/README.md)、[client/README.md](client/README.md) — 两套系统各自的组件总览

## License

MIT
