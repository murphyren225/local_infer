# AI 集群技术文档

## 0. 文档说明

**读者对象**：部署、运维和二次开发本系统的工程师。

**与 design.md 的分工**：design.md 描述系统结构、设计决策与对外 API 契约；
本文档描述每一层的实现规范、配置格式与运维方法。同一事实只在一处维护：
对外接口以 design.md 第二部分为准，配置与参数以本文档为准。

**维护规则**：接口变更先改 design.md 的契约再改实现；配置项变更先改本文档
对应规范节再改脚本。每节末尾的「已知限制」随实现同步更新。

## 1. 总览

### 1.1 定位

本系统是一个跨设备的 LLM 推理集群：一个网关进程将 OpenAI 兼容的请求按
策略分发到多台机器上的推理引擎，云端 API 是最后的回退。

```mermaid
flowchart LR
    C["客户端<br/>控制台 / Pi / SDK"] --> G["网关 :4000"]
    G --> W["弱池节点"]
    G --> S["强池节点"]
    G --> CL["云端 API"]
```

### 1.2 术语

术语表见 design.md 第一部分 §1，本文档沿用：节点、池、Hub、路由表、
判定器、会话粘性、降级。

### 1.3 技术选型与版本清单

| 组件 | 选型 | 验证版本 | 许可 | 版本约束 |
|---|---|---|---|---|
| 调度 | NVIDIA NeMo Switchyard | 0.2.0（PyPI） | Apache-2.0 | 需 Python ≥ 3.12；PyPI 版配置为 YAML，GitHub 主干为 TOML，勿混用文档 |
| GPU 推理 | vLLM | 0.28.0 | Apache-2.0 | CUDA 平台 |
| CPU/Mac 推理 | llama.cpp | b10819 / 源码构建 | MIT | 官方 macOS 二进制要求 ≥ 13.3，更旧系统需源码编译 |
| 备选 GPU 推理 | SGLang | 未部署 | Apache-2.0 | 见 §4.4 |
| Harness | Pi | 0.74.2 | 开源（pi.dev） | Node ≥ 22.14 可运行；0.75+ 需 Node ≥ 22.19 |
| 控制台 | FastAPI + uvicorn | — | MIT | 依赖 python-multipart |
| 默认模型 | Qwen3 系（32B-AWQ / 1.7B） | — | Apache-2.0 | 生产部署应锁定权重 revision |

## 2. 接入层

### 2.1 Pi：选型理由与集成方式

接入层的设计要求是薄：厚逻辑属于调度层和节点。Pi 是以此为立身之本的
harness——零改动接入，集成物只有一份 provider 配置文件。对比项
（OpenHands 等平台型 harness 自带路由与运行时，与本系统调度层职责重叠）
不采用。集成方式：安装 CLI 后将 §2.2 的配置写入 `~/.pi/agent/models.json`，
编排脚本自动完成。

### 2.2 provider 配置规范（`~/.pi/agent/models.json`）

```json
{
  "providers": {
    "home": {
      "baseUrl": "http://<Hub>:4000/v1",
      "api": "openai-completions",
      "apiKey": "home",
      "models": [
        { "id": "auto",  "name": "Cluster Auto",  "input": ["text"],
          "contextWindow": 5120, "maxTokens": 1024,
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } },
        { "id": "small", "contextWindow": 8192, "maxTokens": 1024, "input": ["text"],
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } },
        { "id": "large", "contextWindow": 5120, "maxTokens": 1024, "input": ["text"],
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } }
      ]
    }
  }
}
```

| 字段 | 约束 |
|---|---|
| `baseUrl` | 网关地址，以 `/v1` 结尾 |
| `api` | 固定 `openai-completions` |
| `apiKey` | 网关无鉴权，填任意非空串 |
| `models[].id` | 必须是路由表里存在的路由名 |
| `models[].contextWindow` | 不得超过对应池的引擎 `max-model-len`，否则 Pi 会构造超限请求收到 400 |
| `cost` | 本地池填零；该文件在 Pi 内 `/model` 时热加载 |

