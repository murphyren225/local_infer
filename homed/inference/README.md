# inference — 本地推理车道

vLLM 双实例共享一张卡，参数全部来自 `presets/<名字>.env`。

**preset = 换模型的全部工作量**：显存分片（真机实测）+ 该模型家族专属的
三样 vLLM 参数——reasoning parser、工具调用解析器（pi 这类 agent 没它直接 400）、
思考开关（Qwen3 不关思考会把 max_tokens 烧光）。

| preset | 状态 |
|---|---|
| `qwen3-24gb` | ✅ RTX 4090D 真机校准（32B-AWQ@5120 + 1.7B-FP8@8192，余量 1.1GiB） |
| `glm-5.3-flash` | 📋 预留：320B/18B MoE，最小量化 ~93GB，需 DGX Spark/128GB 级设备 |

切换：`INFERENCE_PRESET=<名字> ./homed/run_cluster.sh`。
新模型接入 = 复制一份 preset 改参数，其他组件零改动。

已知约束（24GB 档）：32B 冷启动装载峰值 ~20.5GiB 需要整卡空闲，
所以启动顺序必须大→小（run_cluster 和 failover/heal.sh 都遵守这一点）。
