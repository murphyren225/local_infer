# 链路逐环节手工测试

拓扑见 `pipeline-design.md` §9。一条请求的路：

```
本机服务 :7000 → Switchyard :4000 → 决策器 :4100 → PAIR LM 槽位代理 :11234 → {Mac llama-server, 4090 SGLang}
```

Mac 是调度机。4090 通过 SSH 隧道接到 Mac 的 127.0.0.1 上，PAIR 把它当作一个手动节点。
两台机器各跑**一个同名模型** `qwen3-1.7b`（Qwen3-1.7B）：4090 上是 SGLang，Mac 上是 llama-server，
都放在 PAIR 的 LM 槽位里，所以在同一份名单上，PAIR 在两台之间按空闲选硬件。Switchyard 的弱池和强池
指向同一个模型，升级只在 RL 轨迹里可见；第三台机器进来再放别的模型。
下面七个环节从底往上，每一环只依赖它下面的环。**哪一环第一个失败，问题就在那一段**，上面的失败都是它引起的。

所有命令在 Mac 上跑，不依赖当前目录。`tests/manual.sh` 把它们串起来自动跑；`tests/links.py` 是同一套检查的自动化版本。

## 0. 前提：隧道

4090 上的端口只监听在 4090 本机。Mac 上凡是 1234、14318 这几个端口，都是 ssh 隧道开的入口，请求会被原样转到 4090 的对应端口。隧道断了，这些端口就没人听，curl 什么都不打印。

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:1234/v1/models
```

200：隧道在。000：隧道没了，拉起来，等十秒再查：

```bash
nohup /Users/murphyren/Desktop/local_infer/bin/tunnel-4090 > /Users/murphyren/Desktop/local_infer/logs/tunnel.log 2>&1 &
```

隧道刚恢复的头二十秒，PAIR 要重新探测一遍 4090，环节 2、3 可能暂时看不到 4090 的模型。

| Mac 上的端口 | 实际是谁 |
|---|---|
| 1234 | 4090 的 SGLang（隧道） |
| 14318 | 4090 的 PAIR 节点信息（隧道） |
| 11235 | Mac 自己的 llama-server，PAIR 在 LM 槽位里拉起的 |
| 11234 | Mac 上 PAIR 的 LM 槽位代理，两台机器的名单都在这 |
| 4100 | 决策器 |
| 4000 | Switchyard 网关 |
| 7000 | 个人端本机服务 |

## 环节 1：引擎直连

**是什么。** 两个真正做推理的进程，各加载同一个模型 Qwen3-1.7B：4090 上的 SGLang（GPU，bf16 权重），Mac 上的 llama-server（CPU，GGUF 量化权重）。两个都是 OpenAI 兼容的 HTTP 服务，都把模型报成 `qwen3-1.7b`。名字必须一样，Switchyard 和 PAIR 都按名字找模型，差一个字符就是两个模型。

**为什么先测它。** 绕开所有路由和调度直接打引擎。这里不通，上面每一环都会跟着失败，但原因和调度无关：模型没加载、显存不够、进程挂了、隧道断了。

4090 的 SGLang，问它加载了什么：

```bash
curl -s http://127.0.0.1:1234/v1/models
```

正常：`"id":"qwen3-1.7b"`，`"owned_by":"sglang"`。

4090 的 SGLang，让它说一句话：

```bash
curl -s http://127.0.0.1:1234/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-1.7b","messages":[{"role":"user","content":"Reply with exactly: sglang-ok"}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"content":"sglang-ok"`。`enable_thinking=false` 关掉 Qwen3 的思考模式，不关的话 20 个 token 全花在思考上，content 是空的。

Mac 的 llama-server，问它加载了什么：

```bash
curl -s http://127.0.0.1:11235/v1/models
```

正常：`data` 里有 `"id":"qwen3-1.7b"`。这个进程是 PAIR 的引擎管理器按 manifest 拉起来的，二进制和模型文件在 PAIR 引擎目录里的两个符号链接：`engine-bin/lmstudio/llama-server` 和 `model.gguf`。

Mac 的 llama-server，让它说一句话：

```bash
curl -s http://127.0.0.1:11235/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-1.7b","messages":[{"role":"user","content":"Reply with exactly: mac-ok"}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"content":"mac-ok"`，CPU 推理，小模型不到一秒。

**挂了怎么读。** 前两条没输出：隧道断了。有输出但 500：SGLang 进程有问题，去 4090 看 PAIR 引擎日志。后两条没输出：Mac 上 PAIR 桌面端没在跑，或 LM 槽位没被启用（看环节 2 的 `engine-state.json`）。

