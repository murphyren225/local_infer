# 打通设计：Switchyard 选模型 → PAIR 选硬件 → SGLang 推理 → 投机解码

> 状态：设计稿，2026-09-26。目标是把四段用现有开源件的 fork 串成一条能跑的链，
> 每处改动尽量小，并把"以后换成 RL"的接口先钉死。批准后按 §6 顺序搭。

## 0. 一条请求走过的路

```
Pi ──OpenAI HTTP──▶ Switchyard(fork)            ① 选模型：weak / strong / cloud
                        │  target = 本机 PAIR 代理
                        ▼
                    PAIR(fork) 代理 :1234        ② 选硬件：有该模型的节点里挑一台（负载 + 会话粘性）
                        │  mTLS 到目标节点
                        ▼
                    SGLang 实例（每节点一个）     ③ 推理：前缀缓存 + （后续）投机解码
```

三个组件各自的职责不变，我们只改三处：Switchyard 的**判定器换成可插拔的决策器**，
PAIR 的**引擎换成 SGLang**，SGLang 的**启动参数由 preset 控制**（含 drafter）。

## 1. Switchyard fork：决策器接口

### 1.1 现状（读 0.2.0 源码得到的事实）

- `escalation_router` 的判定逻辑在
  `switchyard/lib/processors/escalation_judge_request_processor.py`。
- 判定器已经是**注入的**：构造函数接受 `client: LLMClassifierClient | None`，
  调用只有一处 `await self._client.classify(model, system_prompt, request_summary)`，
  返回内容解析成 `EscalationVerdict{escalate: bool, reason: str}`。
- 判定失败 fail-open 到弱池；连续 `escalate_confirmations`（默认 2）次才锁定；
  `judge_min_turn` 默认 **3**——前两轮根本不判（这就是单发请求永远不升级的原因）。
- 自带 RL 轨迹日志：`--enable-rl-logging` 用 `RlLoggingRequestProcessor` /
  `RlLoggingResponseProcessor` 把每轮的 `message_history` 写成 JSON。
- 配置入口 `profiles/escalation_router_config.py`（`judge: LlmTarget` 等字段），
  处理链在 `profiles/escalation_router_profile_config.py:106` 组装。

结论：不需要动路由主干，改动集中在"谁来出裁决"和"日志里多记什么"。

### 1.2 改动

**新增 `DecisionProvider` 协议**（`switchyard/lib/decision/provider.py`）：

```python
class DecisionState(BaseModel):
    session: str | None
    turn: int
    task_type: str | None            # 来自 x-task-type，可空
    summary: str                     # 现有 _summarize_for_judge 的产物
    recent_messages: list[dict]      # 最近 N 条，截断
    has_tools: bool

class Decision(BaseModel):
    route: dict[str, float]          # {"weak": p, "strong": p, "cloud": p}，和为 1
    escalate: bool                   # 给现有锁定逻辑用的二值视图 = route["strong"] >= threshold
    confidence: float
    task_type: str | None
    rewrite: float | None            # 要不要改写的概率，此阶段只记录不执行
    reason: str
    provider: str                    # "llm" | "jev" | "anyjev" | "rl" | "rules"
    latency_ms: float

class DecisionProvider(Protocol):
    async def decide(self, state: DecisionState) -> Decision: ...
```

**四个实现**（同一目录）：

| provider | 实现 | 用途 |
|---|---|---|
| `llm` | 包装现有 `OpenAIChatLLMClassifierClient`，把 `EscalationVerdict` 映射成 `route={"strong": 1 if escalate else 0, ...}` | 现状，默认，零行为变化 |
| `rules` | 无模型：按 `task_type` / 长度 / 关键词的静态表 | 无任何模型可用时的兜底 |
| `http` | `POST {url}/decide` 发 `DecisionState`，收 `Decision` | **RL 路由器、AnyJev、Jev 适配都走这个**：决策器是独立进程，训练、替换不碰 Switchyard |
| `jev` | 直接调 TypeSafe/OpenRouter 的 `/v1/systemone`，问三个类型化问题 | 有 key 时的对照组 |

