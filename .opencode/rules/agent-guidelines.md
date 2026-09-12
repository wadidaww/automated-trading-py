# Repo rules for OpenCode agents

## Required project context
- Read [AGENTS.md](AGENTS.md) before making changes when the task touches repo workflow, configuration, or execution assumptions.
- Treat [AGENTS.md](AGENTS.md) as the authoritative project guidance for repo conventions.

## Required behavior
- Prefer the smallest correct fix.
- Do not add unnecessary dependencies.
- Do not refactor unrelated code.
- Respect the event-driven architecture and stage boundaries.
- Keep imports package-qualified (`trader.*`).

## Validation
- Validate with the smallest relevant command.
- If direct invocation is needed, include `PYTHONPATH=src`.
- If the issue is environment-related, mention the environment constraint plainly.

## On uncertain tasks
- Investigate the Root cause before patching.
- Check existing tests and adjacent modules.
- Ask for clarification only when the requirement is ambiguous.
