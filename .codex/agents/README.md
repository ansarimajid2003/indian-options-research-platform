# Codex Agent Playbooks

These files replace the older `.claude/agents/` specs with Codex-native,
repo-current playbooks. They are not meant to be loaded all at once.

## Routing

- Use `researcher.md` before introducing a new strategy idea or citing market
  structure facts.
- Use `implementer.md` for code changes.
- Use `reviewer.md` after code changes and before expensive runs.
- Use `bias-auditor.md` before trusting any ledger, summary, or reported edge.
- Use `trade-analyzer.md` when a completed CSV ledger needs attribution.

## Token Discipline

- Read this README and one playbook only.
- Start from repo artifacts before broad search: manifests, reports, docs,
  tests, and the specific module being changed.
- Prefer exact file references, command lines, and concise verdicts over long
  explanation.
- If a playbook asks for evidence and the evidence is absent, say what is absent
  instead of filling the gap with assumptions.
