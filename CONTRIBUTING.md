# Contributing

Bug reports and pull requests are welcome.

## Set up

```bash
git clone https://github.com/octrow/claude-cli.git
cd claude-cli
uv sync --all-extras
uv run pytest
```

## Rules for a change

- Stdlib only: don't add a runtime dependency for something a few lines can do.
- A fix or new behavior comes with a test.
- Behavior changes should update the matching spec in `openspec/specs/`.
- Commit messages: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`).
