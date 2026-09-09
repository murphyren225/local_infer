# client — 个人端（每人一套）

和 `cluster/`（模型端）是两套系统，互不 import。两者之间唯一的契约是模型端的
网关地址：OpenAI 格式的 HTTP。

装（一次）：

```
bin/install-client.sh http://<Hub>:4000     # 装 Pi(不需要 sudo)、接线、拉起本机服务
```

用：

```
bin/client pi                                # Pi harness:在本机执行工具,模型在集群上
open http://127.0.0.1:7000                   # 个人网页:Chat(只推理) / Agent(本机 Pi 干活)
bin/client ask "翻译成英文:今天很忙" --lane small   # 一次推理,不经 harness
bin/client batch prompts.txt -c 4 -o out.jsonl     # 一批推理任务并发提交
bin/client status | up | down                # 本机服务
```

| 文件 | 内容 |
|---|---|
| `pi/setup.py` | 把 Pi 接到网关：写 `~/.pi/agent/models.json`、`settings.json`，同步扩展 |
| `pi/extensions/` | 我们的工具扩展（`*.ts`），一个内部系统一个文件；`setup` 时复制到 Pi 的扩展目录 |
| `gateway.py` | 个人端对模型端的全部认识：一次 chat、列路由、读提示词文件（标准库） |
| `serve.py` | 本机服务（仅 127.0.0.1）：托管网页、把 `/v1/*` 代理到网关、`POST /api/agent` 在本机跑一次 Pi 任务 |
| `web/index.html` | 个人网页：Chat / Agent 两个页签，车道选择，每条回答标注执行模型与延迟 |
| `cli.py` | 上面那些命令；hub 地址记在 `~/.cluster-client/config.json` |

两种运行位置（与 Claude Code 同构）：装在员工本机可操作本机文件；托管在 Hub 上
则零安装、操作共享资源。当前实现是前者；后者在路线图。