## 环节 2：PAIR 看见两台机器

**是什么。** PAIR 在 Mac 上维护"哪个节点上有哪个模型"的目录。它按引擎槽位分名单，我们把两台机器的引擎都放在 LM 槽位：Mac 自己的 llama-server 由本机引擎管理器汇报，4090 的 SGLang 由手动节点探测（每隔几秒探 4090 的 1234 和 14318）拿到。两台就在同一份名单上，名单对外的形式是 LM 槽位代理的 `/v1/models`。

**为什么测它。** 环节 3 是在这份名单里挑节点。名单里少了谁，谁就永远不会被选。

LM 槽位代理的清单：

```bash
curl -s http://127.0.0.1:11234/v1/models
```

正常：`qwen3-1.7b`。清单按模型名去重，两台机器同名所以只有一条。要看两台是否都在，用环节 3 的分流结果。

手动节点的持久化配置，桌面端每次启动读它重新加节点：

```bash
cat ~/Library/Application\ Support/Nvidia\ Corporation/Personal\ AI\ Router/configs/manual-nodes.json
```

正常：`[{"id":"autodl-4090","address":"127.0.0.1","name":"autodl-4090"}]`。地址 127.0.0.1 不是笔误，PAIR 拨的是隧道入口。

Mac 本机引擎的启用状态，PAIR 引擎管理器按它决定开机自启哪个引擎：

```bash
cat ~/Library/Application\ Support/Nvidia\ Corporation/Personal\ AI\ Router/engine-bin/engine-state.json
```

正常：`"lmstudio": true`，`"ollama": false`。一台机器一个模型，Ollama 关掉了。

**挂了怎么读。** 环节 1 通、这里清单是空的或 503：等二十秒再试，探测有周期；还不行看桌面端是否在跑。

## 环节 3：PAIR 选硬件

**是什么。** PAIR 代理收到请求，看 `model` 字段，在名单里找出持有该模型的节点，按每个节点在途请求数挑一台转过去，完成后在账本里记一笔：哪个模型、在哪台机器上跑的。这就是"选硬件"。

**为什么这么测。** 两台机器同一个模型，单发一条永远是空闲的本机接，看不出选择。八条并发进去，PAIR 会把一部分派给 4090。账本里两台都出现，就证明了选择发生在两台之间。

```bash
for i in 1 2 3 4 5 6 7 8; do curl -s -o /dev/null -w "%{http_code} " http://127.0.0.1:11234/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-1.7b","messages":[{"role":"user","content":"Say hi"}],"max_tokens":6,"chat_template_kwargs":{"enable_thinking":false}}' & done; wait; echo
```

正常：八个 200。

等六秒读账本，账本是异步落盘的，太早读会少几条：

```bash
python3 -c "
import json,os
p=os.path.expanduser('~/Library/Application Support/Nvidia Corporation/Personal AI Router/workloads-history.json')
me=json.load(open(os.path.dirname(p)+'/node-id.json'))['node_uuid']
ws=json.load(open(p)); ws=ws.get('workloads',ws) if isinstance(ws,dict) else ws
for w in ws[-8:]: print(w['model'], w['engine'], w['state'], 'on', 'this-mac' if w['scheduledOn']==me else 'autodl-4090')
"
```

正常：八行 `qwen3-1.7b lmstudio completed`，一部分 `this-mac` 一部分 `autodl-4090`，实测是四四开。`lmstudio` 是 PAIR 那个槽位的内部名字，实际跑的是 SGLang 和 llama-server。分配只按在途数量，不看两台快慢，这是 PAIR 的已知局限。桌面端窗口左边的 Jobs 列表显示的就是这份账本。

**挂了怎么读。** 有非 200：看是哪台慢，Mac 是 CPU。账本全是 this-mac：4090 那一刻被判不可达，多跑几次；持续如此看环节 2。

## 环节 4：决策器

**这一环是什么。** 一个独立的小服务。Switchyard 每一轮把会话状态发给它，它返回三档概率、置信度、任务类型。现在是规则后端：看关键词（prove、debug、root cause……）、看请求头给的任务类型、看长度。以后换 Jev 或者自训的 RL 策略，接口不变，Switchyard 不用改。

**为什么单独测。** 它是纯函数，输入状态输出概率，不碰任何引擎。把它和 Switchyard 分开测，能区分"决策错了"和"决策对了但路由没听"。

