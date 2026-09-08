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
| **Client (Pi side)** | every person's own machine, one per person | `client/` | `bin/client setup / web` |
| **Cluster (inference side)** | the devices in the cluster (GPU box, Mac, big-RAM host) | `cluster/` | `bin/cluster init / join / link-gpu` |

## 1. Interfaces

**`pi` in a terminal** (client side) — a Claude-Code-like coding agent running against
your own cluster, with real tool execution on your machine. `/model` switches lanes;
tools for internal systems are added as one TypeScript file each under
[client/pi/extensions/](client/pi/extensions/).

**Personal web UI (:7000, client side)** — `bin/client web` starts a chat page on your
own machine: lane picker (`auto`/`small`/`large`/`cloud`), every answer annotated with
the model that did the work and the end-to-end latency. Zero dependencies beyond Python.

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

### Cluster side (once per device)

Prereqs: NVIDIA GPU (24GB tier validated) with `pip install vllm`, or a Mac/CPU box
(llama.cpp is built by the installer); Python 3.12+ for the gateway venv.

```bash
git clone https://github.com/murphyren225/local_infer.git && cd local_infer
bin/install.sh                # service venv + Switchyard; (CPU box) llama.cpp + 1.7B model
# download weights (ModelScope inside China, HF elsewhere) to local dirs, then:
bin/cluster init              # first device = Hub: probe hardware, pick preset, start lanes, gateway, console
bin/cluster join http://<hub>:6006 --token JOIN-xxxx     # any further device on the LAN
bin/cluster link-gpu "ssh -p 43314 root@gpu-host"        # or adopt a remote GPU over SSH
./cluster/test.sh all         # per-component tests: small|large|router|console|pi
```

### Client side (once per person)

Prereqs: Node 22+ (for Pi). Python 3 only for the optional web page.

```bash
bin/install-client.sh http://<hub>:4000    # installs Pi and wires it to the cluster
pi                                         # terminal agent, default lane auto
bin/client web --hub http://<hub>:4000     # personal web UI at http://127.0.0.1:7000
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
