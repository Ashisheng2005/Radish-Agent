# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# ========== Main Project ==========

# Install main dependencies
pip install -r requirements.txt
# Optional: tree-sitter backends for code graph (pip install -r requirements-code-graph.txt)

# Activate venv and start interactive REPL
.venv\Scripts\activate     # Windows
python llmServer/console.py

# Build code graph for current project (saves to wiki/<name>/CODE_GRAPH.json)
python -m llmServer.code_graph.build_index .

# Test flow: regression test runner (smoke → regression → destructive)
python llmServer/test_flow_cli.py list-cases
python llmServer/test_flow_cli.py run --group smoke
python llmServer/test_flow_cli.py run --group regression
python llmServer/test_flow_cli.py run --case <case_name>

# File write engine unit tests
cd RadishTools/src/FileExecutor/core
python -m pytest test_write_file_v2.py -v

# Python unit tests (no API key needed)
python -m unittest discover -s llmServer -p "test_*.py"

# Integration checks (no API)
python tests/integration/verify_console_graph.py
python tests/integration/verify_config_audit.py

# ========== Research Experiments ==========

.venv-research\Scripts\activate

# Run RepoBench-R retrieval evaluation (A1-A5 retrievers)
cd research && pip install -e . && cd ..
PYTHONPATH=research python -m eval.eval_retrieval --max-samples 200

# Verify local CodeBERT model
python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('research/models/codebert-base'); m.encode(['test'])"

# Clone projects for cross-file evaluation (Phase 1b)
mkdir -p projects
git clone https://github.com/pallets/flask.git projects/flask --branch 3.0.3 --depth 1
git clone https://github.com/pallets/click.git projects/click --branch 8.1.7 --depth 1
git clone https://github.com/pydantic/pydantic.git projects/pydantic --branch v2.9.0 --depth 1

# ========== Phase 1b Cross-file Evaluation ==========

.venv-research\Scripts\activate

# Run all three tasks of Phase 1b evaluation
python research/eval2_crossfile/task_a_call_chain.py   # Call chain retrieval
python research/eval2_crossfile/task_b_ripple.py       # Ripple effect analysis
python research/eval2_crossfile/task_c_read_gate.py    # SymbolReadGate simulation

# Generate summary report (markdown + LaTeX tables)
python research/eval2_crossfile/report.py

# ========== Paper Compilation ==========

cd research/paper
"E:/MikTex/miktex/bin/x64/pdflatex.exe" main.tex
"E:/MikTex/miktex/bin/x64/bibtex.exe" main
"E:/MikTex/miktex/bin/x64/pdflatex.exe" main.tex
"E:/MikTex/miktex/bin/x64/pdflatex.exe" main.tex

# Generate paper figures
python research/paper/figures/generate_figures.py

# ========== SWE-bench Lite (RQ4, WSL + Docker) ==========
# Full workflow: research/DEPLOYMENT.md, research/eval_swebench/README.md

# WSL: start Docker CE (each WSL restart)
sudo service docker start
# bash research/eval_swebench/start_docker_ce.sh

# Pre-clone bare caches + workspaces (reduces git clone failures)
python research/eval_swebench/preclone_workspaces.py \
  --dataset-path research/data/swebench_lite/test.jsonl \
  --missing-only --workers 6 --phase1-workers 2

# Agent predictions (requires config.yaml API key)
python research/eval_swebench/run_agent.py --variant B2 --agent --retry-failed \
  --dataset-path research/data/swebench_lite/test.jsonl \
  --max-instances 300 --max-tool-rounds 20 \
  --output research/eval_swebench/predictions/b2_full.jsonl

python research/eval_swebench/summarize_predictions.py \
  research/eval_swebench/predictions/b2_full.jsonl --errors-by-type \
  --export-patched research/eval_swebench/predictions/b2_full_patched_only.jsonl