`EscalationJudgeRequestProcessor` 改为持有 `DecisionProvider`；`process()` 里把
`classify → EscalationVerdict` 那一段换成 `decide → Decision`，其余（锁定、计数、
fail-open、stats）不动。`Decision` 整体写入 `ctx.metadata["_decision"]`。

**配置**（`routes.yaml` 的 `judge` 段加字段，向后兼容）：

```yaml
judge:
  provider: http            # llm | rules | http | jev；缺省 llm
  url: http://127.0.0.1:4100
  model: qwen3-1.7b         # provider=llm 时才用
  base_url: ...
  escalate_threshold: 0.5   # route["strong"] 超过即 escalate
  confirmations: 2
  min_turn: 1               # 从 3 改成 1：单发请求也要判
```

**日志**：`RlLoggingResponseProcessor` 的每条记录增加 `decision`（上面的 `Decision`）、
`served_tier`、`usage`、`latency_ms`、`explore: bool`（ε 探索标记，由 `http`
provider 返回）。这就是 RL 路由器的训练数据，格式在 `docs/agent-stack-draft.md` §1.3。

**ε 探索**：在处理器里加 `explore_epsilon`（默认 0）：以该概率忽略决策、随机选层，
并在日志里标记。RL 上线前设 0.05–0.1 攒反事实数据。

### 1.3 决策器服务（我们自己的，放 local_infer 仓库）

`decider/`：一个 FastAPI 进程，`POST /decide`，内部可切后端：

- `anyjev`：调 AnyJev（Qwen3 在 SGLang/vLLM 上，`Question.choice`）——本地、免训练。
- `rl`：加载自训策略（先逻辑回归赌博机，后 LoRA 小模型）。
- `jev`：转发到 TypeSafe（对照用）。

对 Switchyard 来说这些都是 `provider: http`，换后端不改 Switchyard。

## 2. PAIR fork：引擎换 SGLang

### 2.1 事实

- 引擎的装/起/停/列模型是 manifest 驱动（`manifests/*.json`），加 JSON 即可。
- 但"把请求送给引擎"按引擎名写死了两份：`ollama-proxy`（Ollama 格式）和
  `lmstudio-proxy`（OpenAI 格式），broker、调度器、mDNS 键、手动节点探测里都是
  `"ollama"` / `"lmstudio"` 两个字面量。
- `lmstudio-proxy` 本质是一个 OpenAI 兼容转发器，SGLang 说的正是 OpenAI 格式。

### 2.2 两步走

**第一步（manifest-only，不改 Go）**：把 `lmstudio.json` 换成 SGLang 的启动定义，
引擎内部 id 仍叫 `lmstudio`，`display_name` 改成 "SGLang"：

```json
"runtime": {"mode": "command", "port": 30000, "bind": "127.0.0.1",
            "start": [["{cli}", "-m", "sglang.launch_server", "--model-path", "{model}",
                       "--port", "{port}", "--host", "{host}", "{extra_args}"]],
            "ready": {"http": "http://127.0.0.1:{port}/v1/models", "status": 200, "timeout_s": 900}},
"actions": {"list_models": {"http": {"method": "GET", "path": "/v1/models"},
                            "result": {"array": "data", "field": "id"}}}
```

这样 PAIR 的 OpenAI 代理（:1234）就在给 SGLang 路由，节点发现、模型清单、
负载分发全部沿用。Switchyard 的 target 指向 `http://127.0.0.1:1234/v1`。
代价：面板上引擎显示位置是 LM Studio 那一栏；一个节点只能有一个 SGLang 实例
（= 一个模型）。

**第二步（Go 改动，几百行）**：把 `lmstudio` 泛化成 `openai` 引擎类型，允许一个
节点登记多个实例（弱模型 + 强模型各一个 SGLang 进程），在 `reserveCandidate`
加会话粘性（读 `x-switchyard-session-id`，同会话固定副本）和按吞吐加权。
第一步跑通、数据证明需要后再做。

