"""client — the Pi side, one per person.

Two things live here and nothing else:
  pi/    wiring for the Pi harness: provider config, settings, extensions (tools)
  web/   a personal web front-end served locally, talking to the cluster gateway

It never imports from cluster/. The only contract between the two systems is the
gateway URL (OpenAI-format HTTP).
"""

__version__ = "0.1.0"
