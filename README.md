# RadishWeixinBot
开发中。。。。

## Tooling 现状（2026-04）

- `write_file` 已在 `llmServer/tools.py` 中直接切换到 LLM-first v2 内核。
- `write_file` 主协议为 `edits(JSON)`，兼容紧凑字段 `op/s/e/t`。
- 不再支持 `code_chunk` 写入协议；传入时会返回结构化 `invalid_arguments` 错误并给出 `suggested_action`。
- 新内核支持 `dry_run`、`return_patch`、`conflict_mode`、`request_id`，便于模型自恢复重试和可观测。

### write_file 推荐调用示例

```python
write_file(
    "./main.py",
    edits=[
        {"op": "replace", "s": 3, "e": 4, "t": "for i in range(5):\n    print(i)"}
    ],
)
```

### write_file 典型返回结构

```json
{
  "ok": false,
  "file": "./main.py",
  "applied": 0,
  "error_code": "invalid_arguments",
  "error": "...",
  "retryable": true,
  "suggested_action": "use_edits_json_then_retry",
  "diagnostics": []
}
```

## llmServer 回归测试用例

测试用例定义在 [llmServer/test_cases.json](llmServer/test_cases.json)，并支持按分组执行：

- smoke：基础链路快速冒烟
- regression：功能回归与策略增强场景
- destructive：错误输入与失败路径校验

推荐执行顺序：

1. smoke
2. regression
3. destructive

示例命令：

```bash
/home/repork/project/RadishWeixinBot/.venv/bin/python llmServer/test_flow_cli.py list-cases
/home/repork/project/RadishWeixinBot/.venv/bin/python llmServer/test_flow_cli.py run --group smoke
/home/repork/project/RadishWeixinBot/.venv/bin/python llmServer/test_flow_cli.py run --group regression
/home/repork/project/RadishWeixinBot/.venv/bin/python llmServer/test_flow_cli.py run --group destructive
```