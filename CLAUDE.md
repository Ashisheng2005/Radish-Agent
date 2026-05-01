# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install prompt-toolkit pyyaml openai requests

# Activate venv then start interactive console
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
python llmServer/console.py

# Or via shell script (Linux/Mac)
bash radish.sh

# List test cases
python llmServer/test_flow_cli.py list-cases

# Run tests by group (smoke → regression → destructive recommended)
python llmServer/test_flow_cli.py run --group smoke
python llmServer/test_flow_cli.py run --group regression
python llmServer/test_flow_cli.py run --group destructive

# Run a single test case
python llmServer/test_flow_cli.py run --case <case_name>

# Run with ad-hoc prompt (JSON output for scripting)
python llmServer/test_flow_cli.py run --prompt "your prompt"
python llmServer/test_flow_cli.py run --prompt "your prompt" --json

# Run write engine unit tests directly
cd RadishTools/src/FileExecutor/core
python -m pytest test_write_file_v2.py -v

# Check syntax of all modules
python -c "import ast; ast.parse(open('llmServer/llmPolling.py', encoding='utf-8').read()); print('OK')"
```

## Project Structure

```
E:\Radish-Agent\
├── config.yaml              # Model selection & API credentials (gitignored)
├── .env                     # Environment variables (gitignored)
├── llmServer/               # Main application
│   ├── console.py           # Interactive REPL entry point (prompt_toolkit)
│   ├── llmPolling.py        # Core orchestrator: message loop, tool execution, metrics
│   ├── deepseek.py          # OpenAI-compatible API client wrapper
│   ├── tools.py             # Tool definitions exposed to LLM + tools_json schemas
│   ├── pollTools.py         # Utility functions (intent detection, parsing, system info)
│   ├── promptTemplate.py    # System prompt templates (ask/plan/agent modes)
│   ├── yamlConfig.py        # YAML config loader (Config class)
│   ├── provider_setup.py    # Interactive LLM provider setup wizard
│   ├── test_flow_cli.py     # Test runner CLI
│   ├── test_cases.json      # Test case definitions (smoke/regression/destructive)
│   ├── models_dev_cache.json # Cached model database from models.dev (auto-generated)
│   └── CreateCodeNode.py    # Wiki/indexing related
├── RadishTools/src/         # Tool execution engines
│   ├── CmdExecutor/core/    # Shell command execution (subprocess)
│   └── FileExecutor/core/   # File operations
│       ├── WriteFileV2.py   # v2 write API (LLM-first, edit-based)
│       ├── write_v2/        # v2 write engine: models, protocol, service, conflict
│       ├── ReadFile.py      # File reading
│       ├── ListDir.py       # Directory listing
│       └── CreatePathOrFile.py
├── docs/                    # Design docs (token optimization, tool chain fixes)
├── plan/                    # Implementation plans
└── runtime_metrics.jsonl    # Interaction metrics (JSONL)
```

## Architecture

The system is an AI coding agent with layered architecture:

### Architecture Layers

1. **REPL Layer** (`console.py`) — `prompt_toolkit`-based interactive console with ANSI-colored prompt, mode indicator, `/`-prefixed command autocompletion (`CommandCompleter`), and commands (`/clear`, `/mode`, `/budget`, `/debug`, `/setup`, `/switch`). Gracefully falls back to plain `input()` when prompt_toolkit is unavailable.

2. **Orchestrator** (`llmPolling.py`) — The `Polling` class manages multi-turn LLM conversations:
   - Builds prompts from templates, caches system prompts for prompt caching
   - Receives native `tool_calls` from the API (with XML `<tools>` fallback)
   - Runs tool execution loop (up to `MAX_TOOL_ROUNDS` default 10)
   - Tracks token usage per round, compresses long context via summary
   - Writes interaction metrics to `runtime_metrics.jsonl`
   - Provides methods for provider switching: `get_max_token()`, `get_available_providers()`, `switch_provider()` — these also persist `MODEL_SELECT` to `config.yaml`

3. **LLM Client** (`deepseek.py`) — `DeepSeek` class wraps `openai.OpenAI`. `sendinfo()` accepts `messages`, `tools`, `tool_choice`, `temperature`, `max_tokens` and returns `(content, tool_calls, usage_dict)`. Uses `extra_body={"thinking": {"type": "disabled"}}` for thinking-model compatibility.

4. **Tool Layer** (`tools.py`) — 7 tools accessible to the LLM: `cmd`, `list_dir`, `read_file`, `write_file`, `raw_write_file`, `create_path_or_file`, `tool_docs`. Tools are exposed to the API via `tools_json` (OpenAI-compatible JSON Schema format). Defined in parallel dicts: `tools_func` (name→callable), `tools_docs` (name→docstring).

5. **Execution Engines** (`RadishTools/`) — Actual file I/O and command execution implementation. `write_file` uses a v2 edit-based protocol with `edits(JSON)`, supporting `dry_run`, `return_patch`, `conflict_mode` (strict/soft), and structured error responses.

6. **Provider Setup** (`provider_setup.py`) — Interactive wizard for configuring LLM providers. Features:
   - 6 known providers + custom URL option
   - Fetches model list via OpenAI-compatible models API
   - Fetches context window size from `models.dev/api.json` (with local caching)
   - Persists `MAX_TOKEN`, `API_KEY`, `BASE_URL`, `MODEL` to `config.yaml`
   - Three-tier context window lookup: hardcoded map → models.dev cache → manual input (default 128000)

### Tool System

- Tools are defined in `tools.py` with `tools_func` (name→callable), `tools_docs` (name→docstring), `tools_title` (name→short description)
- Tool schemas for the API are in `tools_json` (OpenAI `functions` format, 7 tools with full JSON Schema parameters)
- Tools are passed to the API via the `tools` parameter; native `tool_calls` are the primary path
- XML `<tools>name(args)</tools>` format is kept as fallback for backward compatibility
- Two execution paths: `_run_tool()` (XML args via `ast.literal_eval`) and `_run_tool_from_native()` (native JSON dict args)
- Tool results use `role: "tool"` for native calls, `role: "user"` for XML fallback calls
- `tool_docs` is a meta-tool that returns other tools' documentation

### Console Commands

- `/help` — Available commands
- `/mode [ask|plan|agent|auto]` — Switch interaction mode (auto = automatic routing)
- `/clear` — Clear conversation history
- `/budget [per_round N] [rounds N] [reset]` — Adjust tool call limits
- `/debug on|off` — Toggle debug logging
- `/usage on|off` — Show token usage statistics
- `/setup [refresh]` — Open LLM provider config wizard, or refresh models.dev cache
- `/switch [提供商名]` — Switch to another configured provider (interactive if no arg)

### LLM Client Registry

`llmPolling.py` has a `llmServer` dict mapping provider names to client classes:
```python
llmServer = {'deepseek': DeepSeek}
```
On startup `Polling.__init__` reads `MODEL_SELECT.model_name` from config, looks up the class in `llmServer`, and falls back to `DeepSeek` via `llmServer.get(llm, DeepSeek)` for any provider not explicitly registered. This means new OpenAI-compatible providers work without code changes.

## Configuration

Configuration is in `config.yaml` (gitignored). Model settings are nested under `MODEL_SELECT.model_name`:

```yaml
MODEL_SELECT:
  model_name: "MiMo"
