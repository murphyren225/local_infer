# AI Cluster — a complete local AI stack on one consumer GPU

**English** | [中文](README.zh-CN.md)

> A Claude-Code-grade agent experience on your own hardware: Pi as the entrypoint,
> Switchyard for smart triage, a large + small open model sharing one GPU, and automatic
> cloud-API failover when a local model dies. Everything is stock open-source parts —
> we only build the glue that turns them into one "AI cluster".
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
bin/install.sh                # prerequisites: service venv + Switchyard, Pi, (CPU box) llama.cpp + 1.7B model
# download weights (ModelScope inside China, HF elsewhere) to local dirs, then:
bin/homed init                # one command up: probe hardware, pick preset, start lanes, gateway, console
./homed/test.sh all           # per-component tests: small|large|router|console|pi
./homed/ask.sh auto "any task"
```

- Console access: AutoDL users click "Custom Service" (port 6006); otherwise
  `ssh -L 6006:127.0.0.1:6006 <host> -N` and open http://localhost:6006
- Cloud failover: put `TOGETHER_API_KEY=...` in `.env` for a real cloud tier
- Model switching: `bin/homed init --preset <name>` — adding a model
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

One package per layer of the design; layers talk only over HTTP and files (see
[docs/repo-layout.md](docs/repo-layout.md)): `homed/access/` (access layer: Pi config,
web console + node registration), `homed/router/` (scheduling: Switchyard route table,
gateway process), `homed/node/` (resources: hardware probe, presets, vLLM / llama.cpp),
`homed/control/` (control plane: node registry, health, watchdog, staged heal).

## Model support

| Model | Status |
|---|---|
| Qwen3-32B-AWQ + Qwen3-1.7B-FP8 | ✅ validated on 24GB (current default) |
| Qwen3.8-27B | ⏳ waiting for a 4-bit quant (bf16 56GB / FP8 28GB both exceed 24GB) |
| GLM-5.3-Flash | 📋 preset reserved: 320B/18B MoE, smallest quant ~93GB — DGX Spark / 128GB-class devices |

## Status

Single-box full stack, failover/self-heal and the Mac-hub + remote-GPU link are validated
on real machines (2026-09). Registry-driven hot-plug: `init` and `link-gpu` validated,
`join` implemented and awaiting a two-machine LAN test. Real cloud failover is wired but
awaits a key. Per-pool load balancing, auto-discovery and overload protection are on the
roadmap.

## Documentation

- [docs/design.md](docs/design.md) — **the system document** (design / API contract / implementation & ops; Chinese)
- [docs/hardware-model-matrix.md](docs/hardware-model-matrix.md) — product page (hardware, config, linking, tasks)
- [docs/roadmap.md](docs/roadmap.md) — roadmap
- [docs/repo-layout.md](docs/repo-layout.md) — repository layout mapped to the design (Chinese)

## License

MIT
