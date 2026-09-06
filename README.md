# Home AI Cluster — a complete local AI stack on one consumer GPU

**English** | [中文](README.zh-CN.md)

> A Claude-Code-grade agent experience on your own hardware: Pi as the entrypoint,
> Switchyard for smart triage, a large + small open model sharing one GPU, and automatic
> cloud-API failover when a local model dies. Everything is stock open-source parts —
> we only build the glue that turns them into one "home AI computer".
> Validated end-to-end on a real RTX 4090D.

## 1. Interfaces

**Web console (:6006)** — for everyday users and admins: chat with a lane picker
(`auto`/`small`/`large`/`cloud`), **file upload** (txt/md/csv/json/code, ≤2MB) for
summarize/analyze tasks, and full routing transparency — every answer is annotated with
the model that did the work, end-to-end latency and tok/s; a live side panel shows the
router's current policy, the **escalation event stream** (the judge's verbatim reasons
for upgrading a task to the 32B), cumulative stats, and a cluster health badge that turns
orange in degraded mode and explains where traffic is going.

**`pi` in a terminal** — for developers: a Claude-Code-like coding agent running on your
own GPU, with real tool execution. `/model` switches lanes.

**OpenAI-compatible API** — for every existing tool:

```
POST http://<host>:4000/v1/chat/completions    model: auto | small | large | cloud
```

Anthropic Messages format is also accepted. That is the entire API surface.

## 2. Measured performance (RTX 4090D 24GB)

| Metric | Measured |
|---|---|
| Co-resident VRAM | 23.45 / 24.56 GB (1.1GiB safety margin, calibrated via OOM drill) |
| Small lane (Qwen3-1.7B-FP8) | TTFT 0.15s, 108 tok/s aggregate @4 concurrent |
| Large lane (Qwen3-32B-AWQ) | TTFT 0.17s, 38 tok/s aggregate @2 concurrent |
| Failure detection → route switch | ~30–40s (10s watchdog interval × 2 consecutive misses) |
| Self-heal after a large-model crash | 2–5 min automatic (staged restart; zero downtime with a cloud key) |

## 3. How to use

Prereqs: NVIDIA GPU (24GB tier validated), Python 3.10+, `pip install vllm nemo-switchyard`,
Node 22+ (`npm i -g --ignore-scripts @earendil-works/pi-coding-agent`).

```bash
git clone https://github.com/murphyren225/local_infer.git && cd local_infer
# download weights (ModelScope inside China, HF elsewhere) to local dirs, then:
./homed/run_cluster.sh        # one command up (32B load ≈ 4 min); idempotent; `stop` to halt
./homed/test.sh all           # per-component tests: small|large|router|console|pi
./homed/ask.sh auto "any task"
```

- Console access: AutoDL users click "Custom Service" (port 6006); otherwise
  `ssh -L 6006:127.0.0.1:6006 <host> -N` and open http://localhost:6006
- Cloud failover: put `TOGETHER_API_KEY=...` in `.env` for a real cloud tier
- Model switching: `INFERENCE_PRESET=<name> ./homed/run_cluster.sh` — adding a model
  family = adding one preset file, see [homed/inference/](homed/inference/)
- Failover drill: `./homed/test.sh failover` (kills the 32B on purpose, watches the
  auto-switch and self-heal complete)

## 4. What it can do

Front-desk tasks on the small lane at ~zero cost (translate, summarize, rewrite,
proofread, classify, extract, rename); expert tasks on the 32B (design analysis, code
review, contract risk, root-cause analysis, math); file analysis via console upload;
multi-step agent tasks with real tool execution via pi. The `auto` lane decides who does
what, and the judge silently escalates when the small model's answer isn't good enough.

## Components

One module per directory, communicating only via HTTP and files — see
[homed/README.md](homed/README.md): harness ([Pi](https://pi.dev/)), router
([NeMo Switchyard](https://github.com/NVIDIA-NeMo/Switchyard)), inference (vLLM +
measured presets), failover (watchdog + staged self-heal, ~120 lines of shell, fully
ours), console (FastAPI single page).

## Model support

| Model | Status |
|---|---|
| Qwen3-32B-AWQ + Qwen3-1.7B-FP8 | ✅ validated on 24GB (current default) |
| Qwen3.8-27B | ⏳ waiting for a 4-bit quant (bf16 56GB / FP8 28GB both exceed 24GB) |
| GLM-5.3-Flash | 📋 preset reserved: 320B/18B MoE, smallest quant ~93GB — DGX Spark / 128GB-class devices |

## Status (the honest version)

Single-box full stack and the failover/self-heal loop are validated on a real machine
(2026-09-06, destructive drill included). Real cloud-API failover is wired but awaits a
real key. Multi-device linking is validated on real machines (2026-09): `homed init` + `homed link-gpu` turn a Mac into the hub (gateway + console + local weak lane via llama.cpp) with a remote 4090 as the strong lane over an SSH tunnel — tunnel loss auto-degrades, relink auto-recovers. Auto-discovery (mDNS/join tokens) is designed but not yet built. Phase-1 assets
(Tandem gateway, agent protocol, routing eval set) live on in docs/ and CI.

## License

MIT
