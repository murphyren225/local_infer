# 硬件×模型×任务 分工矩阵

> 2026-09 梳理。三条心法:**内存看总参数、速度看激活参数、稠密吃带宽/MoE 吃容量**。
> 状态标注: ✅ 真机实测 / ⚠️ 按实测外推 / 📋 调研或设计完成 / ⏳ 等上游

## 1. 硬件 → 推理系统

| 硬件 | 关键特征 | 该跑的系统 | 状态 |
|---|---|---|---|
| 消费独显 24GB (4090/4090D/3090) | ~1TB/s 高带宽 | vLLM (AWQ + FP8 KV;参数见 homed/inference/presets/qwen3-24gb.env) 或 SGLang | ✅ |
| 消费独显 12–16GB (4070/4080) | 高带宽小容量 | vLLM,模型降档 (config/profiles/) | ⚠️ |
| 工作站 32–48GB (5090/A6000) | 高带宽大容量 | vLLM/SGLang,可开 CUDA graph;4B 弱档在此档可行 | ⚠️ |
| Apple Silicon Mac | 统一内存 16–128GB,常开低功耗 | MLX-LM (vLLM/SGLang 无 Metal 支持);或 Ollama/LM Studio | 📋 二期 |
| NVIDIA DGX Spark (GB10) | 128GB 统一内存,官方生态最全 | SGLang(官方 Spark 菜谱,nvfp4) / vLLM;2–4 台 RoCE 组网跑 300B MoE(spark-bench 实测) | 📋 |
| Perplexity Portable Computer | 硬件即 DGX Spark 128GB,预装 Perplexity 本地栈(编排器+Qwen3.8-27B,云端按审批升级) | 整机产品,不可自装;我们方向的**参照产品** | 📋 对标 |
| 懒猫 AI Pod (国产) | Jetson T4000/T5000,64/128GB,273GB/s,¥29,899 起,预装 Ubuntu+CUDA | vLLM-Jetson / Ollama / unsloth·llama.cpp(GGUF) | 📋 |
| 纯 CPU 旧机器 | 无 GPU | 只装 Pi 当终端;或 llama.cpp ≤2B | 📋 |
| 云端 API | 无限 | 逃生舱;key 只存网关 (homed/failover) | ✅ |

同为 128GB 级,三台的区别:DGX Spark 是生态标杆(引擎官方直接出菜谱),
Perplexity 是「买来即用的成品」,AI Pod 是价格更低的国产替代——
在我们的调度里都是「大内存车道」的候选宿主,preset 各配一份即可。

## 2. 模型:按 Switchyard 双档分

Switchyard 的世界里只有两个槽位:**weak(efficient,走量)** 和 **strong(专家,破局)**,
外加一个 judge。模型进哪个槽是配置,不是模型属性——同一张表按槽位整理:

### 2.1 弱档候选(efficient —— 承接约八成流量,要快要便宜要常开)

| 模型 | 参数 | 4-bit 内存 | 宿主 | 状态 |
|---|---|---|---|---|
| **Qwen3-1.7B-FP8**(现役) | 1.7B 稠密 | ~2GB | 任意;24GB 卡与 32B 共存(util 0.11) | ✅ |
| Qwen3-4B-AWQ | 4B 稠密 | 运行时 3.2GiB(fp16 词表层) | 需 32GB+ 卡才能共存;24GB 实测装不下 | ✅(排除) |
| Qwen3-8B-AWQ | 8B 稠密 | ~6GB | 大内存主机/工作站卡上当弱档 | ⚠️ |
| 出厂预置(云端,参考) | Kimi K2.6 / Gemini 3.5 Flash / MiniMax M2.7 / **Nemotron-3-Super-120B-A12B** | — | OpenRouter | 官方 preset |

注:NVIDIA 把自家 Nemotron 放在弱档卖——弱档承接大部分流量,才是走量的位置。
judge 与弱档同宿主(裁决要快要便宜),现役 judge = 1.7B。

### 2.2 强档候选(strong —— 破局用,升级是单程票)

