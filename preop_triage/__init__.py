"""Hybrid rules-plus-LLM pre-op scheduling triage (see DESIGN.md).

Modules, in data-flow order:

- ``settings``: mode flags (offline, no-cache, force-LLM) from the environment.
- ``normalize``: date parsing, lab-code aliasing, document-title normalization.
- ``classify``: three-outcome title classifier and keyword text judgments.
- ``rules``: missing-data gates and policy rules 1 through 4.
- ``llm``: the narrowly scoped LLM fallback (client, cache, strict schema).
- ``engine``: the orchestrator behind ``core.triage_submission``.
"""
