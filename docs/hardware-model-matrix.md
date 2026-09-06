# 硬件×模型×任务 分工矩阵

> 2026-09 梳理。三条心法:**内存看总参数、速度看激活参数、稠密吃带宽/MoE 吃容量**。
> 状态标注: ✅ 真机实测 / ⚠️ 按实测外推 / 📋 调研或设计完成 / ⏳ 等上游

## 1. 硬件 → 推理系统

| 硬件 | 关键特征 | 该跑的系统 | 状态 |
|---|---|---|---|
| 消费独显 24GB (4090/4090D/3090) | ~1TB/s 高带宽 | vLLM (AWQ + FP8 KV;参数见 homed/inference/presets/qwen3-24gb.env) 或 SGLang | ✅ |
| 消费独显 12–16GB (4070/4080) | 高带宽小容量 | vLLM,模型降档 (config/profiles/) | ⚠️ |
| 工作站 32–48GB (5090/A6000) | 高带宽大容量 | vLLM/SGLang,可开 CUDA graph;4B 前台在此档可行 | ⚠️ |
| Apple Silicon Mac | 统一内存 16–128GB,常开低功耗 | MLX-LM (vLLM/SGLang 无 Metal 支持);或 Ollama/LM Studio | 📋 二期 |
| **NVIDIA DGX Spark** (GB10) | 128GB 统一内存,官方生态最全 | SGLang(官方 Spark 菜谱,nvfp4) / vLLM;2–4 台 RoCE 组网跑 300B MoE(spark-bench 实测) | 📋 |
| **Perplexity Portable Computer** | 硬件即 DGX Spark 128GB,预装 Perplexity 本地栈(编排器+Qwen3.8-27B,云端按审批升级) | 整机产品,不可自装;是我们整个方向的**参照产品** | 📋 对标 |
| **懒猫 AI Pod** (国产) | Jetson T4000/T5000,64/128GB,273GB/s,¥29,899 起,预装 Ubuntu+CUDA | vLLM-Jetson / Ollama / unsloth·llama.cpp(GGUF) | 📋 |
| 纯 CPU 旧机器 | 无 GPU | 只装 Pi 当终端;或 llama.cpp ≤2B | 📋 |
| 云端 API | 无限 | 逃生舱;key 只存网关 (homed/failover) | ✅ |

同为 128GB 级,三台的区别:DGX Spark 是生态标杆(引擎官方直接出菜谱),
Perplexity 是「买来即用的成品」,AI Pod 是价格更低的国产替代——
在我们的调度里它们都是「大内存车道」的候选宿主,preset 各配一份即可。

## 2. 模型 → 宿主硬件

| 模型 | 总参/激活 | 架构 | 4-bit 内存 | 宿主 | 状态 |
|---|---|---|---|---|---|
| Qwen3-1.7B-FP8 | 1.7B | 稠密 | ~2GB | 任意;24GB 卡与 32B 共存(util 0.11) | ✅ |
| Qwen3-4B-AWQ | 4B | 稠密 | 运行时 3.2GiB(fp16 词表层) | 需 32GB+ 才能当共存前台;24GB 实测装不下 | ✅(排除) |
| Qwen3-8B/14B-AWQ | 8/14B | 稠密 | 6/9GB | 12/16GB 卡的专家 | ⚠️ |
| Qwen3-32B-AWQ | 32.8B | 稠密 | 19GB (运行时 17.7GiB+开销) | 24GB 独显专家车道 | ✅ |
| Qwen3.8-27B | 27.8B | 稠密+多模态,262K ctx | ~16GB(待量化) | 24GB 独显下一代专家 | ⏳ |
| GLM-4-9B | 9.4B | 稠密(2024) | ~6GB | 被 Qwen3 系压制,不选 | 参考 |
| GLM-5.3-Flash | 321B/18B | MoE,1M ctx | 1bit 93GB~4bit 200GB | 128GB 单机(2-3bit) 或 4 台组网(4bit,spark-bench 实测 128.9 tok/s@4流) | 📋 preset 已留 |
| DeepSeek-V4-Flash(/Vision) | 291B/305B | MoE,原生视觉 | nvfp4 ~150GB | 2× DGX Spark(SGLang 官方菜谱) | 📋 |
| DeepSeek-V3 | 671B/37B | MoE | ~350GB | 数据中心,家用排除 | — |
| 云端大杯 (Qwen3-235B 等) | 235B+ | MoE | — | cloud 车道 | ✅ |

