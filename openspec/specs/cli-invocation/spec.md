## Purpose

Define the argument vector and process environment for every `claude -p`
invocation this library makes. The library exists because six repos each
hand-rolled this contract and each got a different subset of it wrong; the
invariants below are the ones that were learned the hard way, and they are
enforced in exactly one place (`build_args`) so a consumer cannot re-break them.

## Requirements

### Requirement: The `--bare` flag is never emitted

`build_args` MUST NOT include `--bare` under any combination of arguments.
`--bare` forces `ANTHROPIC_API_KEY`-only mode and breaks the Claude Code
subscription/OAuth auth that every consumer relies on. There is no option,
keyword, or escape hatch that turns it on.

#### Scenario: No argument combination produces --bare

- **WHEN** `build_args` is called with any combination of model, effort, output
  format, tools, slash-command, setting-source and permission-mode values
- **THEN** `--bare` is absent from the returned argv
- **Anchor**: `tests/test_runner.py::test_build_args_never_passes_bare`

### Requirement: Runs are isolated from project configuration by default

Every invocation MUST default to a locked-down surface: `--strict-mcp-config`
pointed at a freshly written empty MCP config, `--setting-sources user`,
`--disable-slash-commands`, and `--no-session-persistence`. No project MCP
server, project setting, or slash command is reachable, and no session file is
left behind.

#### Scenario: Default argv carries every isolation flag

- **WHEN** `build_args` is called with defaults
- **THEN** the argv contains `--strict-mcp-config`, `--setting-sources user`,
  `--disable-slash-commands`, `--no-session-persistence` and `--mcp-config`
- **Anchor**: `tests/test_runner.py::test_build_args_isolation_flags`

#### Scenario: The MCP config declares zero servers

- **WHEN** a run is executed and the file passed to `--mcp-config` is read
- **THEN** its contents parse to `{"mcpServers": {}}`
- **Anchor**: `tests/test_runner.py::test_mcp_config_file_is_empty_servers`

### Requirement: Runs execute from a throwaway working directory

When no `cwd` is supplied, the subprocess MUST run inside a fresh temporary
directory so that no project `CLAUDE.md` is auto-discovered. The directory is
removed when the call returns.

#### Scenario: The prompt goes to stdin and the cwd is a scratch dir

- **WHEN** `run_claude` is called without `cwd`
- **THEN** the prompt is passed as the subprocess's stdin input and the
  subprocess `cwd` is a temporary directory, not the caller's directory
- **Anchor**: `tests/test_runner.py::test_run_claude_passes_prompt_and_scratch_cwd`

### Requirement: Project mode is opt-in and reverses the isolation defaults

Passing `cwd`, `slash_commands=True`, `setting_sources="user,project"` and
`permission_mode` runs a prompt inside a real project with its `CLAUDE.md` and
settings loaded, permitting file writes. This is a different trust posture, so none of it
MUST ever become a default: each of these stays off unless the caller asks.

#### Scenario: Slash commands and permission mode appear only when requested

- **WHEN** `build_args` is called with `slash_commands=True`,
  `setting_sources="user,project"` and a `permission_mode`
- **THEN** `--disable-slash-commands` is absent, `--setting-sources user,project`
  is present, and `--permission-mode <value>` is present
- **Anchor**: `tests/test_runner.py::test_build_args_project_mode_enables_slash_commands`

### Requirement: Tools are disabled by default and allow-listed when enabled

`tools` defaults to the empty string, meaning no tool use. A non-empty `tools`
value MUST be passed to BOTH `--tools` and `--allowed-tools`: headless `-p` mode
auto-denies any call that is not on the allow-list, so a tool that is enabled but
not allowed would stall or fail silently mid-run.

#### Scenario: A non-empty tools value reaches both flags

- **WHEN** `build_args` is called with a non-empty `tools` value
- **THEN** that value follows both `--tools` and `--allowed-tools` in the argv
- **Anchor**: `tests/test_runner.py::test_build_args_tools_are_also_allowed`

### Requirement: Stream output format implies verbose partial messages

When `output_format` is `stream-json`, the argv MUST also include `--verbose` and
`--include-partial-messages`. The CLI rejects `stream-json` without `--verbose`
in `-p` mode, and partial messages keep the stream flowing so a long run does not
look wedged.

#### Scenario: stream-json adds the verbose and partial-message flags

- **WHEN** `build_args` is called with `output_format="stream-json"`
- **THEN** the argv contains `--verbose` and `--include-partial-messages`
- **Anchor**: `tests/test_runner.py::test_build_args_stream_json_is_verbose`

### Requirement: Reasoning effort is optional

`effort` defaults to `medium` and is emitted as `--effort <value>`. Passing
`effort=None` MUST omit the flag entirely rather than sending an empty value.

#### Scenario: A None effort omits the flag

- **WHEN** `build_args` is called with `effort=None`
- **THEN** `--effort` is absent from the argv
- **Anchor**: `tests/test_runner.py::test_build_args_effort_optional`

### Requirement: CLI availability is reportable without invoking it

`claude_available()` MUST report whether the `claude` binary is on `PATH`
without spawning it, so callers can degrade to a non-LLM path instead of
catching an exception from a failed run.

#### Scenario: Availability follows PATH lookup

- **WHEN** the `claude` binary is resolvable on `PATH`
- **THEN** `claude_available()` returns `True`, and `False` when it is not
- **Anchor**: `tests/test_runner.py::test_claude_available`

### Requirement: A one-method provider seam allows swappable backends

`LLMProvider` MUST remain a runtime-checkable protocol with a single method,
`ask(prompt, *, model=None) -> str`. `ClaudeCliProvider` implements it over
`run_claude`. Its `DEFAULT_MODEL` and `DEFAULT_TIMEOUT` are class attributes so a
consumer pins its own defaults by subclassing one line each, with no `__init__`
override.

#### Scenario: The CLI provider satisfies the protocol structurally

- **WHEN** `isinstance(ClaudeCliProvider(), LLMProvider)` is evaluated
- **THEN** it is `True`
- **Anchor**: `tests/test_runner.py::test_provider_satisfies_protocol`

#### Scenario: ask returns the result text

- **WHEN** `ClaudeCliProvider().ask(prompt)` is called
- **THEN** it returns the `text` of the underlying `ClaudeResult`
- **Anchor**: `tests/test_runner.py::test_provider_ask`

#### Scenario: Subclass defaults override without an initializer

- **WHEN** a subclass sets `DEFAULT_MODEL` and `DEFAULT_TIMEOUT` as class
  attributes and is instantiated with no arguments
- **THEN** the instance uses those values for its model and timeout
- **Anchor**: `tests/test_cache.py::test_provider_class_level_defaults_are_overridable`