### 2.3 控制台

单页应用（`homed/console/`），页面五个区域：对话流（每条回答标注实际执行
模型、端到端延迟、tok/s）、设备卡片（在线状态、硬件档案、最近一次解码指标）、
路由策略说明（随 `cluster_mode` 变化）、升级事件流（判定器结论原文）、累计
统计。数据来源为 5 秒轮询 `GET /api/status`。三个后端接口的规格见
design.md 第二部分 §3。控制台不含路由逻辑，仅代理与展示。

### 2.4 第三方客户端接入

OpenAI SDK：

```python
from openai import OpenAI
client = OpenAI(base_url="http://<Hub>:4000/v1", api_key="home")
r = client.chat.completions.create(model="auto",
        messages=[{"role": "user", "content": "…"}])
```

Anthropic SDK（网关以 `--inbound both` 同时接受 Messages 格式）：

```python
from anthropic import Anthropic
client = Anthropic(base_url="http://<Hub>:4000", api_key="home")
r = client.messages.create(model="auto", max_tokens=1024,
        messages=[{"role": "user", "content": "…"}])
```

约束：内网无鉴权，`api_key` 任意非空；`model` 只接受路由名或节点真实模型名。

## 3. 调度层 · Switchyard

### 3.1 部署形态与启动参数

单 Python 进程，无状态，由控制平面启动与重启：

```
switchyard serve --routing-profiles homed/router/routes.generated.yaml \
                 --host 0.0.0.0 --port 4000 --inbound both
```

| 参数 | 说明 |
|---|---|
| `--routing-profiles` | 路由表文件（§3.2）。启动时一次性加载，改表须重启（秒级） |
| `--inbound both` | 同时接受 OpenAI 与 Anthropic 入站格式 |
| `--workers N` | 多进程。会话粘性表随进程分片，前置负载均衡需按会话哈希；单进程无此问题 |

网关不探测健康、不管理进程；配置非法时启动即退，错误定位单行输出到日志。

### 3.2 路由表规范（`routes.generated.yaml`）

由 `homed/router/gen_routes.sh` 按当前健康节点集合生成，手改会在下次生成时
被覆盖。顶层仅 `defaults` 与 `routes` 两键。

```yaml
defaults:
  api_key: home            # 本地引擎不校验,但字段必须非空

routes:
  auto:                    # 键名即客户端可用的 model 值
    type: escalation_router
    weak:
      model: qwen3-1.7b-gguf          # 必须等于该节点 served-model-name/-a 别名
      base_url: http://127.0.0.1:8002/v1
    strong:
      model: qwen3-32b-awq
      base_url: http://127.0.0.1:8001/v1
    judge:
      model: qwen3-1.7b-gguf
      base_url: http://127.0.0.1:8002/v1
      confirmations: 1                # ≥1;连续 N 次升级结论才生效
      disable_reasoning: true         # 判定器禁思考,保证 JSON 结论可解析
      max_completion_tokens: 512
    fallback_target_on_evict: strong  # 只能取 strong|weak;会话被 LRU 逐出后的去向
  small:
    type: model                       # 直通路由
    model: qwen3-1.7b-gguf
    base_url: http://127.0.0.1:8002/v1
  large:
    type: model
    model: qwen3-32b-awq
    base_url: http://127.0.0.1:8001/v1
  cloud:
    type: model
    model: <供应商模型 id>
    base_url: <供应商地址>/v1
    api_key: <真实 key>               # 仅此处出现,永不下发节点
    # format: anthropic_messages      # 后端为 Anthropic 协议时声明,网关负责翻译
```

字段约束：