**测什么。** 活着；对明显该升级的状态给 strong 大头；对明显不该升级的给 weak 大头；概率和为 1；每次决定都写进日志。

存活：

```bash
curl -s http://127.0.0.1:4100/health
```

正常：`{"ok": true, "backend": "rules"}`。backend 告诉你现在挂的是哪个后端。

该升级的状态。`summary` 和 `first_user_text` 就是 Switchyard 会发过来的字段，这里手写一个：

```bash
curl -s http://127.0.0.1:4100/decide -H 'Content-Type: application/json' -d '{"session":"t","turn":1,"task_type":"code","summary":"prove this race condition fix","first_user_text":"prove this race condition fix"}'
```

正常：`"strong": 0.7`，`"task_type": "code"`，`"reason": "strong hint"`。reason 是给人看的，说明命中了哪条规则。

不该升级的状态，故意不带 `task_type`，看它自己猜：

```bash
curl -s http://127.0.0.1:4100/decide -H 'Content-Type: application/json' -d '{"session":"t","turn":1,"summary":"translate hello to French","first_user_text":"translate hello to French"}'
```

正常：`"weak": 0.85`，`"task_type": "edit"`（从 translate 猜出来的），`"reason": "default weak"`。

日志：

```bash
tail -1 /Users/murphyren/Desktop/local_infer/logs/decisions.jsonl
```

正常：最后一行就是刚才那条，`state` 和 `decision` 都在。这个文件加上环节 6 的 RL 轨迹，是以后训练路由器的数据。

**挂了怎么读。** health 没输出：`bin/cluster status` 看 decider 那行，没起来就 `bin/cluster init`。概率不对：改的是 `decider/service.py` 里的规则，和链路无关。

## 环节 5：Switchyard 模型路由

**这一环是什么。** Switchyard 是路由网关。它对外暴露几个路由名：`small`、`large` 是固定路由，直接映射到弱池、强池的目标；`auto` 是带判定的路由，环节 6 测。两个池的目标现在都是 PAIR 的 LM 槽位代理、同一个模型 `qwen3-1.7b`，这个映射是 `cluster init` 按 `.env` 生成的，写在 `state/routes.yaml`。

**为什么先测固定路由。** 固定路由没有判定逻辑，只有"名字到目标"的映射。它通了，说明 Switchyard 到 PAIR 到引擎这三跳是连着的，环节 6 再出问题就只剩判定这一个变量。

**测什么。** `small` 和 `large` 都能拿到回复，返回里的 `model` 字段是最终服务的引擎报的名字，两条都应是 `qwen3-1.7b`；哪台机器算的看账本。

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"small","messages":[{"role":"user","content":"Reply with exactly: small-ok"}],"max_tokens":20,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"model":"qwen3-1.7b"`，`"content":"small-ok"`。

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"large","messages":[{"role":"user","content":"Reply with exactly: large-ok"}],"max_tokens":20,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"model":"qwen3-1.7b"`，`"content":"large-ok"`。

再跑一次环节 3 的账本命令，最后两条应该都是 `qwen3-1.7b lmstudio`，落在哪台机器由 PAIR 决定。`lmstudio` 是 PAIR 里那个槽位的内部名字，实际跑的是 SGLang 或 llama-server。

两条请求都同时带了 `reasoning_effort` 和 `chat_template_kwargs`，因为事先不知道会落到哪个家族的引擎，各家认各家的，多余的会被忽略。

**挂了怎么读。** 502：Switchyard 到代理那一跳断了，看 `state/routes.yaml` 里的 `base_url` 是不是 11234。返回的 `model` 不是 `qwen3-1.7b`：`.env` 里 `PAIR_PROXY_URL` 指错了代理，改完 `bin/cluster regen`。

## 环节 6：Switchyard auto 路由

**这一环是什么。** `auto` 路由每一轮的流程：会话已锁定就直接强池；否则把会话摘要发给决策器；决策器说升级就计一次数，连续两次就把这个会话锁到强池，之后不再问；决策器挂了就留在弱池。每一轮的决策和实际服务的层写进 RL 轨迹。

**为什么这么测。** 要同时证明四件事：决策器真的被问了、请求头里的任务类型真的到了决策器、锁定逻辑按预期工作、轨迹写对了。所以用三轮对话，第一轮弱池，第二轮升级并锁定，第三轮直接强池不再问。

