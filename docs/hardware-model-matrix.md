# 硬件×模型×任务 分工矩阵

> 2026-09 梳理。三条心法:**内存看总参数、速度看激活参数、稠密吃带宽/MoE 吃容量**。
> 状态标注: ✅ 真机实测 / ⚠️ 按实测外推 / 📋 调研或设计完成 / ⏳ 等上游

## 1. 硬件 → 推理系统

| 硬件 | 关键特征 | 该跑的系统 | 状态 |
|---|---|---|---|
| 消费独显 24GB (4090/4090D/3090) | ~1TB/s 高带宽 | vLLM (AWQ + FP8 KV;参数见 homed/inference/presets/qwen3-24gb.env) 或 SGLang | ✅ |
| 消费独显 12–16GB (4070/4080) | 高带宽小容量 | vLLM,模型降档 (config/profiles/) | ⚠️ |
| 工作站 32–48GB (5090/A6000) | 高带宽大容量 | vLLM/SGLang,可开 CUDA graph;4B 前台在此档可行 | ⚠️ |
| Apple Silicon Mac | 统一内存,常开低功耗 | MLX-LM (vLLM/SGLang 无 Metal 支持);或 Ollama/LM Studio | 📋 二期 |
| 大统一内存小主机 (DGX Spark GB10 / 懒猫 AI Pod 64·128GB, 273GB/s) | 容量大带宽低 | vLLM-Jetson / SGLang(官方 Spark 菜谱) / unsloth·llama.cpp;nvfp4 为未来首选;2–4 台 RoCE 组网 | 📋 |
| 纯 CPU 旧机器 | 无 GPU | 只装 Pi 当终端;或 llama.cpp ≤2B | 📋 |
| 边缘 (ESP32-S3 / RK3588) | MCU / NPU | ESP32 只做语音客户端接家庭网关;RK3588 用 RKLLM 跑 2–4B int8 | 机器人线 |
| 云端 API | 无限 | 逃生舱;key 只存网关 (homed/failover) | ✅ |

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

## 3. 任务 → 模型 → 硬件

| 任务 | 模型 | 硬件 | 理由 |
|---|---|---|---|
| 高频简单活(翻译/总结/分类/提取) | 1.7B–4B 稠密 | 常开设备(Mac/AI Pod/独显小分片) | 量大要快要便宜 |
| 硬活(分析/评审/数学/根因) | 32B 稠密 | 24GB 独显 | 稠密配高带宽 |
| Agent 多步(pi) | 32B+工具解析 | 同上,escalation 阶梯 | 工具编排小模型干不动(实测 judge 会升级) |
| 长文档/大代码库/多模态 | 300B 级 MoE | 128GB 统一内存主机 | MoE 要容量;262K–1M ctx 只有这档给得起 |
| judge 裁决 | 1.7B | 与前台同宿主 | 快而便宜 |
| 超纲/本地全灭 | 云端 frontier | API | watchdog 自动切 |
| 语音机器人 | 不本地跑 | ESP32 → 家庭网关 | 边缘只做耳嘴,脑在网关 |

**组合原则**:常开设备当前台+大脑(网关/小模型/judge),高带宽独显当专家
(开关机=成本开关,watchdog 天然支持),大内存主机当图书馆(长上下文/多模态),
云端当保险。gen_routes 把这张表编译成 Switchyard 路由。

## 相关生态(2026-09 快照)

- NVIDIA Personal AI Router:官方的局域网多设备聚合(RTX/DGX/Mac→单端点,按空闲分发)。
  验证品类;我们的差异在能力分级×成本阶梯×judge 兜底×企业功能
- spark-bench:4×DGX Spark RoCE 组网跑 GLM-5.3-Flash 的社区实测
- unsloth:转型 local inference,GGUF 动态量化(1bit 起)单机跑 300B MoE
- Wafer:推理内核优化平台(任意硬件 1.5–5x),在我们 inference 层之下;Wafer Pass 可作云端供应商
- 懒猫 AI Pod:Jetson T4000/T5000,64/128GB,¥29,899 起,国产大内存小主机代表