### 2.3 一个节点几个模型（需要你定）

PAIR 原生是"一个引擎一个进程一个模型"。第一步下：

- 4090（24GB）跑 SGLang 32B-AWQ = 强池节点；
- 弱池要另一个节点：levi 跑 SGLang 1.7B/8B，或 Mac 跑 Ollama 1.7B（PAIR 支持混合引擎，
  Switchyard 的 weak target 指向 `:11434`，strong 指向 `:1234`）。

同一张 24GB 卡上同时跑两个 SGLang（我们 vLLM 时代的显存分片做法）要等第二步。

## 3. SGLang：上游不 fork

- 每节点一个实例，参数在 `cluster/inference/presets/*.env` 的 `*_ENGINE=sglang`
  与 `*_ARGS` 里；`cluster/node/engine.py` 加 `sglang` 分支（命令 `python -m sglang.launch_server`）。
- 24GB 档起点：`--quantization awq --context-length 8192 --mem-fraction-static 0.85 --enable-torch-compile`（待校准）。
- 前缀缓存（RadixAttention）默认开，配合 PAIR 第二步的会话粘性才真正生效。
- **投机解码是最后一步、纯参数**：`--speculative-algorithm EAGLE3 --speculative-draft-model-path <head> --speculative-num-steps/--speculative-eagle-topk/--speculative-num-draft-tokens`；
  先用 Qwen3 的社区 EAGLE-3 头，再用自有轨迹训领域头。不改任何组件，只改 preset。

## 4. 接口总表（改完后固定，后续只换实现）

| 边界 | 契约 |
|---|---|
| Pi → Switchyard | OpenAI HTTP + `x-switchyard-session-id` + `x-task-type` |
| Switchyard → 决策器 | `POST /decide`：`DecisionState` → `Decision`（§1.2） |
| Switchyard → PAIR | OpenAI HTTP 到本机 `127.0.0.1:1234/v1`，`model` = SGLang 的 served name |
| PAIR → SGLang | OpenAI HTTP（本机直连或 mTLS 到对端代理再直连其引擎） |
| 训练数据 | Switchyard RL 日志（每轮：状态摘要、决策、实际层、结果、token、延迟、explore） |

## 5. 试验床与限制

- SGLang 只跑 Linux + CUDA。**Mac 跑不了**（Intel，无 CUDA）；AutoDL 的 4090 能跑但
  **不在局域网**，PAIR 只能用之前的隧道 + 改端口方案接它。
- levi（RTX，局域网内）是唯一能同时满足"局域网 + CUDA"的机器，但它的操作系统我还不知道；
  Windows 要走 WSL2（CUDA on WSL 可用，PAIR 在 Windows 侧、SGLang 在 WSL 侧，localhost 互通）。
- Switchyard 和决策器跑在 Mac（Hub），不需要 GPU；AnyJev 后端需要一个 GPU 上的 Qwen3。

## 6. 搭建顺序

1. fork 两个仓库到你的 GitHub（`murphyren225/Switchyard` 分支 `decision-provider`，
   `murphyren225/Personal-AI-Router` 分支 `sglang-engine`），local_infer 的安装脚本改为装 fork。
2. Switchyard：`DecisionProvider` + `llm`/`rules`/`http` 三个实现 + 配置字段 + 日志扩展 + ε 探索；
   单元测试覆盖"provider=llm 行为与原版逐字一致"。
3. 决策器服务 `decider/`，先只有 `rules` 和 `anyjev` 后端。
4. PAIR 第一步（SGLang manifest），在 levi/4090 上装 SGLang，PAIR 代理路由到它。
5. local_infer：`routes.py` 生成的 target 改指本机 PAIR 代理；`engine.py` 加 sglang；preset。
6. 端到端：Pi → Switchyard → PAIR → SGLang，重放 20 条轨迹，记 token/延迟/去向。
7. 投机解码：preset 加 EAGLE-3 参数，对比接受长度和 tok/s。

