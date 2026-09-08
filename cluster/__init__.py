"""cluster — the inference side: nodes, router config, control plane, admin console.

Layers (see docs/design.md):
  access/   管理台  cluster admin console (status, devices, node registration endpoint)
  router/   调度层  Switchyard route table generation, gateway process
  node/     资源层  hardware probe, preset parsing, engine processes
  control/  控制平面 node registry, health, watchdog, heal
"""

__version__ = "0.2.0"