| 字段 | 约束 |
|---|---|
| `routes.<name>` | name 即对外 model 枚举；未知键导致启动失败 |
| `type` | `model` 或 `escalation_router`（本系统只用这两种） |
| `weak/strong/judge.model` | 必须与节点声明的模型名逐字相等，否则上游 404 |
| `fallback_target_on_evict` | 必填；取值为 tier 名而非模型名 |
| 后端 `format` | 缺省 `openai_chat`；声明后网关做双向协议翻译（§3.4） |

生成器的降级语义（健康集合 → 路由表形态）：

| 健康状态 | auto | small | large | cluster_mode |
|---|---|---|---|---|
| 全健康 | escalation（弱→强） | 直通弱池 | 直通强池 | `normal` |
| 强池死，有云 key | 直通云端 | 直通弱池 | 直通云端 | `degraded-large-cloud` |
| 强池死，无 key | 直通弱池 | 直通弱池 | 直通弱池 | `degraded-large-small` |
| 弱池死 | 直通强池/云端 | 改指幸存方 | 直通强池 | `degraded-small-*` |
| 全死，有 key | 全部直通云端 | 同 | 同 | `degraded-all-cloud` |
| 全死，无 key | 生成失败，保留旧表 | — | — | `dead` |

### 3.3 路由策略

`auto` 路由的决策序列（每请求）：

1. 会话已在粘性表 → 强池，判定器不参与；
2. 轮次 < `min_judge_turn` → 弱池，不裁决；
3. 判定器读会话压缩视图（任务框架 + 最近 N 轮，每条截断），输出
   `{"escalate": bool, "reason": str}`。判据是轨迹（重复循环、假进展、
   跑偏、绝望动作），不是单条消息难度；
4. 连续 `confirmations` 次升级结论 → 会话写入粘性表，此后单向走强池；
   任何一次否定结论清零计数；
5. 判定器自身失败 → fail-open 停留弱池并计入 stats，不因判定器不可用
   误耗强池。

粘性表为进程内 LRU（上限 1 万会话），逐出后按 `fallback_target_on_evict`。
默认方向为「弱池先行」；准确性优先的部署可在生成器中对调 weak/strong
并为机械任务保留弱池白名单，属配置变更，不动代码。

### 3.4 协议翻译

每个后端在路由表中声明方言，翻译只发生在调度层，客户端与节点均无感知：

| OpenAI | Anthropic |
|---|---|
| `POST /v1/chat/completions` | `POST /v1/messages` |
| `Authorization: Bearer` | `x-api-key` + `anthropic-version` |
| `messages[role=system]` | 顶层 `system` 字段 |
| `content` 为字符串 | `content` 为类型化块数组 |
| `max_tokens` 可选 | `max_tokens` 必填（翻译时自动补） |
| `finish_reason` / `prompt_tokens` | `stop_reason` / `input_tokens` |

### 3.5 可观测性

| 接口 | 字段 | 告警判据 |
|---|---|---|
| `GET /v1/models` | 路由与模型目录 | 非 200 即网关不可用 |
| `GET /v1/routing/stats` | `total_requests/total_errors/total_tokens/cost_estimate/classifier.*` | `total_errors` 持续增长＝存在不可用下游；`classifier.total_errors` 增长＝判定器 fail-open 中，路由退化为「从不升级」 |

## 4. 资源层 · 推理引擎

### 4.1 引擎选型矩阵

三个引擎分属两个生态位；路由之后完全等价（对调度层只是 URL），
选型是节点内部决定，落在该节点的 preset 文件里。

```
该节点有 NVIDIA GPU?
 ├─ 有 → vLLM 或 SGLang(同生态位,二选一,按任务取舍,见下表)
 └─ 无 → Apple Silicon? MLX-LM : llama.cpp
```

vLLM 与 SGLang 的取舍按任务负载：

