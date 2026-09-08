# 仓库结构与设计文档的对应

规则只有三条：**目录名等于设计文档里的层**；**可变状态只放 `state/`，进程产物只放
`logs/`，两者不入库**；**仓库里只有当前设计需要的部件**。

## 两套系统

| 系统 | 目录 | 装在哪 | 入口 |
|---|---|---|---|
| 模型端 | `cluster/` | 集群里的每台设备（GPU 机、Mac、大内存主机） | `bin/cluster init / join / link-gpu` |
| 个人端 | `client/` | 每个人自己的机器，一人一套 | `bin/client setup / web` |

两者互不 import。唯一契约是网关地址（`http://<Hub>:4000`，OpenAI 格式）。
这与 Claude Code 的形态一致：harness 在用户这边，模型在另一边。

## 模型端：`cluster/`（对应 design.md 第一部分的四层）

| 路径 | 设计文档 | 内容 | 为什么放这里 |
|---|---|---|---|
| `cluster/access/` | 第三部分 §2.3 | `console/` 集群管理台与节点登记接口 | 登记接口放在管理台进程里，是因为它是 Hub 上唯一常驻的 HTTP 服务，不值得为此再起一个 |
| `cluster/router/` | 第一部分 §2 调度层；第三部分 §3 | `routes.py` 生成 Switchyard 路由表；`gateway.py` 管网关进程 | 调度层本身是外部件（Switchyard），我们只拥有「配置它」和「起停它」两件事，所以这个目录只有这两个文件 |
| `cluster/node/` | 第一部分 §3 硬件与池；第三部分 §4 | `probe.py` 硬件探测与定池规则；`presets.py` 读 preset；`engine.py` 起 vLLM / llama.cpp | 一台设备成为节点需要的全部逻辑。定池规则和引擎启动放同一目录，因为它们都只在节点本机执行 |
| `cluster/inference/presets/` | 第三部分 §4.2–4.4、§5 | 每个「模型家族 × 硬件档位」一个 `.env`：模型路径、对外名、端口、引擎参数 | 校准数据是资产，独立成文件便于逐个审阅和替换；`node/presets.py` 只负责解析 |
| `cluster/control/` | 第一部分 §4 热插拔、§6 故障处理；第三部分 §6 | `registry.py` 注册表；`health.py`；`watchdog.py` 循环；`heal.py` 分段重启 | 控制平面不在数据路径上，独立成包，与三层互不 import |
| `cluster/util/` | — | `procs.py`：拉起进程、pid 文件、HTTP 健康等待 | 各层共用的机械操作，无业务含义 |
| `bin/install.sh` | 第三部分 §1.3 | 装依赖：服务 venv 与 Switchyard、CPU 机的 llama.cpp 与 1.7B 模型；GPU 机打印 vLLM 步骤 | 每台设备跑一次，幂等；之后才是 `cluster init/join` |
| `cluster/cli.py`、`bin/cluster` | 第三部分 §6.3 | `init / join / link-gpu / status / stop / regen` | 只做编排，不含策略；`bin/cluster` 是 `python3 -m cluster` 的一行包装 |
| `cluster/test.sh`、`cluster/ask.sh` | 第三部分 §6.3、§8 | HTTP 冒烟测试；一行派活 | 走公开接口，不依赖包内部 |
| `tests/` | — | 控制平面与路由生成的单元测试，无 GPU 可跑 | 纯函数（`routes.render`、`health.mode`、`probe.choose_preset`）的测试 |

## 个人端：`client/`（对应 design.md 第三部分 §2）

| 路径 | 设计文档 | 内容 | 为什么放这里 |
|---|---|---|---|
| `client/pi/setup.py` | 第三部分 §2.1–2.2 | 写 `~/.pi/agent/models.json`（provider `home` → 网关）、`settings.json`（默认车道、压缩阈值），同步扩展 | Pi 本身不改；接线只是三个文件 |
| `client/pi/extensions/` | 第三部分 §2.1 | Pi 扩展（`*.ts`），每个内部系统一个文件 | 「往 Pi 里加能力」的唯一位置，不改 `setup.py` |
| `client/web/index.html` | 第三部分 §2.3 | 个人网页前端：对话、车道选择、每条回答标注执行模型与延迟 | 只有聊天，没有集群管理；管理在 `cluster/access/console/` |
| `client/serve.py` | 第三部分 §2.3 | 本机小服务（标准库）：托管网页，把 `/v1/*` 代理到网关 | 员工机器上除 Python 外零依赖；代理是为了免网关 CORS |
| `client/cli.py`、`bin/client` | 第三部分 §2.1 | `setup --hub` / `web --hub` / `status` | 一人一套的全部操作 |
| `bin/install-client.sh` | 第三部分 §1.3 | 装 Pi（npm），然后 `client setup` | 员工机器跑一次 |

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