**两个坑。** Switchyard 的会话键是按第一条消息内容算的，同样的开头会复用已经锁定的会话，第一轮就直接强池，看起来像判定坏了。所以第一句带一个数字，会话 ID 也带同一个数字，每次重跑四处一起换。第二个坑是上下文要自己拼：每一轮把前面的问答都带上，中间的助手回复填 `ok` 就行，决策器看的是用户消息。

跑前记一下决策器被调用的累计次数：

```bash
curl -s http://127.0.0.1:4000/v1/routing/stats | python3 -c "import sys,json;print(json.load(sys.stdin)['classifier']['models']['decision-http']['calls'])"
```

第一轮，带 `x-task-type: code`：

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -H 'x-task-type: code' -H 'x-switchyard-session-id: manual-201' -d '{"model":"auto","messages":[{"role":"user","content":"[201] Debug this race condition in the connection pool and prove the fix is correct."}],"max_tokens":40,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}' | python3 -c "import sys,json;print(json.load(sys.stdin)['model'])"
```

正常：`qwen3-1.7b`。决策器给了 strong 0.7，但这是第一次，还没到两次确认，所以仍在弱池。

第二轮：

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -H 'x-task-type: code' -H 'x-switchyard-session-id: manual-201' -d '{"model":"auto","messages":[{"role":"user","content":"[201] Debug this race condition in the connection pool and prove the fix is correct."},{"role":"assistant","content":"ok"},{"role":"user","content":"It still fails under load. Root cause, please."}],"max_tokens":40,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}' | python3 -c "import sys,json;print(json.load(sys.stdin)['model'])"
```

正常：`qwen3-1.7b`。第二次确认，会话锁定强池，这一轮就走 4090 的 SGLang。