| 负载特征 | 倾向 | 依据 |
|---|---|---|
| 通用混合请求、求稳 | vLLM | 生态与模型适配最广，本系统已在 24GB 档完成校准 |
| 多轮 agent 会话、同模板批量任务占比高 | SGLang | RadixAttention 前缀复用，重复上下文不重算 |
| 结构化输出（JSON/语法约束）密集 | SGLang | 约束解码为其原生能力 |
| 硬件有官方部署菜谱（如 DGX Spark） | SGLang | 直接采用官方参数，省去校准 |

本文档不做全局钦定：同一集群内不同节点可用不同引擎。

### 4.2 vLLM 节点规范

参数由 preset 文件承载（`homed/inference/presets/`）。24GB 档实测值：

| 参数 | 值（24GB 档） | 作用与约束 |
|---|---|---|
| `--served-model-name` | `qwen3-32b-awq` | 对外模型名，必须与路由表逐字一致 |
| `--gpu-memory-utilization` | 0.81 | 显存配额。校准方法见 §5.2 |
| `--max-model-len` | 5120 | 上下文上限，受 KV 池约束 |
| `--kv-cache-dtype` | `fp8` | KV 显存减半，24GB 双模型共存的必要条件 |
| `--max-num-seqs` / `--max-num-batched-tokens` | 8 / 1024 | 批上限。默认值按上千并发预留激活显存，单卡必须调低 |
| `--enable-auto-tool-choice --tool-call-parser hermes` | — | Pi 等带工具客户端的必要条件，缺失时请求直接 400 |
| `--reasoning-parser qwen3` | — | 思考内容与正文分离 |
| `--default-chat-template-kwargs` | `{"enable_thinking":false}` | Qwen3 默认开思考，会耗尽 max_tokens 导致空正文 |
| `--enforce-eager` | — | 放弃 CUDA graph 换 1–2GiB 显存 |

启动顺序约束：同卡双模型时大模型必须先启动——32B 冷启动瞬时峰值约
20.5GiB（AWQ 权重重排临时缓冲），需要整卡空闲；小模型驻留时原地重启大
模型必然 OOM，自愈流程因此采用分段重启（§8.2 案例 2）。

### 4.3 llama.cpp 节点规范

```
llama-server -m <model>.gguf --host 127.0.0.1 --port 8002 \
             -c 8192 --jinja --reasoning-budget 0 -a <对外模型名>
```

| 参数 | 说明 |
|---|---|
| `-m` | GGUF 权重。1.7B 档用 Q8_0（ModelScope 官方仓库仅提供该量化） |
| `--jinja --reasoning-budget 0` | 启用模板并禁思考，等价于 vLLM 侧的思考开关 |
| `-a` | 对外模型名，等价于 vLLM 的 `--served-model-name` |
| `--parallel N` | 槽位数，默认 1（串行）。槽位均分 `-c` 上下文，增并发以缩短单请求上下文为代价 |

构建：官方 macOS 二进制要求系统 ≥ 13.3；更旧系统源码编译
（`cmake -DGGML_METAL=OFF -DGGML_BLAS=OFF`，CPU-only 约 10 分钟）。

### 4.4 SGLang（预留）

替换动机与时机见 §4.1。接入方式与 vLLM 相同：新建 preset，声明启动命令与
参数，路由表无需变化。128GB 档（DGX Spark 单台或组网）优先采用官方
cookbook（docs.sglang.io 的模型菜谱，含 nvfp4 与多机配置），不自行校准。

### 4.5 云端后端

要求：OpenAI 或 Anthropic 兼容 HTTP。key 仅存于 Hub 的 `.env`
（`TOGETHER_API_KEY` 等），由生成器写入路由表的 cloud 路由，不下发节点、
不进日志。未配 key 时生成器以本地节点顶替 cloud 路由，降级语义见 §3.2 表。
已知缺口：无预算上限与用量审批，降级期间云端费用不受控（路线图项）。

### 4.6 新模型接入流程