# Pull per-instance GHCR eval images (CN: NJU mirror)
python research/eval_swebench/pull_eval_images.py \
  research/eval_swebench/predictions/b2_full_patched_only.jsonl --ghcr-mirror nju

# Harness (clears dead WSL proxy unless SWEBENCH_KEEP_PROXY=1)
bash research/eval_swebench/harness.sh \
  research/eval_swebench/predictions/b2_full_patched_only.jsonl 2 \
  research/data/swebench_lite/test.jsonl

python research/eval_swebench/summarize_harness.py \
  --run-id codegraph-YYYYMMDD --model radish-agent-B2 \
  --predictions research/eval_swebench/predictions/b2_full_patched_only.jsonl \
  --update-results
```

## Architecture

The system is an AI coding agent with five layers:

### 1. REPL Layer (`llmServer/console.py`)
`prompt_toolkit`-based interactive console with `/`-prefix command autocompletion, ANSI-colored prompt showing mode/debug/usage status, and fallback to plain `input()` when prompt_toolkit is unavailable.

### 2. Orchestrator (`llmServer/llmPolling.py`, ~1600 lines)
The `Polling` class manages multi-turn LLM conversations:

- **Prompt construction**: Freezes static instructions into one `system` message per mode (ask/plan/agent), wraps user query as short `[Task]\n{question}` to maximize prompt cache hit rates
- **Context management**: Sliding window of `history_limit * 2` messages, in-turn context slice freeze (prevents provider cache misses during tool rounds), session summary compression at turn boundaries
- **Tool execution loop**: Up to `MAX_TOOL_ROUNDS` (default 10) rounds, native `tool_calls` path via OpenAI-compatible API with XML `<tools>` fallback
- **Tool governance**: Duplicate call detection, `findstr`→`grep_code_batch` conversion, grep session budget (max 2/session), search streak detection (3 zero-result hints), evidence-based investigation termination
- **SWE-bench eval mode** (`Polling.swebench_eval = True`, set only by `research/eval_swebench/agent_runner.py`): Appends `swebenchEvalPrompt` to agent system prompt; investigation hints require `write_file`/`write_symbol` instead of text-only conclusions; does not affect normal `console.py` sessions
- **Metrics**: Writes round-level token/usage data to `runtime_metrics.jsonl`, reports cache hit rate, duplicate rate, format compliance

### 3. LLM Client (`llmServer/deepseek.py`)
Thin wrapper around `openai.OpenAI`. `sendinfo()` returns `(content, tool_calls, usage_dict)`. Uses `extra_body={"thinking": {"type": "disabled"}}` for thinking-model compatibility. Normalizes cache fields across providers. Unknown providers fall back to `DeepSeek` via `llmServer.get(name, DeepSeek)` — no code changes needed to add new OpenAI-compatible providers.

### 4. Tool Layer (`llmServer/tools.py`, 14 tools)
Tools are defined in 5 parallel dicts: `tools_func` (name→callable), `tools_docs` (name→docstring), `tools_title` (name→description), `tools_json` (OpenAI JSON Schema format), and exposed to the LLM:

| Category | Tools | Description |
|----------|-------|-------------|
| File I/O | `read_file`, `write_file`, `raw_write_file`, `list_dir`, `create_path_or_file` | Edit-based v2 write protocol with conflict detection, dry-run, structured errors |
| Shell | `cmd` | Command execution with multi-encoding fallback chain |
| Code Graph | `search_symbols`, `read_symbol`, `write_symbol`, `list_symbol_callers`, `list_module_importers` | Symbol-level access via code graph |
| Search | `grep_code`, `grep_code_batch` | Multi-pattern text search with presets |
| Meta | `tool_docs` | Returns other tools' documentation |

Sensitive file protection blocks `.env`, `config.yaml`, `*.key`, `*secret*`, `credentials.json` by default.

### 5. Code Graph (`llmServer/code_graph/`)
The core intellectual contribution — a static analysis index with:

- **Multi-tier parsing** (`extractors.py`, `parser.py`):
  1. Python AST (precise, zero dependencies)
  2. Tree-sitter (for JS/TS/Java/C#/C++, optional install)
  3. Regex fallback (when neither is available)

- **Data model** (`models.py`, `store.py`): `CodeNode` (SHA-1 node_id, qualified_name, kind, location, body_hash, caller/callee edges), `CodeEdge`, `CodeGraphIndex` (by_file, by_qualified_name, by_basename indexes). Saved to `wiki/<project>/CODE_GRAPH.json`.

- **Indexer** (`indexer.py`, `build_index.py`): Project-wide walk → extract symbols → resolve call edges via same-file, qualified-name, basename, and `__init__` heuristics. CLI: `python -m llmServer.code_graph.build_index <path>`.

- **SymbolReadGate** (`gate.py`): Session-scoped singleton that enforces `read_symbol` before `write_symbol` on the same symbol AND its graph neighbors. If `write_symbol` is called without prerequisite reads, returns `symbol_not_read` error with missing node IDs.

- **Symbol tools** (`symbol_tools.py`): `search_symbols` (keyword overlap scoring with hints), `read_symbol` (source + neighbor graph, gate recording), `write_symbol` (symbol-relative line conversion, gate enforcement), `grep_code` / `grep_code_batch` (filesystem walk, not delegating to OS grep).

### 6. Execution Engines (`RadishTools/src/`)
- **CmdExecutor**: Subprocess execution with multi-encoding fallback
- **FileExecutor**: v2 write engine (`write_v2/`) with edit protocol, conflict detection, atomic store via temp file + `os.replace`, typed error codes with retry hints. Legacy `WriteFile.py` kept for reference.

## Tool Contract

- Tool results: `{"ok": bool, "tool": str, ...}` with `error_type` on failure (`invalid_arguments`, `tool_not_found`, `sensitive_file_blocked`, `tool_runtime_error`)
- Write v2 errors: `error_code` + `retryable` (bool) + `suggested_action` + `diagnostics`
- Config access: `Polling._cfg("KEY", default=val, cast=int|bool)` in orchestrator methods

## Console Commands

- `/mode [ask|plan|agent|auto]` — Switch interaction mode
- `/clear` — Clear conversation history
- `/budget [per_round N] [rounds N] [reset]` — Tool call limits
- `/debug on|off` — Toggle debug logging
- `/usage on|off` — Show token usage including cache hit rate
- `/setup [refresh]` — LLM provider config wizard or refresh model cache
- `/switch [provider]` — Switch provider (interactive if no arg)
- `/graph build` — Build project code graph

## Research (`research/`)

Independent environment (`research/pyproject.toml`, `.venv-research/`) for experiments showing that code static graph enables cross-file understanding that flat retrieval (embedding/BM25) fundamentally cannot achieve.

### Phases

1. **Phase 1a (done)**: RepoBench-R baseline — intra-file retrieval accuracy & speed. Results in `research/results/eval_results.json`. Five retrievers (A1 TF-IDF through A5 Hybrid) compared on 200 Python samples.
2. **Phase 1b (done)**: Cross-file evaluation on Flask/Click/Pydantic — call chain retrieval (Task A), ripple effect (Task B), SymbolReadGate (Task C). Results in `research/eval2_crossfile/results_task_*.json`. Key finding: Graph achieves 92% coverage vs 14% for CodeBERT on cross-file retrieval.
3. **Phase 2 (RQ4, in progress)**: SWE-bench Lite end-to-end — agent predictions + Docker harness resolve rate. Infrastructure and agent optimizations are implemented; full 300-instance re-run is pending (see below).

### Paper (`research/paper/`)

LaTeX paper draft targeting ICPC/SANER 2027 or EMSE. Structure:
- `main.tex` + `sections/*.tex` (abstract through conclusion)
- `figures/fig[1-4]_*.pdf` — 4 publication-quality figures
- `bibliography.bib` — 17 references
- Compiles to 7-page PDF with `pdflatex` + `bibtex`

### Directory layout

```
research/
├── baseline_retrieval/     # A1-A5 retriever implementations
├── data/                   # RepoBench-R + swebench_lite/test.jsonl
├── eval/                   # Phase 1a eval scripts
├── eval2_crossfile/        # Phase 1b eval scripts (tasks A/B/C)
├── eval_swebench/          # Phase 2: SWE-bench Lite agent + harness (see below)
├── models/                 # CodeBERT model cache
├── projects/               # Flask/Click/Pydantic source repos
├── paper/                  # LaTeX paper draft + figures
├── DEPLOYMENT.md           # Windows + WSL full reproduction order
└── results/                # Phase 1a evaluation results
```

See `docs/research-roadmap.md` and `docs/plan-phase1b.md` for Phase 1 details. Phase 1 experiments are offline (no API keys). SWE-bench agent runs need `config.yaml`; harness needs WSL + Docker.

### SWE-bench Lite (`research/eval_swebench/`)

End-to-end RQ4 pipeline: clone repo at `base_commit` → Radish agent (`Polling`) → `git diff` patch → SWE-bench Docker harness → resolve rate.

| Component | File | Role |
|-----------|------|------|
| Agent batch | `run_agent.py` | `--agent`, `--resume`, `--retry-failed` (re-run empty patch / errors only) |
| Agent core | `agent_runner.py` | `_cache` bare clone + `--reference` workspace; tool whitelist patches **both** `tools` and `llmPolling`; tiered retry |
| Pre-clone | `preclone_workspaces.py` | Warm `workspaces/_cache/<repo>` and per-instance checkouts |
| Predictions stats | `summarize_predictions.py` | `--export-patched`, `--errors-by-type` (`git_clone`, `git_network`, …) |
| Images | `pull_eval_images.py` | Per-instance GHCR Epoch images; `--ghcr-mirror nju` for `ghcr.nju.edu.cn` |
| Harness | `harness.sh` | `swebench.harness.run_evaluation`; unsets dead proxy by default |
| Results | `summarize_harness.py` | Parse `logs/run_evaluation/<run_id>/<model>/*/report.json` → `results_swebench.json` |

**Agent variants (paper RQ4):**

| ID | Tools | Gate |
|----|-------|------|
| B0 | read/write/grep/list_dir/cmd | none |
| B1 | B0 + symbol graph tools | none |
| B2 | B1 | ASRG depth=2 (main comparison) |
| B3 | B2 + G2R context | ASRG depth=2 |

**SWE-bench agent optimizations** (via `swebench_eval` only):

- `promptTemplate.swebenchEvalPrompt`: minimal diff, preserve nested-function indentation, NumPy in-place (`arr[:] = …`), no diagnostic scripts / test edits
- Post-write `py_compile` on changed `.py` files; up to 2 syntax/indent retries with error feedback (empty patch: up to 3 `sendinfo` rounds)
- `run_radish_on_instance` returns `{patch, validation_errors}`; jsonl may include `validation_errors`

**Known pitfalls:**

- Tool whitelist must patch `llmServer.tools` **and** `llmServer.llmPolling` (`tools_func` / `tools_json`)
- `--resume` skips all existing rows; use `--retry-failed` to re-run only empty `model_patch`
- WSL harness: clear `HTTP_PROXY` if Clash is off (`harness.sh` does this unless `SWEBENCH_KEEP_PROXY=1`)
- GHCR eval images are per-instance; Hub mirrors do not apply — use `pull_eval_images.py --ghcr-mirror nju`

**Status (as of implementation):** Pilot harness 5 patched instances → 3/5 resolved (`run_id` `codegraph-20260526`). Full `b2_full.jsonl` had many `git clone` errors; use preclone + `--retry-failed` before harness. Logs: `logs/run_evaluation/<run_id>/radish-agent-B2/<instance_id>/report.json`.