## 7. 已确认的决定（2026-09-26）

- levi 暂不用；SGLang 节点先用 AutoDL 4090（隧道方案）。
- PAIR 第一步只改 manifest，一节点一模型；先用两台放**同一个最小模型**看硬件选择。
- 决策器接口：完整概率 `route{weak,strong,cloud}` + confidence + task_type。
- 代码放**私有**新仓库：`murphyren225/switchyard-decision`（分支 `decision-provider`）、`murphyren225/pair-sglang`（分支 `sglang-engine`）。

## 8. 进度

| 步骤 | 状态 |
|---|---|
| Switchyard fork：`DecisionProvider`（llm/rules/http/jev）、`x-task-type`、ε 探索、决策写入 RL 轨迹、YAML 键 `judge.provider/url/min_turn/escalate_threshold/explore_epsilon` | **完成**，上游 87 个相关测试 + 12 个新测试通过；Mac 上以 `provider: http` 真跑：turn 1–2 问决策器、turn 2 锁定强池、turn 3 不再问 |
| 决策器服务 `decider/`（rules / jev / anyjev 后端，`logs/decisions.jsonl`） | **完成**（rules 真跑；anyjev 等 GPU） |
| local_infer 接线：`routes.py` 生成 `judge.provider: http`（`DECIDER_URL`）、`PAIR_PROXY_URL` 把 target 指向 PAIR 代理；`cluster init` 拉起 decider；`engine.py` 加 `sglang`；preset `qwen3-24gb-sglang.env`；个人端本机服务透传 `x-task-type` | **完成** |
| PAIR fork 第一步：`lmstudio.json` 改为启动 SGLang（进程模式，argv 为字面值，按节点用 `engines/lmstudio.json` 覆盖模型路径与参数） | **完成**，4090 真机由 PAIR 引擎管理器拉起 SGLang，`engine:models` 报 `qwen3-1.7b`，`:1234` 代理可对话 |
| 4090 上端到端：Mac 本机服务 → 隧道 → Switchyard fork（:4000）→ decider（:4100）→ PAIR 代理（:1234）→ SGLang（:30000） | **完成**（2026-09-27，证据见下） |
| 投机解码 preset（EAGLE-3） | 下一步 |

端到端证据（`scratchpad/e2e_chain.py`，Mac 发起）：`small` 直达路由 0.5 s 返回 `chain-ok`；`auto` 三轮、`x-task-type: code`：decider 两次收到 `task_type=code`，给出 `route{weak 0.3, strong 0.7}`；Switchyard RL 轨迹三条依次 `served_tier = weak / strong / strong`（turn 2 达到 confirmations=2 锁定强池，turn 3 不再问决策器），前两条带完整 `decision`；PAIR 账本五条 `qwen3-1.7b / lmstudio / completed`。当前弱池与强池是同一个 `qwen3-1.7b`，所以"锁定强池"只在轨迹里可见，不改变实际模型；换成两档模型只需改 routes 生成器的 target。

两处踩坑记入设计：PAIR 引擎管理器进程模式的 `runtime.args` 只替换 `{install_dir}/{host}/{port}` 占位符，不展开 `$ENV`，所以模型路径这类节点差异走 `<config>/engines/<engine>.json` 深合并覆盖，而不是环境变量；`runtime.bin` 会被 `detect` 命中的路径覆盖，因此 `detect` 指向 venv 的 python，argv 以 `-m sglang.launch_server` 开头。

Switchyard fork 的安装方式（Intel Mac 没有 Rust 工具链，不重编 wheel）：装官方 0.2.0 wheel 取得 `switchyard_rust` 扩展，再把 fork 的 `switchyard/` 纯 Python 包覆盖到 site-packages（`bin/install.sh` 已加这一步，`SWITCHYARD_FORK` 指向 fork 目录）。

尚未验证：两台同模型节点之间的硬件选择（Mac 跑不了 SGLang、levi 暂不用，只有 4090 一个 SGLang 节点）；以及 anyjev 后端上 GPU。