1. **确认可用量化**。目标显存档位能装下的格式（AWQ/GPTQ/FP8/GGUF）与
   许可；记录权重仓库与 revision。注意运行时体积大于纸面体积：fp16 词表层
   与运行开销可多出 0.7–1.5GiB（4B-AWQ 实测运行时 3.2GiB）。
2. **建 preset**。复制最近档位的 preset，改模型路径与对外名。
3. **定解析器三件套**。该模型家族的工具调用解析器（Qwen3 为 `hermes`）、
   思考开关、reasoning parser。三者任一缺失的症状明确：工具请求 400、
   正文为空、思考混入正文。
4. **显存校准**（GPU 节点）。从保守 `gpu-memory-utilization` 起步，逐步上调
   至引擎报出的 KV 池满足目标上下文；双模型共存时给整卡保留 ≥1GiB 余量
   （§5.2 的教训值）。
5. **验收**。依次通过：`/health` 200；带 tools 请求不 400；短请求正文非空；
   `test.sh all`；破坏性演练 `test.sh failover`。全过后提交 preset。

## 5. 硬件档位

### 5.1 档位总表

| 档位 | 代表硬件 | 池角色 | 状态 |
|---|---|---|---|
| 24GB 独显 | RTX 4090/4090D | 强池（32B）＋判定器；可兼弱池分片 | 实测 |
| 12–16GB 独显 | 4070/4080 | 强池降档（8–14B） | 外推 |
| 32–48GB | 5090 / RTX 6000 Ada | 强池满上下文＋4B 弱池 | 外推 |
| 常开低功耗 | Mac、小主机 | 弱池、Hub | 实测（Intel Mac） |
| 128GB 统一内存 | DGX Spark、懒猫 AI Pod | 长上下文池（300B MoE 3–4bit） | 调研 |
| 组网舱 | 2–4× DGX Spark（RoCE） | 长上下文池满血（4bit） | 调研 |

准确性优先的产品化部署以 24GB 档为最低配置；更小硬件仅作接入终端。

### 5.2 24GB 档校准记录

实测常数（RTX 4090D，vLLM 0.28，2026-09）：

| 量 | 值 |
|---|---|
| Qwen3-32B-AWQ 运行时权重 | 17.7 GiB |
| Qwen3-4B-AWQ 运行时权重 | 3.2 GiB（含 fp16 词表层 0.74 GiB） |
| 每进程 CUDA 上下文（记账外） | ~0.45 GiB |
| 32B 冷启动瞬时峰值 | ~20.5 GiB |
| 32B fp8 KV | 0.125 MiB/token |
| 稳态安全余量（OOM 演练后定值） | ≥1.1 GiB |

结论：32B-AWQ 与 4B-AWQ 无法在 24GB 共存（合计需求超可用约 1GiB），
弱池配 1.7B；4B 弱池需要 32GB+ 档。校准方法：固定批参数后二分
`gpu-memory-utilization`，以引擎报告的 KV 池 token 数满足目标上下文为准，
再以真实 agent 负载压测确认余量（曾在余量 80MiB 时被 94MiB 的运行时
分配打崩，故定 1.1GiB）。

### 5.3 组网舱

单台装不下且需要 4bit 质量的 300B MoE 时使用。原则：舱内的张量并行与
RoCE 配置出厂预置，对集群表现为一个节点、一个 URL；组网复杂度不越过
节点边界。

## 6. 控制平面

### 6.1 文件契约

| 文件 | 写者 | 读者 | 内容 |
|---|---|---|---|
| `homed/router/routes.generated.yaml` | gen_routes | 网关 | 路由表 |
| `logs/cluster_mode` | gen_routes | 控制台、脚本、人 | 状态枚举（§6.2） |
| `logs/lanes.env` | CLI/编排脚本 | gen_routes、控制台 | 池内模型名 |
| `homed/inference/presets/*.env` | 人（校准后固化） | 编排脚本 | 节点档位参数 |
| `logs/*.info` | CLI 探测 | 控制台 | 设备硬件档案 |
| `logs/*.pid` | 各启动器 | stop/status/看门狗 | 进程句柄 |
| `.env` | 管理员 | gen_routes | 云端凭据 |

