# 仓库结构与设计文档的对应

规则只有三条：**目录名等于设计文档里的层**；**可变状态只放 `state/`，进程产物只放
`logs/`，两者不入库**；**仓库里只有当前设计需要的部件**。

## 现役代码：`homed/`（对应 design.md 第一部分的四层）

| 路径 | 设计文档 | 内容 | 为什么放这里 |
|---|---|---|---|
| `homed/access/` | 第一部分 §2 接入层；第三部分 §2 | `pi.py` 生成 Pi 的 provider 配置；`console/` 网页控制台与节点登记接口 | 所有「用户或设备进入系统」的东西在一起。登记接口放在控制台进程里，是因为它是 Hub 上唯一常驻的 HTTP 服务，不值得为此再起一个 |
| `homed/router/` | 第一部分 §2 调度层；第三部分 §3 | `routes.py` 生成 Switchyard 路由表；`gateway.py` 管网关进程 | 调度层本身是外部件（Switchyard），我们只拥有「配置它」和「起停它」两件事，所以这个目录只有这两个文件 |
| `homed/node/` | 第一部分 §3 硬件与池；第三部分 §4 | `probe.py` 硬件探测与定池规则；`presets.py` 读 preset；`engine.py` 起 vLLM / llama.cpp | 一台设备成为节点需要的全部逻辑。定池规则和引擎启动放同一目录，因为它们都只在节点本机执行 |
| `homed/inference/presets/` | 第三部分 §4.2–4.4、§5 | 每个「模型家族 × 硬件档位」一个 `.env`：模型路径、对外名、端口、引擎参数 | 校准数据是资产，独立成文件便于逐个审阅和替换；`node/presets.py` 只负责解析 |
| `homed/control/` | 第一部分 §4 热插拔、§6 故障处理；第三部分 §6 | `registry.py` 注册表；`health.py`；`watchdog.py` 循环；`heal.py` 分段重启 | 控制平面不在数据路径上，独立成包，与三层互不 import |
| `homed/util/` | — | `procs.py`：拉起进程、pid 文件、HTTP 健康等待 | 各层共用的机械操作，无业务含义 |
| `bin/install.sh` | 第三部分 §1.3 | 装依赖：服务 venv 与 Switchyard、Pi、CPU 机的 llama.cpp 与 1.7B 模型；GPU 机打印 vLLM 步骤 | 每台设备跑一次，幂等；之后才是 `homed init/join` |
| `homed/access/extensions/` | 第三部分 §2.1 | Pi 扩展（`*.ts`），每个内部系统一个文件；`init` 时同步到 `~/.pi/agent/extensions/` | 「往 Pi 里加能力」的唯一位置，不改 `pi.py` |
| `homed/cli.py`、`bin/homed` | 第三部分 §6.3 | `init / join / link-gpu / status / stop / regen` | 只做编排，不含策略；`bin/homed` 是 `python3 -m homed` 的一行包装 |
| `homed/test.sh`、`homed/ask.sh` | 第三部分 §6.3、§8 | HTTP 冒烟测试；一行派活 | 走公开接口，不依赖包内部 |
| `tests/` | — | 控制平面与路由生成的单元测试，无 GPU 可跑 | 纯函数（`routes.render`、`health.mode`、`probe.choose_preset`）的测试 |

## 运行时目录（不入库）

| 路径 | 内容 | 谁写 |
|---|---|---|
| `state/` | `nodes.json` 注册表、`routes.yaml` 路由表、`cluster_mode` 模式字、`join_token`、`preset` | 控制平面与 CLI；是第三部分 §6.1「文件契约」的全部载体 |
| `logs/` | 各进程日志与 pid | `util/procs.py` |
| `~/.homed/` | 模型权重、服务 venv、本机编译的 llama.cpp | 安装步骤 |

`state/` 与 `logs/` 分开，是因为前者是**接口**（另一个模块会读），后者只是**产物**。

## 文档：`docs/`

| 路径 | 用途 |
|---|---|
| `docs/design.md` | 主文档：设计 / 接口契约 / 实现与运维 |
| `docs/hardware-model-matrix.md` | 产品页：硬件清单、配置、联动、任务 |
| `docs/roadmap.md` | 路线图 |
| `docs/repo-layout.md` | 本文 |

## CI：`.github/workflows/ci.yml`

跑 `tests/`（控制平面与路由生成的单元测试）。
