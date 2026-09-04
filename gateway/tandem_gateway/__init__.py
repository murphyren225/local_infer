"""Tandem Gateway — smart routing between a small and a large local LLM.

Keep this module import-light: pure-logic submodules (router, cache,
escalation) must be importable without FastAPI/httpx installed, so that
offline evals and unit tests can run anywhere.
"""

__version__ = "0.1.0"
