# 家庭 AI 集群 — 一张消费级显卡上的完整本地 AI 栈

[English](README.md) | **中文**

> 把「Claude Code 级的 agent 体验」装进自己的硬件：Pi 当入口，Switchyard 智能分诊，
> 大小两个开源模型共享一张卡，模型崩了自动切云端 API 兜底。全部用现成开源件，
> 我们只造让它们变成一台「家庭 AI 电脑」的胶水层。已在 RTX 4090D 真机端到端验证。

## 一、界面介绍

三个入口，按用户类型分层：

**1. Web 控制台（:6006）——给普通用户和管理员**

- 聊天界面，车道下拉选择：`auto`（智能分诊）/ `small`（1.7B 前台）/ `large`（32B 专家）/ `cloud`（云端）
- **文件上传**：拖入 txt/md/csv/json/代码文件（≤2MB），直接让模型总结、分析、提取
- **路由透明化**：每条回答下方标注「干活的模型 · 端到端延迟 · 生成速度 tok/s」；
  右侧面板实时显示路由器当前策略、**升级事件流**（judge 每次把任务升级到大模型的理由原文）、
  累计请求/token 统计；顶栏徽章显示集群状态（正常 / 各种降级模式），车道红绿灯 5 秒刷新
- 降级时徽章变橙并说明流量去向（云端或幸存车道），恢复自动变绿

**2. `pi` 终端——给开发者**

SSH 进服务器敲 `pi`，体验等同 Claude Code：聊天、写代码、真实执行工具
（建文件、跑命令）。`/model` 随时切换四个车道。

**3. OpenAI 兼容 API——给一切现有工具**

```
POST http://<主机>:4000/v1/chat/completions    model: auto | small | large | cloud
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
| 升级判决 | judge 真实触发过，理由可在控制台查看 |

## 三、怎么使用

前置：NVIDIA 卡（24GB 档已验证）、Python 3.10+、`pip install vllm nemo-switchyard`、
Node 22+（装 Pi：`npm i -g --ignore-scripts @earendil-works/pi-coding-agent`）。

```bash
git clone https://github.com/murphyren225/local_infer.git && cd local_infer
# 模型权重下载到本地(国内走 ModelScope):
#   modelscope download --model Qwen/Qwen3-32B-AWQ  --local_dir /root/autodl-tmp/models/Qwen3-32B-AWQ
#   modelscope download --model Qwen/Qwen3-1.7B-FP8 --local_dir /root/autodl-tmp/models/Qwen3-1.7B-FP8
./homed/run_cluster.sh        # 一键起栈(大模型加载约 4 分钟),幂等,stop 全停
./homed/test.sh all           # 分层全测: small|large|router|console|pi 也可单测
./homed/ask.sh auto "随便派个活"
```

- 控制台：AutoDL 用户在实例页点「自定义服务」即得公网链接（就是 6006 端口）；
  其他环境 `ssh -L 6006:127.0.0.1:6006 <主机> -N` 后开 http://localhost:6006
- 云端兜底：`.env` 里写 `TOGETHER_API_KEY=...` 即启用真云端（不配则降级到幸存车道）
- 换模型：`INFERENCE_PRESET=<preset名> ./homed/run_cluster.sh`，
  见 [homed/inference/](homed/inference/)（新模型接入 = 加一个 preset 文件）
- 兜底演练：`./homed/test.sh failover`（故意杀掉 32B，看自动切换和自愈全程）

## 四、能做什么任务

- **前台类（小模型秒回，成本≈0）**：翻译、总结、改写润色、纠错、分类判断、
  信息提取、格式转换、起名、一句话文案
- **专家类（32B）**：技术方案分析、代码评审与调试、合同条款风险、根因分析、
  数学推理、长文写作
- **文件类（控制台上传）**：报表要点提取、文档摘要、CSV 初步分析、代码文件讲解
- **Agent 类（pi）**：写脚本并运行、批量处理目录文件、真实工具调用的多步任务
- `auto` 车道会自动判断以上任务该给谁干；答砸了 judge 会升级重试

## 组件架构

每个模块一个组件，互相只通过 HTTP 和文件通信（详见 [homed/README.md](homed/README.md)）：

| 组件 | 用的现成件 | 我们写的胶水 |
|---|---|---|
| harness | [Pi](https://pi.dev/) | 一份 provider 配置 |
| router | [NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard) | 健康感知的配置生成器 |
| inference | vLLM | 实测显存 preset（每模型家族一个） |
| failover | — | 看门狗 + 分段自愈（全自研，~120 行 shell） |
| console | FastAPI | 聊天/上传/路由可视化单页 |

## 模型支持

| 模型 | 状态 |
|---|---|
| Qwen3-32B-AWQ + Qwen3-1.7B-FP8 | ✅ 24GB 真机验证（当前默认） |
| Qwen3.8-27B | ⏳ 等 4-bit 量化（现有 bf16 56GB / FP8 28GB 均超 24GB） |
| GLM-5.3-Flash | 📋 preset 已预留：320B/18B MoE，最小量化 ~93GB，需 DGX Spark/128GB 级设备 |

## 项目状态（诚实版）

| 部分 | 状态 |
|---|---|
| 单机全栈（双 vLLM + Switchyard + Pi + 控制台 + 兜底） | ✅ 2026-09-06 真机端到端验证 |
| 故障切换 + 分段自愈闭环 | ✅ 破坏性演练通过（杀 32B → 40s 切换 → 自动复活） |
| 云端兜底走真实 API | ⚠️ 逻辑已通（假云端验证），真实 key 待插 |
| 多设备联动（Mac Hub + 远端 GPU 经隧道） | ✅ 2026-09 真机验证：homed init/link-gpu 两条命令，Mac 弱档 + 4090 强档分工，断链自动降级、重连自动恢复 |
| 设备自动发现（mDNS、join token） | 📋 设计完成，未实现 |
| 历史资产：Tandem 网关与 Agent 协议 | ✅ 见 docs/（路由评测集与显存档位已平移到本栈） |

## 文档

- [homed/README.md](homed/README.md) — 组件总览（每个组件目录内有各自 README）
- [docs/home-cluster.md](docs/home-cluster.md) — 家庭集群整体设计与二期多机蓝图
- [docs/architecture.md](docs/architecture.md) / [docs/routing.md](docs/routing.md) /
  [docs/agent-interface.md](docs/agent-interface.md) — 一期 Tandem 网关的设计与协议
- [docs/roadmap.md](docs/roadmap.md) — 路线图

## License

MIT
