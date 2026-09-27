# 把 Windows 机器 LEVI 接进链路（LM Studio 槽位）

## 现状（2026-09-27 从 Mac 实测）

LEVI（`10.0.0.173`，RTX 4060 Laptop 8 GB）**已经在 PAIR 集群里**：之前 PIN 配对的信任还在，Mac 的 PAIR 通过 mTLS 能调到它，
PAIR 窗口里有它的卡片。从 Mac 的 Ollama 代理发 `qwen3:1.7b`，账本记录落在 levi，回复正常。不需要手动加节点。

它还**不在我们的链路里**，原因有两个：

1. 它的引擎在 **Ollama 槽位**。PAIR 每个槽位一份名单，我们的链路走 LM 槽位代理（Mac 的 llama-server、4090 的 SGLang 都在这份名单上），
   所以 Switchyard 发出的请求永远选不到 LEVI。
2. 模型名不一样：LEVI 报的是 `qwen3:1.7b`，链路里用的是 `qwen3-1.7b`。PAIR 按名字匹配。

要做的事：在 LEVI 上通过 PAIR 启用 LM Studio，装 Qwen3-1.7B 并加载。**PAIR 不要退出**（它是 LEVI 进集群的通道）。
PAIR 对外广播的模型名是 LM Studio 的 model key（`/api/v1/models` 里的 `key`），这个名字由 LM Studio 决定；
如果它不是 `qwen3-1.7b`，把实际的 key 告诉 Mac 这边，我们把另外两台的服务名改成和它一致（SGLang 的 `--served-model-name`、
llama-server 的 `--alias`、`.env` 里的池模型），三台就同名了。

## 给 LEVI 上 Claude Code 的 prompt

```
你在一台 Windows 笔记本上（RTX 4060 Laptop 8GB），机器上装着 NVIDIA Personal AI Router（PAIR）并且在运行，
它已经和局域网里一台 Mac 配成了集群。PAIR 现在管着 Ollama。目标：让 PAIR 同时管一个 LM Studio，
LM Studio 里加载 Qwen3-1.7B，让 Mac 那边能通过 PAIR 的 LM Studio 槽位调到这台机器。
不要退出或卸载 PAIR，不要动 Ollama。每一步做完先验证；需要我在图形界面里点的地方，停下来告诉我具体点哪里。

1. 汇报现状：nvidia-smi；PAIR 是否在运行（tasklist | findstr /i "nvpair lmstudio-proxy ollama-proxy"）；
   LM Studio 是否已安装（%USERPROFILE%\.lmstudio\bin\lms.exe 是否存在，lms --version）；
   谁在监听 1234 和 1235（netstat -ano | findstr ":1234 :1235"）。正常情况是 PAIR 的 lmstudio-proxy 占 1234，
   LM Studio 自己的服务由 PAIR 放在 1235。

2. 安装并启用 LM Studio 引擎：让我在 PAIR 窗口里本机那张卡片上点 "LM Studio" 按钮（安装），装完后把它的开关打开。
   如果 LM Studio 已经装过，只需要打开开关。然后确认 http://127.0.0.1:1235/v1/models 返回 200。

3. 下载模型：lms get 搜 Qwen3 1.7B 的 GGUF（Q8_0 或 Q4_K_M）。Hugging Face 连不上就告诉我。
   先看 lms --help 和子命令的 --help，以实际参数为准。

4. 加载模型：lms load <模型> --gpu max --context-length 8192，用 lms ps 确认已加载。

5. 查 PAIR 会对外广播的名字：curl http://127.0.0.1:1235/api/v1/models ，找到 Qwen3-1.7B 那一项的 "key" 字段，
   原样告诉我（比如 qwen3-1.7b 或 qwen/qwen3-1.7b）。如果能让这个 key 正好是 qwen3-1.7b 就这么做
   （例如换一个 key 是 qwen3-1.7b 的 GGUF 来源），做不到也没关系，把实际的 key 告诉我就行。

6. 通过 PAIR 的代理验证（注意是 1234，不是 1235）：
   curl http://127.0.0.1:1234/v1/models
   curl http://127.0.0.1:1234/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"<第5步的key>\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: levi-ok\"}],\"max_tokens\":200}"
   看回复的 content：如果里面有 <think>…</think>，或者 content 为空而思考内容在别的字段里，说明 Qwen3 的思考模式开着。
   试着在 LM Studio 里把这个模型的思考关掉（模型默认设置里的 reasoning / think 开关，或给它的默认系统提示词加 /no_think），
   直到 content 直接是 levi-ok。关不掉就如实告诉我现象。

7. 让它重启后还在：LM Studio 设置里让这个模型自动加载（或打开 JIT 加载），PAIR 里 LM Studio 的开关保持打开。

8. 最后汇报：第 5 步的 key 原文、lms ps 输出、第 6 步两条命令的原始返回、生成速度（tok/s）、哪些步骤需要我手动做。
```

## LEVI 好了以后，Mac 这边

1. PAIR 窗口里 LEVI 的卡片上 LM Studio 开关应是开的；`http://localhost:6006/links` 下半部分 levi 那一行从"不在链路里"变成"在链路里"。
2. 如果 LEVI 的 key 不是 `qwen3-1.7b`：改 4090 的 `--served-model-name`（`engines/lmstudio.json`）、Mac 的 `--alias`（同名文件）、`.env` 的
   `PAIR_STRONG_MODEL` / `PAIR_WEAK_MODEL`，重启两个引擎，`bin/cluster init`。
3. `/batch` 页发十几句话，节点分布里应出现三台。
