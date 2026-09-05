# router — 分诊路由（Switchyard 配置生成）

上游选型 [NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard)（零改动）。
本组件是 `gen_routes.sh`：探测车道健康 → 生成 `routes.generated.yaml` →
把当前模式写进 `logs/cluster_mode`。

- 全健康 → `auto` = escalation_router（小模型先答，judge 审，超纲升级 32B）
- 有车道挂 → 降级路由：配了 `TOGETHER_API_KEY` 指云端，否则指幸存车道

输入：车道健康(HTTP :8001/:8002)、`logs/lanes.env`（inference 落盘的车道名）、
`.env`（云端 key）。输出：yaml + mode 文件。谁重启 switchyard 谁负责
（run_cluster 或 failover），本组件只生成不执行。
