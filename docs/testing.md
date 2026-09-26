# 链路逐环节手工测试

拓扑见 `pipeline-design.md` §9。一条请求的路：

```
本机服务 :7000 → Switchyard :4000 → 决策器 :4100 → PAIR 代理（弱池 :21434 / 强池 :11234）→ 引擎
```

Mac 是调度机。4090 通过 SSH 隧道接到 Mac 的 127.0.0.1 上，PAIR 把它当作一个手动节点。
下面七个环节从底往上，每一环只依赖它下面的环。**哪一环第一个失败，问题就在那一段**，上面的失败都是它引起的。

所有命令在 Mac 上跑，不依赖当前目录。`tests/manual.sh` 把它们串起来自动跑；`tests/links.py` 是同一套检查的自动化版本。

## 0. 前提：隧道

4090 上的端口只监听在 4090 本机。Mac 上凡是 1234、11434、14318 这几个端口，都是 ssh 隧道开的入口，请求会被原样转到 4090 的对应端口。隧道断了，这些端口就没人听，curl 什么都不打印。

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
| 11434 | 4090 的 Ollama 引擎（隧道） |
| 14318 | 4090 的 PAIR 节点信息（隧道） |
| 11435 | Mac 自己的 Ollama 引擎，PAIR 管的 |
| 11234 | Mac 上 PAIR 的 LM 槽位代理 |
| 21434 | Mac 上 PAIR 的 Ollama 代理 |
| 4100 | 决策器 |
| 4000 | Switchyard 网关 |
| 7000 | 个人端本机服务 |

## 环节 1：引擎直连

**这一环是什么。** 三个真正做推理的进程：4090 上的 SGLang（强池引擎，GPU）、4090 上的 Ollama（弱池引擎之一，GPU）、Mac 上的 Ollama（弱池引擎之一，CPU）。它们各自是一个 OpenAI 兼容的 HTTP 服务，加载了一个模型。

**为什么先测它。** 绕开所有路由和调度，直接打引擎。如果这里不通，上面的每一环都会跟着失败，但原因和调度无关：模型没加载、显存不够、进程挂了、隧道断了。

**测什么。** 两件事：引擎报的模型名是不是我们要的；引擎能不能按要求回一句话。模型名很重要，Switchyard 和 PAIR 都是按名字找引擎的，SGLang 这边叫 `qwen3-1.7b`，Ollama 那边叫 `qwen3:1.7b`，一个字符不同就是两个模型。

4090 的 SGLang，问它加载了什么：

```bash
curl -s http://127.0.0.1:1234/v1/models
```

正常：`"id":"qwen3-1.7b"`，`"owned_by":"sglang"`，`"max_model_len":8192`。这是 SGLang 自己报的，8192 是启动参数里给的上下文长度。

4090 的 SGLang，让它说一句话：

```bash
curl -s http://127.0.0.1:1234/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3-1.7b","messages":[{"role":"user","content":"Reply with exactly: sglang-ok"}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"content":"sglang-ok"`，`"finish_reason":"stop"`，`completion_tokens` 是个位数。`chat_template_kwargs.enable_thinking=false` 是关掉 Qwen3 的思考模式，不关的话 20 个 token 全花在思考上，content 是空的。

4090 的 Ollama，问它有哪些模型。Ollama 有自己的原生接口，这里用它：

```bash
curl -s http://127.0.0.1:11434/api/tags
```

正常：`qwen3:1.7b` 和 `qwen3:8b` 两个。`qwen3:8b` 只有 4090 有，后面环节 3 靠它证明请求真的去了 4090。

Mac 的 Ollama，让它说一句话：

```bash
curl -s http://127.0.0.1:11435/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3:1.7b","messages":[{"role":"user","content":"Reply with exactly: mac-ok"}],"max_tokens":20,"reasoning_effort":"none"}'
```

正常：`"content":"mac-ok"`，`"system_fingerprint":"fp_ollama"`，两三秒，因为是 CPU。`reasoning_effort: none` 是 Ollama 关思考的开关，Ollama 不认 `chat_template_kwargs`。

