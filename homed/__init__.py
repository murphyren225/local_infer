"""homed — cluster control plane and node tooling.

Layers (see docs/design.md):
  access/   接入层  Pi provider config, web console
  router/   调度层  Switchyard route table generation, gateway process
  node/     资源层  hardware probe, preset parsing, engine processes
  control/  控制平面 node registry, health, watchdog, heal
"""

__version__ = "0.2.0"
