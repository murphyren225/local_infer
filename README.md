# AI Cluster — a complete local AI stack on your own hardware

**English** | [中文](README.zh-CN.md)

> A Claude-Code-grade agent experience on your own hardware: Pi as the harness on each
> person's machine, Switchyard for smart triage, a large + small open model sharing one
> GPU, and automatic cloud-API failover when a local model dies. Everything is stock
> open-source parts — we only build the glue. Validated end-to-end on a real RTX 4090D.

Two systems, shaped like Claude Code — the harness sits with the user, the models sit
on the other side, and the only contract between them is one gateway URL:

| System | Where it runs | Directory | Entrypoint |
|---|---|---|---|
| **Personal side (harness)** | every person's own machine, one per person | `client/` | `bin/install-client.sh URL` (one command: Pi, wiring, local service) |
| **Model side (Switchyard + inference)** | the devices in the cluster (GPU box, Mac, big-RAM host) | `cluster/` | `bin/install.sh` (one command: install, download, serve) |

## 1. Interfaces

**`pi` in a terminal** (client side) — a Claude-Code-like coding agent running against
your own cluster, with real tool execution on your machine. `/model` switches lanes;
tools for internal systems are added as one TypeScript file each under
[client/pi/extensions/](client/pi/extensions/).

**Personal local service (:7000, personal side)** — `bin/client up` starts a small service
on your own machine: a web page with two tabs — **Chat** (one inference call on the model
side, nothing runs here) and **Agent** (the task goes to Pi on this machine, which reads
and writes files in a working directory you choose; the model side only answers) — plus
`client ask` / `client batch` for inference jobs from scripts. Every answer is annotated
with the model that did the work and the end-to-end latency. Zero dependencies beyond Python.

**Cluster admin console (:6006, Hub)** — for admins: device cards with hardware
profiles and health, the router's current policy, the **escalation event stream** (the
judge's verbatim reasons for upgrading a task to the 32B), cumulative stats, a health
badge that turns orange in degraded mode, plus a chat area with file upload for testing.

**OpenAI-compatible API** — for every existing tool:

```
POST http://<hub>:4000/v1/chat/completions    model: auto | small | large | cloud
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

### Model side — one command on a blank device

Prereqs: an NVIDIA GPU (24GB tier validated) with drivers, or a Mac/CPU box; Python 3.12+.

```bash
curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash
```

That clones the repo and runs `bin/install.sh`: service venv + Switchyard; on a GPU box
vLLM plus the Qwen3-32B-AWQ and Qwen3-1.7B-FP8 weights (ModelScope; `--hf` for Hugging
Face), on a Mac llama.cpp plus the 1.7B GGUF; then `cluster init` — the device is serving
when the command returns. Further devices: `bin/install.sh --join http://<hub>:6006 --token JOIN-xxxx`;
a remote GPU over SSH: `bin/cluster link-gpu "ssh -p 43314 root@gpu-host"`.
`./cluster/test.sh all` runs the per-component tests.

### Personal side — one command on your own machine

Prereqs: Node 22+ (Pi installs into your home dir, no sudo). Python 3 for the local service.

```bash
curl -fsSL https://raw.githubusercontent.com/murphyren225/local_infer/main/bin/bootstrap.sh | bash -s -- personal http://<hub>:4000
```

Then:

```bash
bin/client pi                                  # Pi harness: tools run here, models run on the cluster
open http://127.0.0.1:7000                     # personal web UI: Chat (inference only) / Agent (Pi on this machine)
bin/client ask "summarize this" --lane small   # one inference call, no harness
bin/client batch prompts.txt -c 4 -o out.jsonl # a batch of inference jobs, concurrent
```

- Admin console: http://<hub>:6006 (on AutoDL click "Custom Service"; elsewhere
  `ssh -L 6006:127.0.0.1:6006 <host> -N`)
- Cloud failover: put `TOGETHER_API_KEY=...` in the Hub's `.env`
- Model switching: `bin/cluster init --preset <name>` — adding a model
  family = adding one preset file, see [cluster/inference/](cluster/inference/)
- Failover drill: `./cluster/test.sh failover` (kills the 32B on purpose, watches the
  auto-switch and self-heal complete)

## 4. What it can do

Front-desk tasks on the small lane at ~zero cost (translate, summarize, rewrite,
proofread, classify, extract, rename); expert tasks on the 32B (design analysis, code
review, contract risk, root-cause analysis, math); multi-step agent tasks with real tool
execution via pi on your own files. The `auto` lane decides who does what, and the
judge silently escalates when the small model's answer isn't good enough.

## Components

The cluster package has one subpackage per layer of the design; layers talk only over
HTTP and files (see [docs/repo-layout.md](docs/repo-layout.md)):
`cluster/router/` (scheduling: Switchyard route table, gateway process),
`cluster/node/` (resources: hardware probe, presets, vLLM / llama.cpp),
`cluster/control/` (control plane: node registry, health, watchdog, staged heal),
`cluster/access/` (admin console + node registration). The client package is
`client/pi/` (Pi wiring + extensions) and `client/web/` + `client/serve.py` (personal
web UI). The two packages never import each other.

## Model support

| Model | Status |
|---|---|
| Qwen3-32B-AWQ + Qwen3-1.7B-FP8 | ✅ validated on 24GB (current default) |
| Qwen3.8-27B | ⏳ waiting for a 4-bit quant (bf16 56GB / FP8 28GB both exceed 24GB) |
| GLM-5.3-Flash | 📋 preset reserved: 320B/18B MoE, smallest quant ~93GB — DGX Spark / 128GB-class devices |

## Status

Single-box full stack, failover/self-heal and the Mac-hub + remote-GPU link are validated
on real machines (2026-09). Registry-driven hot-plug: `init` and `link-gpu` validated,
`join` implemented and awaiting a two-machine LAN test. Client side: Pi wiring and the
personal web UI run locally; a Hub-hosted zero-install mode is on the roadmap. Real
cloud failover is wired but awaits a key.

## Documentation

- [docs/design.md](docs/design.md) — **the system document** (design / API contract / implementation & ops; Chinese)
- [docs/hardware-model-matrix.md](docs/hardware-model-matrix.md) — product page (hardware, config, linking, tasks)
- [docs/roadmap.md](docs/roadmap.md) — roadmap
- [docs/repo-layout.md](docs/repo-layout.md) — repository layout mapped to the design (Chinese)

## License

MIT