### 6.2 健康与降级状态机

```mermaid
stateDiagram-v2
    [*] --> normal
    normal --> degraded : 某池连续2次探测失败\n重写路由表并重启网关
    degraded --> normal : 池恢复,对称还原
    degraded --> dead : 全部本地池失效且无云 key
    dead --> normal : 节点拉起后重新生成
```

状态取值即 `logs/cluster_mode` 枚举（§3.2 表）。探测周期 10 秒，判死
条件为连续 2 次失败；控制平面自身失效的判据是该文件时间戳超过 2×周期
不更新，此时数据面照常但失去自动降级能力。

### 6.3 CLI 规范

| 命令 | 语义 | 前置条件 | 产物 | 失败行为 |
|---|---|---|---|---|
| `homed init` | 本机成为 Hub：起弱池引擎、生成路由表、起网关/控制台/看门狗 | venv 内有 switchyard | `lanes.env`、`local_hw.info`、各 pid | 弱池启动失败不中断，落到远借模式继续 |
| `homed link-gpu "<ssh 目标>"` | SSH 隧道接入远端 GPU 节点并重生成路由 | 免密 key 已配 | 隧道守护循环、`remote_gpu.info` | 隧道不通则退出，已起组件不受影响 |
| `homed status` | 各组件健康与当前模式 | — | — | — |
| `homed stop` | 按 pid 全停 | — | 清除 pid 文件 | — |

单 GPU 机整机部署用 `homed/run_cluster.sh`（幂等；`stop` 全停）。
测试入口 `homed/test.sh [small|large|router|console|pi|failover|all]`，
`failover` 为破坏性演练不含在 `all` 内。

## 7. 并发与容量

### 7.1 各组件并发机制与饱和行为

见 design.md 第一部分 §4 的表；本文档不重复。要点：唯一的压力点是
GPU 节点，机制为连续批处理，上限由 KV 池与批参数决定，饱和表现为排队。

### 7.2 容量规划方法

以池的聚合吞吐与目标回答长度估算，常数用实测值：

```python
# 24GB 档实测: 强池 38 tok/s(2 并发聚合), 弱池(vLLM) 108 tok/s(4 并发聚合)
def answers_per_minute(pool_tok_s: float, avg_answer_tokens: int = 200) -> float:
    return pool_tok_s * 60 / avg_answer_tokens

strong = answers_per_minute(38)        # ≈ 11 答/分钟
weak   = answers_per_minute(108)       # ≈ 32 答/分钟
# 判定器开销: 弱池有效容量 × 0.85
# 经验规模: 单标准盒支撑 10–20 人团队日常;超出则池内加节点或收窄任务
```

### 7.3 已知限制

无过载拒绝（不返回 429，只排队）；无租户配额与限流；llama.cpp 弱池默认
单槽位。三者均在路线图，不是设计选择。

## 8. 运维

### 8.1 监控清单

| 探测对象 | 健康值 | 故障签名 | 证据位置 |
|---|---|---|---|
| 各节点 `/health` | 200 | 连接拒绝/超时；日志 `EngineDeadError` | `logs/vllm-*.log`、`logs/small.log` |
| 网关 `/v1/models` | 200 | 连接拒绝；配置错则启动日志单行 error | `logs/switchyard.log` |
| `stats.total_errors` | 不增长 | 持续增长＝下游不可用 | `/v1/routing/stats` |
| `stats.classifier.total_errors` | 不增长 | 增长＝判定器 fail-open，升级失效 | 同上 |
| 控制台 `/api/status.mode` | `normal` | `degraded-*` 为降级中 | 页面徽章 |
| 控制平面心跳 | `cluster_mode` 时间戳新鲜 | 过期＝失去自动降级 | `logs/watchdog.log` |