291/305/321B 挤在同一档不是巧合:各家都在瞄准 128–256GB 家用设备的容量甜点位。

## 3. 任务分工 —— 按 NeMo Switchyard 官方路由设计

不自创分法。以下直接取自 Switchyard 内置的 escalation judge 系统提示词
(`switchyard/lib/processors/prompts/escalation_judge.md`)——这是 NVIDIA
对「弱档/强档各管什么」的官方定义,再由我们映射到硬件。

### 3.1 双档框架:efficient(弱) 起步,judge 盯梢,确认后单程升级 strong(强)

**efficient 档负责**(官方原文归纳——"being stuck on those is usually temporary"):

- 常规编码、文件探索、单文件修改
- 常规调试、依赖与环境安装、多数重构
- 流程性/机械性摩擦:装工具、起服务、照菜谱执行、局部单文件测试修复

**升级到 strong 的三类卡点**(官方判据:卡点超出弱档能力,强模型能破局):

- **跨模块/跨代码库综合**:修复需要从代码库别处学会某个约定、契约或不变量并一致地应用——哪怕改动本身只是单文件
- **隐蔽不变量**:貌似合理的修复反复挂同一个测试,根因在没人碰过的行为契约上
- **跨模块根因、多步算法/形式化推理**:弱档一直"差一点点"

**谁都不该升**(官方明确:纯浪费):外部资源缺失——数据/文件不存在、服务永久性坏死、环境与需求矛盾。强模型变不出缺失的资源。

### 3.2 升级触发信号(judge 盯的四类"轨迹病症",官方原文归纳)

| 病症 | 表现 |
|---|---|
| 重复与循环 | 同一命令/编辑失败 2+ 次;近似工具调用反复;跟环境较劲(重试已被拒绝的做法) |
| 假进展 | 证据显示失败却宣布成功;跳过任务指定的验证;写了个空转的测试并基于假信号继续 |
| 跑偏与死路 | 活动不再服务原始任务;违反显式约束(改了不许碰的文件);没打开报错指向的文件就凭想象改代码;多轮无任何耐久产出 |
| 绝望动作 | 宣布任务不可能;rm -rf / 全量重装式的破坏性挣扎 |

**正常摩擦不升级**:TDD 先红后绿、报错下一轮就修掉、会话早期的探索死胡同、
缺工具时自适应换替代品、顺序尝试不同方案(换方案是适应,同方案重试才是循环)。

### 3.3 Switchyard 四种决策机制 → 我们的硬件映射

| 机制 | 适用场景 | 档位落在哪个硬件 |
|---|---|---|
| escalation(轨迹 judge,我们在用) | 多轮 agent 会话 | efficient=常开设备小模型(Mac/AI Pod/独显小分片);strong=最强在线节点(24GB 独显 32B;有 128GB 主机时,长上下文任务落 300B MoE);judge=与 efficient 同宿主;fallback_target_on_evict=strong |
| llm_classifier capability(答前估解题概率) | 单发请求,不想浪费第一答 | 分类器放常开小模型,分流同上 |
| stage_router(读对话既有信号,零额外调用) | 大流量省 judge 成本 | 同上,信号免费 |
| subagent_target(子代理定向) | harness 派生的子任务 | 子代理活默认发 efficient 宿主 |

云端 API 不在 Switchyard 的档位设计里,是我们 failover 组件补的第三层:
本地全灭或明确超纲时的逃生舱。

## 相关生态(2026-09 快照)

- NVIDIA Personal AI Router:官方的局域网多设备聚合(RTX/DGX/Mac→单端点,按空闲分发)。
  验证品类;我们的差异在能力分级×成本阶梯×judge 兜底×企业功能
- spark-bench:4×DGX Spark RoCE 组网跑 GLM-5.3-Flash 的社区实测
- unsloth:转型 local inference,GGUF 动态量化(1bit 起)单机跑 300B MoE
- Wafer:推理内核优化平台(任意硬件 1.5–5x),在我们 inference 层之下;Wafer Pass 可作云端供应商
