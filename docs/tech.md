# 技术文档（每个接口背后是什么、怎么连接）

> 与 docs/api.md 配套：那边定义「接口长什么样」，这边说明「谁在实现它、
> 请求怎么流动、部件之间靠什么连接」。分层定义见 §1，逐接口技术溯源见 §3。

## 1. 分层与选型总表

| 层 | 组件 | 选型 | 改动量 |
|---|---|---|---|
| L3 接入 | 聊天控制台 | FastAPI + 单页 HTML（`homed/console/`） | 自研（~300 行） |
| L3 接入 | CLI agent | Pi（pi.dev），provider 配置指向网关 | 零改动 |
| L2 调度 | 网关 | **NVIDIA NeMo Switchyard 0.2**（PyPI） | 零改动，纯配置 |
| L1 资源 | GPU 节点引擎 | **vLLM 0.28**（CUDA） | 零改动，参数即档位 |
| L1 资源 | CPU/Mac 节点引擎 | **llama.cpp**（本机编译，GGUF 模型） | 零改动 |
| L1 资源 | 云端兜底 | 任意 OpenAI 兼容 API（Together 等） | 配 key 即用 |
| 控制平面 | 注册/编译/健康/生命周期 | 自研 shell（`homed/router|failover|cli`，~400 行） | 全自研 |

**连接原则**：数据面只有 HTTP（OpenAI 线格式一种「普通话」）；控制面只有
文件 + 进程信号。组件间无库依赖、无自定义 RPC。

## 2. 连接拓扑与进程清单

```
浏览器 ──HTTP──> 控制台:6006 ──HTTP──> 网关:4000 ──┬─HTTP─> llama.cpp:8002 (Hub 本机, CPU)
pi CLI ─────────HTTP────────────────────┘         ├─HTTP─> vLLM:8001 (GPU 节点)
任意 OpenAI/Anthropic 客户端 ──HTTP──> 网关:4000 ──┘└─HTTPS─> 云端 API (兜底)

跨设备物理链路: Hub 与 GPU 节点走普通 TCP/IP —— 同局域网直连,
或 SSH 隧道(当前部署: ssh -N -L 8001:127.0.0.1:8001, 断线 5s 自动重连循环)。
每台设备各自供电;设备间不存在任何直连线缆。

控制平面(常驻于 Hub, 不在数据路径上):
  watchdog(10s 健康探测) ─触发→ gen_routes(重编译路由表) ─文件+重启→ 网关
```

每台设备上的进程清单（当前部署实测）：

| 设备 | 进程 | 端口 |
|---|---|---|
| Hub（Mac） | Switchyard 网关、控制台、llama.cpp 弱档、SSH 隧道保持循环、watchdog | 4000 / 6006 / 8002 |
| GPU 节点（4090） | vLLM 强档 | 8001（经隧道映射到 Hub 的 8001） |

## 3. 逐接口技术溯源

### `POST :4000/v1/chat/completions`（主对话，api.md §1.1）

- **实现者**：Switchyard（Python 进程，无状态反向代理）。
- **`model` 字段的处理**：
  - `small/large/cloud` → 查路由表直通对应后端（`type: model` 路由）；
  - `auto` → `escalation_router` 策略：请求默认进弱池；一个判定器
    （复用弱池模型，`disable_reasoning`）按会话轨迹打分，连续越过阈值
    （`confirmations`）则把该会话**单向**固定到强池；会话被 LRU 逐出时
    落到 `fallback_target_on_evict`。
- **转发时的改写**：`model` 重写为目标真实模型名；其余字段透传。
- **格式翻译**：每个后端在路由表里声明方言（`format: openai_chat |
  anthropic_messages`），Switchyard 在转发/回传时双向翻译——
  OpenAI↔Anthropic 的字段映射：`messages[role=system]` ↔ 顶层 `system`、
  字符串 content ↔ 类型化块数组、`finish_reason` ↔ `stop_reason`、
  `prompt/completion_tokens` ↔ `input/output_tokens`。翻译只存在于 L2 这一层。
- **路由表来源**：`homed/router/routes.generated.yaml`——由控制平面的
  `gen_routes.sh` 按「当前健康的节点集合」编译生成；网关本身从不探测健康。

### `POST :4000/v1/messages`（Anthropic 入口，api.md §1.2）

- 同一个 Switchyard 进程，启动参数 `--inbound both`；入站即翻译成内部
  统一格式，后续路径与上条完全相同。

### `GET :4000/v1/models` / `GET :4000/v1/routing/stats`

- Switchyard 内建。models 由路由表推导；stats 是进程内累计器
  （请求数、token、错误、判定器自身的调用与失败率——判定器故障采取
  fail-open：留在弱池并计数，绝不因裁判宕机烧强池）。