### 8.2 故障手册（含真实案例）

**案例 1：强池运行中 OOM。** 症状：请求 500，`vllm-large.log` 出现
`torch.OutOfMemoryError`（实例：`Tried to allocate 94.00 MiB … 66.44 MiB is free`）
继而 `EngineDeadError`。根因：稳态余量不足，真实负载激活峰值超过启动期
profiling。处置：看门狗自动降级并自愈，无需人工；复发则下调该节点
`max-num-batched-tokens` 或 utilization。预防：余量 ≥1.1GiB（§5.2）。

**案例 2：强池自愈失败。** 症状：降级后长时间不恢复，`heal.log` 中 32B
启动 OOM。根因：冷启动峰值 20.5GiB 需整卡，弱池驻留时原地重启必败。
处置：自愈脚本已实现分段重启（先撤弱池腾卡）；若仍失败查
`logs/watchdog.log` 与 `heal.log`。

**案例 3：远端节点断连（隧道）。** 症状：`cluster_mode` 变
`degraded-large-*`，设备卡片红点。处置：隧道循环每 5 秒自动重连；对端
关机则属预期降级，恢复后 `homed link-gpu …` 归队。

**案例 4：网关启动即退。** 症状：`/v1/models` 连接拒绝，
`switchyard.log` 单行 `error: invalid route bundle: …`。根因：路由表字段
非法（常见：`fallback_target_on_evict` 填了模型名而非 tier 名）。处置：
按错误行修生成器，勿手改生成文件。

**案例 5：回答为空但请求 200。** 症状：`content` 为空串。根因：模型思考
模式未关，思考耗尽 `max_tokens`。处置：确认节点启动参数含思考开关
（vLLM `--default-chat-template-kwargs`，llama.cpp `--reasoning-budget 0`）。

**案例 6：带工具请求 400。** 症状：Pi 首次调用即报
`"auto" tool choice requires --enable-auto-tool-choice`。处置：补
`--enable-auto-tool-choice --tool-call-parser <family>` 后重启节点。

### 8.3 安全边界

内网部署默认无鉴权；对外暴露必须在 4000/6006 前置反向代理鉴权。云端
key 只存 Hub 的 `.env`。上传文件不落盘，仅注入当次请求；对话不持久化。
除显式 `cloud` 路由与降级模式外，数据不出内网。云端用量无预算护栏，
为已知缺口。

## 附录 A：端口与进程清单

| 设备 | 进程 | 端口 |
|---|---|---|
| Hub | Switchyard 网关 / 控制台 / 弱池引擎 / 看门狗 /（多机时）SSH 隧道循环 | 4000 / 6006 / 8002 |
| GPU 节点 | vLLM 强池引擎 | 8001 |

## 附录 B：OpenAI / Anthropic 协议对照速查

见 §3.4 映射表；完整请求示例见 design.md 第二部分 §1.1 与 §1.2。

## 附录 C：版本兼容性记录

| 事项 | 记录 |
|---|---|
| nemo-switchyard | 需 Python ≥ 3.12；PyPI 0.2.0 配置为 YAML bundle，GitHub 主干文档描述的是 TOML 新 API，按安装来源对号 |
| llama.cpp 官方 macOS 二进制 | 依赖 macOS ≥ 13.3 的 Accelerate 符号；13.1 需源码编译 |
| Pi 0.75+ | 需 Node ≥ 22.19（0.74.2 可在 22.14 运行） |
| Qwen3-1.7B GGUF（ModelScope 官方仓） | 仅提供 Q8_0 量化 |
| vLLM 0.28 + Qwen3 | 工具解析器为 `hermes`；思考默认开启，需显式关闭 |
| 大文件下载 | 国内网络下断点续传重试循环为必要实践（curl -C - 循环） |