**挂了怎么读。** 前两条没输出：隧道断了，回到第 0 步。前两条有输出但模型名不对或 500：SGLang 进程有问题，去 4090 上看 PAIR 引擎日志。第四条没输出：Mac 上 PAIR 桌面端没在跑，或者它管的 Ollama 没起来。

## 环节 2：PAIR 看见 4090

**这一环是什么。** PAIR 在 Mac 上跑，维护一份"哪个节点上有哪个模型"的目录。4090 不在局域网，是作为"手动节点"加进去的：PAIR 每隔几秒去探测它的三个端口，节点信息 14318、Ollama 引擎 11434、LM 槽位引擎 1234，把探到的模型并进目录。目录对外的形式就是两个代理的 `/v1/models`。

**为什么测它。** 环节 3 的调度是在目录里挑节点。目录里没有 4090，PAIR 就只能选 Mac 自己，"选硬件"无从谈起。

**测什么。** 两个代理的清单里有没有只属于 4090 的模型；手动节点的配置在不在。

LM 槽位代理的清单：

```bash
curl -s http://127.0.0.1:11234/v1/models
```

正常：只有一个 `qwen3-1.7b`，`owned_by` 是 `sglang`。Mac 上没有任何 LM 槽位引擎，这里出现的一切都来自 4090。所以这条命令有输出就直接证明 PAIR 探到了 4090 的 SGLang。

Ollama 代理的清单：

```bash
curl -s http://127.0.0.1:21434/v1/models
```

正常：Mac 自己的几个（`qwen3:1.7b`、`moondream`、`qwen2.5` 系列）加上 4090 独有的 `qwen3:8b`。看到 `qwen3:8b` 就说明两台的 Ollama 目录合并了。

手动节点的持久化配置，桌面端每次启动会读它并重新加节点：

```bash
cat ~/Library/Application\ Support/Nvidia\ Corporation/Personal\ AI\ Router/configs/manual-nodes.json
```

正常：`[{"id":"autodl-4090","address":"127.0.0.1","name":"autodl-4090"}]`。地址是 127.0.0.1 不是笔误，PAIR 拨的是隧道入口。

**挂了怎么读。** 环节 1 通、这里 4090 的模型不见了：等二十秒再试，PAIR 探测有周期；还不行就是 PAIR 桌面端的手动节点没加上，看配置文件在不在，重启桌面端。

## 环节 3：PAIR 选硬件

**这一环是什么。** PAIR 代理收到一个请求，看 `model` 字段，在目录里找出持有该模型的节点，按每个节点在途请求数挑一台，把请求转过去，完成后在账本里记一笔：哪个模型、哪个引擎、在哪台机器上跑的。这就是"选硬件"。

**为什么测它。** 这是整条链里唯一在两台机器之间做选择的地方。要证明两件事：只有 4090 有的模型一定去 4090；两台都有的模型会被分到两台。

**测什么。** 用模型名控制候选集，然后读账本看实际去向。

只有 4090 有的模型：

```bash
curl -s http://127.0.0.1:21434/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"Reply with exactly: via-4090"}],"max_tokens":20,"reasoning_effort":"none"}'
```

正常：`"content":"via-4090"`。Mac 上没有 8B，这一句只可能是 4090 算的。