### `POST :6006/api/chat`（api.md §3.2）

- **实现者**：控制台 FastAPI 进程。纯代理：转发到 `:4000`，测端到端延迟
  附加为 `_console.latency_ms`，并按响应 `model` 记录「最近一次解码指标」
  内存表（设备面板数据源）。**不含任何路由逻辑。**

### `POST :6006/api/upload`（api.md §3.3）

- FastAPI multipart 处理；UTF-8 解码 + 乱码率检测拒绝二进制；截断 12000
  字符。文件内容不落盘、不进任何存储——只作为文本注入该次对话的 messages。

### `GET :6006/api/status`（api.md §3.4）

- 聚合三类来源：① 对 `:8001/:8002/health` 与 `:4000` 的实时探测；
  ② 控制平面状态文件（`logs/cluster_mode`、`lanes.env`、`local_hw.info`、
  `remote_gpu.info`、`weak.src`、`tunnel.target`）；③ 自身内存里的解码指标表。
  硬件档案由 CLI 在 init/link 时探测落盘（本机 `sysctl`，远端 `nvidia-smi`）。

### 节点 `:800x/v1/chat/completions` + `/health`（api.md §4）

- **GPU 节点**：vLLM `serve`，关键参数即「档位」（preset 文件承载）：
  显存分片 `--gpu-memory-utilization`、上下文 `--max-model-len`、
  `--kv-cache-dtype fp8`、批上限、`--tool-call-parser hermes`（agent 工具
  调用必需）、`--reasoning-parser qwen3` + 默认关思考。24GB 档为真机
  校准值（32B-AWQ util 0.81 / 5120 ctx + 安全余量 1.1GiB）。
- **Mac/CPU 节点**：llama.cpp `llama-server`（本机源码编译，CPU-only），
  GGUF 模型，`--jinja --reasoning-budget 0` 关思考，`-a <名字>` 声明
  对外模型名。
- 引擎属于节点内部实现：换 vLLM→SGLang、换模型，只改该节点的启动
  参数文件，上层零感知。

## 4. 控制平面：文件即接口

| 文件 | 写者 | 读者 | 内容 |
|---|---|---|---|
| `routes.generated.yaml` | gen_routes | 网关（重启加载） | 路由表 |
| `logs/cluster_mode` | gen_routes | 控制台、演练脚本、人 | 状态枚举（api.md §3.4） |
| `logs/lanes.env` | CLI/编排脚本 | gen_routes、控制台 | 车道名 |
| `presets/*.env` | 人（校准后固化） | 编排脚本 | 节点档位参数 |
| `logs/*.info` | CLI 探测 | 控制台 | 设备硬件档案 |
| `logs/*.pid` | 各启动器 | stop/status/看门狗 | 进程句柄 |
| `.env` | 管理员 | gen_routes | 云端凭据（永不下发到节点） |

**故障闭环**：watchdog 每 10s 探测各节点 `/health`，连续 2 次失败判死 →
调 gen_routes 按幸存集合重编译（降级查表：死节点的池指向云端或幸存池）→
重启网关（秒级）→ 后台派 heal 拉起死节点（GPU 节点冷启动需先腾空整卡，
见 failover/README）→ 恢复后对称还原。看门狗自身死亡的判据：
`cluster_mode` 过期（watchdog.log 时间戳超过 2×探测周期）。

## 5. 一次 auto 请求的完整生命周期（把上面全串起来）

```
用户在聊天页输入指令
→ 控制台包装 {model:"auto", messages:[…]} POST :4000        (C1, OpenAI 格式)
→ 网关查会话粘性表(未升级) → 判定器读 messages 轨迹 → 弱池
→ 改写 model=qwen3-1.7b-gguf, POST :8002 (Hub 本机 llama.cpp) (C2)
→ 引擎解码, 返回 choices+usage
→ 网关透传; 控制台附加 latency_ms, 更新设备面板指标
→ (若后续轮次判定器连续报"超纲" → 会话固定到强池,
   之后同会话请求改发 :8001 经隧道到 GPU 节点的 vLLM)
```

## 6. 已知边界与技术债

- 网关与控制平面同宿主，Hub 是单点（v1 接受，同交换机单点同理）；
- 内网无鉴权：企业部署在 4000/6006 前加反向代理鉴权，或用 Switchyard
  的 `forward_auth`；
- 控制台直接读控制平面状态文件（应经只读状态接口）——已记技术债；
- 多设备接入目前是 CLI 手动 `link-gpu`（SSH 隧道）；同局域网自动发现
  （mDNS/join token）在路线图。
