# cluster — 推理端

Python package, one subpackage per layer of docs/design.md; layers talk through
HTTP and files only. The Pi side (per-person client) lives in `client/` and is a
separate system: this package never configures Pi.

| Package | Layer | Contents |
|---|---|---|
| `access/` | 管理台 | `console/`: cluster admin console (devices, mode, escalations, stats) and the node registration endpoint |
| `router/` | 调度层 | `routes.py` renders the Switchyard route table from registry × health; `gateway.py` runs the Switchyard process |
| `node/` | 资源层 | `probe.py` hardware detection and pool assignment; `presets.py` preset parsing; `engine.py` vLLM / llama.cpp processes |
| `control/` | 控制平面 | `registry.py` (state/nodes.json, the hot-plug data structure); `health.py`; `watchdog.py` loop; `heal.py` staged restart |
| `util/` | — | process spawn, pid files, HTTP waits |
| `cli.py` | — | `init` / `join` / `link-gpu` / `status` / `stop` / `regen` |

Run: `bin/cluster <command>` (or `python3 -m cluster <command>` from the repo root).

State: `state/` (registry, route table, mode, token, preset), `logs/` (process logs and pids).
Presets: `inference/presets/*.env`. Tests: `python3 -m pytest tests/`.
HTTP smoke tests: `cluster/test.sh [small|large|router|console|pi|failover|all]`; one-shot tasks: `cluster/ask.sh`.
