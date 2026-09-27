# tools/pair

Everything here works with PAIR from the outside; none of it is part of PAIR.

| File | What it is |
|---|---|
| `pairctl.py` | Headless driver: runs PAIR's `nvpair-ui-broker` over stdio and relays its JSON-RPC on `127.0.0.1:17999`. Used on GPU nodes that have no desktop. `serve` / `call METHOD [PARAMS]`. |
| `taillog.py` | Prints the tail of an engine's log through that RPC. |
| `port-shift.patch`, `port-shift.sh` | Build-time port shift (+10000) for a PAIR that shares a machine with an SSH tunnel to a remote node. Applied to a copy of the source. |
| `peer-probe-shift.patch` | The matching change for a remote node that must probe a shifted peer (only for a two-way tunnel; not in use). |

The SGLang engine definition is not here: `bin/pair-node-setup` writes it as a PAIR user override file
(`<config>/engines/lmstudio.json`), which PAIR deep-merges onto its bundled manifest.