| 模型 | 参数 | 4-bit 内存 | 宿主 | 状态 |
|---|---|---|---|---|
| **Qwen3-32B-AWQ**(现役) | 32.8B 稠密 | 19GB(运行时 17.7GiB+开销) | 24GB 独显 | ✅ |
| Qwen3.8-27B | 27.8B 稠密,多模态,262K ctx | ~16GB(待量化) | 24GB 独显的下一代强档 | ⏳ |
| Qwen3-14B-AWQ | 14B 稠密 | ~9GB | 12/16GB 小卡上的强档 | ⚠️ |
| GLM-5.3-Flash | 321B/18B MoE,1M ctx | 1bit 93GB~4bit 200GB | 128GB 单机(2-3bit)或 4 台组网(4bit) | 📋 preset 已留 |
| DeepSeek-V4-Flash(/Vision) | 291B/305B MoE,原生视觉 | nvfp4 ~150GB | 2× DGX Spark(SGLang 官方菜谱) | 📋 |
| 云端强档 | Claude Opus 4.7/4.6、GPT-5.5/5.2(官方 preset)、Qwen3-235B(Together) | — | 云 API,作我们的兜底层 | ✅ 路由已通 |

强弱是相对部署档位的:14B 在小卡上是强档,在 24GB 卡上让位给 32B。
排除项:GLM-4-9B(老一代,被 Qwen3 系压制)、DeepSeek-V3(671B,数据中心级)。

## 3. 任务分配:按 Switchyard 的双档流转逻辑

以下取自其内置 escalation judge 提示词与源码(escalation_judge_request_processor.py),
不是我们自创的分法。

### 3.1 弱档怎么走(默认路径)

**每个新会话一律从弱档起步**——不预判难度,先干活。官方定义弱档自己能消化的:

- 常规编码、文件探索、单文件修改
- 常规调试、依赖与环境安装、多数重构
- 流程性/机械性摩擦:装工具、起服务、照菜谱执行、局部单文件测试修复

以下「正常摩擦」明确**不触发升级**:TDD 先红后绿、报错下一轮就修掉、
会话早期探索死胡同、缺工具时自适应换替代、顺序尝试不同方案
(换方案是适应;同方案原样重试才算循环)。

### 3.2 强档怎么走(升级路径)

judge 从 `min_judge_turn` 轮起,每轮读会话压缩视图(任务框架+最近几轮),
只回答一个问题:**这个会话的卡点是否超出弱档能力**。三类卡点该升:

1. **跨模块/跨代码库综合**——修复需要从别处学会约定/契约/不变量并一致应用
2. **隐蔽不变量**——貌似合理的修复反复挂同一个测试
3. **跨模块根因、多步算法/形式化推理**——弱档一直"差一点点"

四类「轨迹病症」是升级信号:**重复循环**(同命令失败 2+ 次)、**假进展**
(证据失败却宣称成功/跳过指定验证)、**跑偏死路**(违反显式约束/凭想象改代码/
多轮无耐久产出)、**绝望动作**(宣布不可能、rm -rf 式挣扎)。

**谁都不升**(官方明确,升了纯浪费):外部资源缺失——文件不存在、服务永久坏死、
环境与需求矛盾。强模型变不出缺失的资源。

### 3.3 分配逻辑全流程(源码顺序)

```
请求进来
 1 显式指定 model=small/large/cloud ?  → 直通,永不二次猜测
 2 会话已被升级过(黏性表命中)?        → 直接强档,judge 不再介入(单程票)
 3 默认盖「弱档」章
 4 轮次 < min_judge_turn ?            → 弱档出答,不审
 5 judge 审轨迹 → {escalate, reason}
 6 连续 escalate 达 confirmations 次?  → 会话写入黏性表,本轮起走强档
   (中间任何一次"不升"即清零计数)
 7 judge 自身故障 → fail-open 留弱档,并计入统计(裁判宕机不烧强档的钱)
 8 会话被黏性表 LRU 挤出 → fallback_target_on_evict(我们配 strong,宁贵勿错)
```

云端不在 Switchyard 档位设计内,是我们 failover 组件补的第三层:
本地车道全灭时 watchdog 秒级把路由切向云 API(配 key 零中断)。

### 3.4 档位 → 硬件落位(我们的映射)

| 槽位 | 落在哪 |
|---|---|
| weak + judge | 常开设备:Mac / AI Pod / 独显小分片(现役:4090 上 util 0.11 的 1.7B) |
| strong | 最强在线节点:24GB 独显 32B(现役);128GB 主机就位后,长上下文任务落 300B MoE |
| 兜底 | 云端 API(watchdog 自动切换) |

## 相关生态(2026-09 快照)

- NVIDIA Personal AI Router:官方局域网多设备聚合(按空闲分发)。验证品类;
  我们的差异在能力分级×成本阶梯×judge 兜底×企业功能
- spark-bench:4×DGX Spark RoCE 组网跑 GLM-5.3-Flash 的社区实测
- unsloth:转型 local inference,GGUF 动态量化(1bit 起)单机跑 300B MoE
- Wafer:推理内核优化平台(任意硬件 1.5–5x);Wafer Pass 可作云端供应商
