# homed

Python package. One subpackage per layer of docs/design.md; layers talk through
HTTP and files only.

| Package | Layer | Contents |
|---|---|---|
| `access/` | 接入层 | `pi.py` writes the Pi provider config; `console/` is the web console and the node registration endpoint |
| `router/` | 调度层 | `routes.py` renders the Switchyard route table from registry × health; `gateway.py` runs the Switchyard process |
| `node/` | 资源层 | `probe.py` hardware detection and pool assignment; `presets.py` preset parsing; `engine.py` vLLM / llama.cpp processes |
| `control/` | 控制平面 | `registry.py` (state/nodes.json, the hot-plug data structure); `health.py`; `watchdog.py` loop; `heal.py` staged restart |
| `util/` | — | process spawn, pid files, HTTP waits |
| `cli.py` | — | `init` / `join` / `link-gpu` / `status` / `stop` / `regen` |

Run: `bin/homed <command>` (or `python3 -m homed <command>` from the repo root).

State: `state/` (registry, route table, mode, token, preset), `logs/` (process logs and pids).
Presets: `inference/presets/*.env`. Tests: `python3 -m pytest tests/`.
HTTP smoke tests: `homed/test.sh [small|large|router|console|pi|failover|all]`; one-shot tasks: `homed/ask.sh`.
