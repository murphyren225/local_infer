# 把一台 Windows + NVIDIA 机器接成第三个节点（LM Studio）

Mac 上的 PAIR 把它当"手动节点"：每隔几秒探测 `http://<Windows IP>:1234/v1/models`，探到模型就把这台机器放进 LM 槽位的名单，和 Mac 的 llama-server、4090 的 SGLang 并列。所以 Windows 这边只需要一件事：**LM Studio 在局域网的 1234 端口上提供 OpenAI 接口，模型标识符叫 `qwen3-1.7b`**。

Windows 上原来装的 PAIR 要先退出：它占着 1234 端口，而且对集群外的明文请求返回 403（2026-09-27 从 Mac 探 `10.0.0.173:1234` 得到的就是 403）。Mac 现在跑的是端口偏移版 PAIR，和 Windows 上的官方版配不成对，所以不走 PAIR 对 PAIR 的配对，走手动节点。

## 给 Windows 上 Claude Code 的 prompt

```
你在一台 Windows + NVIDIA 显卡的电脑上。目标：让这台机器用 LM Studio 在局域网上提供一个 OpenAI 兼容接口，
端口 1234，模型是 Qwen3-1.7B，模型标识符必须正好是 qwen3-1.7b。局域网里另一台 Mac 会来调用它。
每一步做完先验证再做下一步；需要管理员权限或需要我在图形界面里点的地方，停下来告诉我具体点哪里。

1. 先看现状并汇报：Windows 版本、显卡型号和显存（nvidia-smi）、本机局域网 IPv4 地址、
   1234 端口现在被谁占用（netstat -ano | findstr :1234，再用 tasklist 查 PID）。

2. 如果 NVIDIA Personal AI Router（PAIR）在运行，把它退出（托盘图标右键 Exit，或结束 nvpair-* 和
   lmstudio-proxy、ollama-proxy 进程）。不要卸载。确认 1234 端口已经空出来。

3. 确认 LM Studio 已安装且命令行工具 lms 可用（lms --version）。没装就从 https://lmstudio.ai 下载安装，
   装完运行一次 LM Studio，再执行 lms bootstrap。先看 lms --help 和各子命令的 --help，以实际参数为准。

4. 下载模型 Qwen3-1.7B 的 GGUF（Q8_0 或 Q4_K_M 都可以）：用 lms get 搜 qwen3-1.7b。
   如果 Hugging Face 连不上，告诉我，我来手动放文件。

5. 加载模型并把标识符设成 qwen3-1.7b，GPU 全量卸载，上下文长度 8192：
   类似 lms load <模型> --identifier qwen3-1.7b --gpu max --context-length 8192。
   用 lms ps 确认标识符正好是 qwen3-1.7b。

6. 启动服务并对局域网开放，端口 1234：lms server start，并打开"Serve on Local Network"
   （命令行有 --bind 0.0.0.0 之类的参数就用参数，没有就告诉我在 LM Studio 的 Developer 页里打开这个开关）。
   用 netstat 确认监听在 0.0.0.0:1234 而不是 127.0.0.1:1234。

7. 防火墙放行入站 TCP 1234，只对专用网络：
   netsh advfirewall firewall add rule name="LM Studio 1234" dir=in action=allow protocol=TCP localport=1234 profile=private
   需要管理员终端；不是管理员就停下来告诉我。

8. 本机验证，两条都要过：
   curl http://127.0.0.1:1234/v1/models            → data 里有 id 为 qwen3-1.7b
   curl http://<本机局域网IP>:1234/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"qwen3-1.7b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: levi-ok\"}],\"max_tokens\":200}"
   看回复的 content：如果里面有 <think>…</think> 或者 content 为空而思考内容在别的字段里，
   说明 Qwen3 的思考模式开着。试着在 LM Studio 里把这个模型的思考关掉（模型设置里的 reasoning / think 开关，
   或在系统提示词预设里加 /no_think），直到 content 直接是 levi-ok。关不掉就如实告诉我现象。

9. 让它开机后还在：LM Studio 设置里打开"登录时启动"和无界面服务（headless / run LLM service on login），
   并设置这个模型自动加载（或 JIT 加载）且保持标识符。做不到的部分告诉我。

10. 最后汇报：本机局域网 IP、lms ps 的输出、第 8 步两条命令的原始返回、生成速度（tok/s）、
    以及哪些步骤是我需要手动做的。不要改动其它软件，不要卸载任何东西。
```

## Windows 那边好了以后，Mac 这边

1. 从 Mac 验证能打到：`curl -s http://<Windows IP>:1234/v1/models`，应列出 `qwen3-1.7b`。
2. 打开 `http://localhost:6006/links`，"添加远端节点"：节点名 `levi`，地址填 Windows 的局域网 IP，SSH 两栏留空。它会写入 PAIR 的手动节点配置并重启 PAIR 桌面端，约一分钟。
3. PAIR 窗口里出现第三张卡片；`/batch` 页发十几句话，节点分布里应出现三台。

注意：Windows 的 IP 是 DHCP 分的，变了就要在第 2 步重新加一次；在路由器里给它绑一个固定 IP 最省事。
