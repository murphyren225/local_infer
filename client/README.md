# client — Pi 端（每人一套）

和 `cluster/`（推理端）是两套系统，互不 import。两者之间唯一的契约是网关地址：
OpenAI 格式的 HTTP。

| 目录 | 内容 |
|---|---|
| `pi/setup.py` | 把 Pi 接到网关：写 `~/.pi/agent/models.json`、`settings.json`，同步扩展 |
| `pi/extensions/` | 我们的工具扩展（`*.ts`），一个内部系统一个文件；`setup` 时复制到 Pi 的扩展目录 |
| `web/index.html` | 个人网页前端：聊天 + 车道选择 + 延迟标注 |
| `serve.py` | 本机小服务（仅标准库）：托管网页、把 `/v1/*` 代理到网关 |
| `cli.py` | `setup` / `web` / `status` |

用法（员工机器）：

```
bin/client setup --hub http://<hub>:4000      # 一次；之后直接敲 pi
bin/client web   --hub http://<hub>:4000      # 个人网页 http://127.0.0.1:7000
```

两种运行位置（与 Claude Code 同构）：装在员工本机可操作本机文件；托管在 Hub 上
则零安装、操作共享资源。当前实现是前者；后者见 docs/client.md。
