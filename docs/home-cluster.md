# AI 集群（homed）技术设计与第一阶段实现

> 目标：家里的若干台异构设备，各自一键安装，装完在任意终端得到一个
> 像 Claude Code 一样的 agent，指令默认跑集群算力，跑不动的自动走云 API。
> 用户不用自己组装任何东西。

## 1. 分层：全部用现成件，只造胶水

| 层 | 选型 | 我们做什么 |
|---|---|---|
| Harness | [Pi](https://pi.dev/)（极简 agent harness） | 零改动，只写 provider 配置 |
| Router | [NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard) | 零改动，生成 routing-profile 配置 |
| Inference | vLLM（NVIDIA 卡）/ SGLang（备选）/ MLX-LM（Mac，规划） | 按硬件探测选型 + 实测显存档位 |
| 云端逃生舱 | Together AI / 任意 OpenAI 兼容 API | key 只存网关，审批 + 预算（规划） |
| **胶水（homed）** | **本仓库** | 安装器、硬件探测、配置生成、注册/心跳（多机，规划） |

```
pi (任意设备的终端)
   │  OpenAI / Anthropic 协议(Switchyard 三种入站全支持)
Switchyard :4000 ── 全家唯一入口, model: auto|small|large|cloud
   ├── vLLM :8001  大车道 (Qwen3-32B-AWQ, 24GB 档实测参数)
   ├── vLLM :8002  小车道 (Qwen3-1.7B-FP8)
   └── 云端 (Together; 未配 key 时用本地大车道假冒,路由逻辑照常可测)
```

`auto` 路由 = Switchyard `escalation_router`：先小模型答，judge（小模型自兼，
`disable_reasoning`）审答案，发现问题升级大车道；会话被逐出时回落 strong。

## 2. API（全部表面积就这么大）

```
POST http://127.0.0.1:4000/v1/chat/completions
  model: auto | small | large | cloud     # 四个词,完事
GET  /v1/models          # 目录
GET  /v1/routing/stats   # 路由统计/成本
```

Anthropic Messages 格式同样可用（`--inbound both`），Claude 系工具可直连。

## 3. 第一阶段（单机版）已实现并真机验证

2026-09-05 于 AutoDL RTX 4090D 24GB 端到端验证：

- `homed/run_cluster.sh`：一条命令拉起 双 vLLM + Switchyard + pi 配置，幂等
  （车道健康则跳过重启）；`stop` 子命令全停
- `homed/test.sh small|large|router|pi|all`：每层独立可测
- 验证过的链路：四车道真实回答；`auto` 先小后判；**pi 通过 auto 路由驱动
  本地模型完成真实工具调用（写文件成功）**
- 关键 vLLM 参数（缺一 pi 就跑不通）：
  - `--enable-auto-tool-choice --tool-call-parser hermes`——pi 是带工具的
    agent，没有它 vLLM 直接 400
  - `--default-chat-template-kwargs '{"enable_thinking": false}'`——Qwen3
    思考模式默认开，会把 max_tokens 烧光导致 content 为空
  - `--reasoning-parser qwen3`——显式开思考的请求 `<think>` 不混进正文
  - 显存分片沿用 2026-09-04 实测档位（32B@6144 util 0.83 + 1.7B@8192 util 0.11）

已知限制（诚实版）：Qwen3.8-27B 尚无 4-bit 量化（bf16 56GB / FP8 28GB 均超
24GB，需 32GB+ 卡），出了改 `run_cluster.sh` 两个变量即换；escalation 的
升级行为按会话生效，深度调优（confirmations、judge prompt）待做；
单机版无多设备注册/发现。

## 4. 第二阶段（多机）设计要点

- **homed 守护进程**：每台设备探测硬件（NVIDIA→vLLM/SGLang，Apple→MLX-LM，
  弱设备只装 pi）→ 选档位 → 拉起推理 → mDNS 找网关注册 → 心跳；
  第一台安装的设备自任网关，后续设备一行 join 命令加入
- **动态模型池**：注册/下线改写 Switchyard 配置并热重载（`--reload`）；
  笔记本合盖即摘除
- **云端治理**：交互任务弹审批（Allow/Deny），批量任务走预算上限；
  key 永不下发到节点
- **账本**：`/v1/routing/stats` 已给基础；按会话聚合沿用 Tandem 协议
  （docs/agent-interface.md）的 session 思路
- 网关单点：v1 接受（网关关机全家降级为"本机模型或云端直连"）

## 5. 测试指南

服务器上（组件级）：

```bash
./homed/test.sh small    # 只测小模型 (vLLM :8002 直连)
./homed/test.sh large    # 只测大模型 (vLLM :8001 直连)
./homed/test.sh router   # 只测 Switchyard (四车道 + 统计)
./homed/test.sh pi       # 只测 harness (含真实工具调用)
./homed/test.sh all      # 整体
```

从自己电脑测（把网关端口拉到本地）：

```bash
ssh -p <端口> -L 4000:127.0.0.1:4000 root@<主机> -N &
curl http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"你好"}]}'
```

交互式用 pi：SSH 进服务器直接敲 `pi`（`/model` 可切 auto/small/large/cloud）。