第三轮：

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -H 'x-task-type: code' -H 'x-switchyard-session-id: manual-201' -d '{"model":"auto","messages":[{"role":"user","content":"[201] Debug this race condition in the connection pool and prove the fix is correct."},{"role":"assistant","content":"ok"},{"role":"user","content":"It still fails under load. Root cause, please."},{"role":"assistant","content":"ok"},{"role":"user","content":"Now write the patch."}],"max_tokens":40,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}' | python3 -c "import sys,json;print(json.load(sys.stdin)['model'])"
```

正常：`qwen3-1.7b`。已锁定，不问决策器。

再跑一次上面的 stats 命令。正常：比跑前正好多 2。多 3 说明第三轮也问了，锁定没生效；多 0 说明决策器根本没被问，多半是路由文件里判定器不是 http 类型，看 `state/routes.yaml` 的 `judge` 段。

RL 轨迹，最新三条，从新到旧：

```bash
ls -t /Users/murphyren/Desktop/local_infer/logs/rl | head -3 | while read f; do python3 -c "import json,sys;d=json.load(open('/Users/murphyren/Desktop/local_infer/logs/rl/'+sys.argv[1]));print(d.get('served_tier'), (d.get('decision') or {}).get('route'))" "$f"; done
```

正常：

```
strong None
strong {'weak': 0.3, 'strong': 0.7, 'cloud': 0.0}
weak {'weak': 0.3, 'strong': 0.7, 'cloud': 0.0}
```

第一行是第三轮，没有决策是对的。后两行是前两轮，决策相同，实际服务的层不同，正是"第一次不升、第二次锁定"的记录。每个文件里还有完整的消息历史和 token 数，这就是 RL 的训练样本。

决策器日志也可以对一下，最后两行的 `task_type` 应该都是 `code`，证明请求头穿到了决策器：

```bash
tail -2 /Users/murphyren/Desktop/local_infer/logs/decisions.jsonl | python3 -c "import sys,json;[print(json.loads(l)['state']['turn'], json.loads(l)['state']['task_type']) for l in sys.stdin]"
```

**挂了怎么读。** RL 轨迹第一轮就是 strong：数字没换，会话被复用了。第二轮 502：强池那一跳的问题，回环节 5 的 `large`。RL 轨迹三轮都是 weak 且 calls 加了 2：决策器被问了但没升级，看它返回的 strong 是不是低于 0.5，或者 `routes.yaml` 里 `confirmations` 不是 2。

## 环节 7：个人端本机服务

**这一环是什么。** Pi 和网页不直接连网关，连的是本机 :7000 的服务。它只做三件事：挡住非本机来源；补上会话 ID；把 `x-task-type` 这类路由提示原样转给网关。它是个人端和模型端之间唯一的接口。

**为什么测它。** 它转发时如果丢了请求头，决策器看到的任务类型就是空，判定质量下降，而且从网关那边完全看不出来。之前正是这里把 `x-task-type` 丢了。

**测什么。** 模型列表和网关一致；一条带任务类型的请求经它转发后，决策器日志里能看到那个任务类型。

```bash
diff <(curl -s http://127.0.0.1:7000/v1/models) <(curl -s http://127.0.0.1:4000/v1/models) && echo 'local == hub'
```

正常：只打印 `local == hub`。有 diff 输出说明本机服务指向的不是这个网关，看 `~/.cluster-client/config.json` 里的 `hub`。

带一个平时不会出现的任务类型，好认：

```bash
curl -s http://127.0.0.1:7000/v1/chat/completions -H 'Content-Type: application/json' -H 'x-task-type: extract' -H 'x-switchyard-session-id: manual7-708' -d '{"model":"auto","messages":[{"role":"user","content":"[708] Reply with exactly: client-ok"}],"max_tokens":12,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"content":"client-ok"`，`"model":"qwen3-1.7b"`。第一轮，弱池。`[708]` 和 `manual7-708` 重跑时换数字。

```bash
tail -1 /Users/murphyren/Desktop/local_infer/logs/decisions.jsonl | python3 -c "import sys,json;print(json.load(sys.stdin)['state']['task_type'])"
```

正常：`extract`。这个值只可能来自刚才那个请求头，穿过了本机服务、网关，到了决策器。

**挂了怎么读。** :7000 没输出：`bin/client status`，没起来就 `bin/client up`。打印 `None`：本机服务没转发请求头，看 `client/serve.py` 里 `HINT_HEADERS`。

## 整链

前七环都通了再跑。脚本做的事和环节 5、6 一样，只是从 :7000 进，一次跑完并汇总证据：

```bash
cd /Users/murphyren/Desktop/local_infer && E2E_HUB=http://127.0.0.1:4000 E2E_PAIR_RPC="" python3 tests/e2e_chain.py
```

正常：最后一行 `RESULT: PASS`。`E2E_HUB` 指到 Mac 的网关，不给的话默认打隧道 14000，那是 4090 上另一套 Switchyard。

## 一次跑完

七环加整链，命令和输出对照打印，会话 ID 自动换新：

```bash
bash /Users/murphyren/Desktop/local_infer/tests/manual.sh
```

只跑第 3 环：

```bash
bash /Users/murphyren/Desktop/local_infer/tests/manual.sh 3
```

自动判 PASS/FAIL 的版本：

```bash
cd /Users/murphyren/Desktop/local_infer && python3 tests/links.py all
```

## 图形界面：链路总览

管理台加了一页 `http://localhost:6006/links`（首页左上角"🔗 链路总览"进）。八张卡片就是七个环节加整链，每张卡片一个"测试"按钮，跑的是 `tests/links.py` 同一套检查，✓/✗ 逐条列在卡片里；右上角"全部测试"按顺序跑，勾着"遇错即停"时第一个失败就停在那一环。下面是 PAIR 面板：隧道状态、LM 槽位代理的名单、每台节点（本机 / 远端手动节点）的在线状态、模型和任务数、最近任务落在哪台机器。"添加远端节点"表单填节点名、地址、SSH 目标和端口，会写入 PAIR 的手动节点配置、开隧道、重启 PAIR 桌面端，一分钟后节点出现；"移除"反之。桌面端的启停在 `bin/pair-desktop start|stop|restart|status|show`。

PAIR 桌面端自己的总览页原本只画集群成员和本机，手动节点不画卡片；fork 里改成也画可路由的非成员节点（`1dfbbc6`），现在 4090 有自己的卡片，显示 "NVIDIA GeForce RTX 4090 D"。

## 图形界面：批量测试

`http://localhost:6006/batch`。文本框里一行一句话，选路由（auto / small / large）、任务类型、并发数，点发送：每一行是一个独立会话，同时发出，经本机服务 → Switchyard → 决策器 → PAIR → 引擎。表格实时显示每条的状态、服务模型、实际层（从该请求的 RL 轨迹里对出来，提示里带了唯一标记）、落在哪台节点（发送结束后按 PAIR 账本的时间窗对上，并发很高时可能错一两条）、耗时、token 数、回复摘要（点开看全文）。底部汇总总耗时和节点分布。八句话并发 8 的一次实测：4090 五条、Mac 三条，Mac 上 120 token 要 15 秒，4090 上不到 2 秒，PAIR 只按在途数量分配、不看快慢这一点在这里一眼能看出来。