两台都有的模型，六条并发：

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code} " http://127.0.0.1:21434/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3:1.7b","messages":[{"role":"user","content":"Say hi"}],"max_tokens":6,"reasoning_effort":"none"}' & done; wait; echo
```

正常：六个 200。并发是故意的，串行发的话 PAIR 每次都会选空闲的本机，看不到分流。

等五秒，读 Mac 上 PAIR 的账本。账本是异步落盘的，太早读会少几条：

```bash
python3 -c "
import json,os
p=os.path.expanduser('~/Library/Application Support/Nvidia Corporation/Personal AI Router/workloads-history.json')
me=json.load(open(os.path.dirname(p)+'/node-id.json'))['node_uuid']
ws=json.load(open(p)); ws=ws.get('workloads',ws) if isinstance(ws,dict) else ws
for w in ws[-8:]: print(w['model'], w['engine'], w['state'], 'on', 'this-mac' if w['scheduledOn']==me else 'autodl-4090')
"
```

正常：一行 `qwen3:8b ollama completed on autodl-4090`；六行 `qwen3:1.7b ollama completed`，一部分 `this-mac` 一部分 `autodl-4090`。分配比例不固定，PAIR 只按在途数量，不看两台快慢，这一点是它的已知局限。桌面端窗口左边的 Jobs 列表显示的就是这份账本，"Ran on autodl-4090" 那几条就是。

**挂了怎么读。** `qwen3:8b` 返回 `no available node advertises the requested model`：目录里没有 4090，回环节 2。六条里有非 200：看哪台，Mac 的 Ollama 慢是正常的，超时看 curl 的 `-m`。账本里全是 this-mac：并发不够或者 4090 那一刻被判不可达，多跑几次。

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

**这一环是什么。** Switchyard 是路由网关。它对外暴露几个路由名：`small`、`large` 是固定路由，直接映射到弱池、强池的目标；`auto` 是带判定的路由，环节 6 测。每个池的目标现在都是 PAIR 的一个代理，弱池指 Ollama 代理，强池指 LM 槽位代理，这个映射是 `cluster init` 按 `.env` 生成的，写在 `state/routes.yaml`。

**为什么先测固定路由。** 固定路由没有判定逻辑，只有"名字到目标"的映射。它通了，说明 Switchyard 到 PAIR 到引擎这三跳是连着的，环节 6 再出问题就只剩判定这一个变量。

**测什么。** `small` 是不是落到弱池的模型，`large` 是不是落到强池的模型。看返回里的 `model` 字段，那是最终服务的引擎报的名字。

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"small","messages":[{"role":"user","content":"Reply with exactly: small-ok"}],"max_tokens":20,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"model":"qwen3:1.7b"`，`"content":"small-ok"`。冒号版的名字，说明是 Ollama 家族，也就是走了弱池的代理。

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"large","messages":[{"role":"user","content":"Reply with exactly: large-ok"}],"max_tokens":20,"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}}'
```

正常：`"model":"qwen3-1.7b"`，`"content":"large-ok"`。横杠版的名字，SGLang 家族，走了强池的代理，只能是 4090。

再跑一次环节 3 的账本命令，最后两条应该是 `qwen3:1.7b ollama` 和 `qwen3-1.7b lmstudio`。`lmstudio` 是 PAIR 里那个槽位的内部名字，实际跑的是 SGLang。

两条请求都同时带了 `reasoning_effort` 和 `chat_template_kwargs`，因为事先不知道会落到哪个家族的引擎，各家认各家的，多余的会被忽略。

**挂了怎么读。** 502：Switchyard 到代理那一跳断了，看 `state/routes.yaml` 里的 `base_url` 是不是 21434 和 11234。`model` 字段和预期反了：`.env` 里 `PAIR_PROXY_URL` 和 `PAIR_STRONG_PROXY_URL` 配反了，改完 `bin/cluster regen`。

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

正常：`qwen3:1.7b`。决策器给了 strong 0.7，但这是第一次，还没到两次确认，所以仍在弱池。

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

**挂了怎么读。** 第一轮就是 `qwen3-1.7b`：数字没换，会话被复用了。第二轮 502：强池那一跳的问题，回环节 5 的 `large`。三轮都是 `qwen3:1.7b` 且 calls 加了 2：决策器被问了但没升级，看它返回的 strong 是不是低于 0.5，或者 `routes.yaml` 里 `confirmations` 不是 2。

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

正常：`"content":"client-ok"`，`"model":"qwen3:1.7b"`。第一轮，弱池。`[708]` 和 `manual7-708` 重跑时换数字。

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
