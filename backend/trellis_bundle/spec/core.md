# Shared engineering rules

- Trace a reported behavior to the relevant entry point, data flow, and concrete code before editing.
- Keep a written chain from observed symptom to suspected cause to changed lines and verification.
- Prefer the smallest change that addresses the cause. Preserve existing public contracts unless the Bug requires a contract change.
- Check callers and consumers across repository boundaries when a Bug spans multiple repositories.
- Give each selected repository a deterministic command that fails on the original commit for the reported reason and passes on the candidate.
- Run relevant project checks in addition to the reproduction command when the environment permits. Record commands that could not run.
- Do not hide a failing check by deleting it, weakening assertions, or changing the command between baseline and candidate.
- Follow the target repository's own AGENTS.md and committed specifications when they apply. This shared bundle supplements those files.