MiMo:
  API_KEY: "sk-..."
  BASE_URL: "https://token-plan-cn.xiaomimimo.com/v1"
  MAX_TOKEN: 1048576
  MODEL: "mimo-v2.5-pro"
  LANGUAGE: "Chinese"
```

Additional settings per-provider read from config via the `_cfg(key, default, cast)` helper:
- `MAX_TOKEN` — Context window size (formatted as K/M in console title bar)
- `RESPONSE_MAX_TOKENS_QA/TOOL/CODE/LARGE_WRITE` — Per-profile response token limits
- `MAX_OUTPUT_CHARS_QA/TOOL/CODE` — Max output characters per profile
- `MAX_TOOLS_PER_ROUND`, `MAX_TOOL_ROUNDS`, `TOOL_RETRY_LIMIT` — Tool call budgets
- `READ_FILE_ALLOWLIST` — Paths allowed to bypass sensitive file check
- `ENABLE_WIKI_RETRIEVAL`, `WIKI_MODE` — Wiki context retrieval settings
- `TOOL_RESULT_MAX_CHARS` / per-tool overrides — Max chars in tool results

## Key Patterns

- **Tool result format**: Always `{"ok": bool, "tool": str, ...}`. Errors include `error_type` (`invalid_arguments`, `tool_not_found`, `sensitive_file_blocked`, `tool_runtime_error`).
- **Context management**: `Polling.context` stores conversation history. When exceeding `history_limit * 2`, older messages are summarized into `context_summary`.
- **Response profiles**: `_choose_response_profile()` selects token limits based on prompt keywords: `large_write`, `code`, `tool`, or `qa` (default).
- **Metrics**: Every interaction round records tokens, tool calls, and errors to `runtime_metrics.jsonl`.
- **Config access**: Use `self._cfg("KEY", default=val, cast=int|bool)` in `Polling` methods instead of `self.config.get_nested(...)`.
- **Provider fallback**: `llmServer.get(provider_name, DeepSeek)` allows unregistered OpenAI-compatible providers to work out of the box.
- **Context window lookup**: Three-tier fallback in `provider_setup.py`: hardcoded `MODEL_CONTEXT_MAP` → local `models_dev_cache.json` → manual input.
