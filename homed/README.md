# homed — 家庭 AI 集群的组件

每个目录一个组件。组件之间**只通过 HTTP 和文件通信**，互不 import，
单独看、单独换、单独测：

| 组件 | 职责 | 对外接口 | 依赖 |
|---|---|---|---|
| [harness/](harness/) | 用户入口（Pi 接线） | `pi` 命令 | router 的 :4000 |
| [router/](router/) | 分诊路由配置生成 | `routes.generated.yaml` + `logs/cluster_mode` | 车道健康探测(HTTP)、`logs/lanes.env` |
| [inference/](inference/) | 本地推理车道 | vLLM :8001 / :8002 (OpenAI 兼容) | preset 文件 |
| [failover/](failover/) | 云端兜底 + 自愈 | 改写 router 产物并重启 switchyard | router、run_cluster.sh |
| [console/](console/) | Web Surface | :6006 | router 的 :4000、`logs/cluster_mode` |

编排入口只有一个：`./homed/run_cluster.sh`（`stop` 全停）。
测试：`./homed/test.sh [small|large|router|pi|console|failover|all]`。
派活：`./homed/ask.sh <auto|small|large|cloud> "任务"`。

```
pi / 浏览器(:6006) / 任意 OpenAI 客户端
        │
   Switchyard :4000  ←─ router 生成配置;failover 在异常时改写它
    ├── vLLM :8001 大车道(专家)
    ├── vLLM :8002 小车道(前台)
    └── 云端 API(兜底,配 TOGETHER_API_KEY 启用)
```
