# Tools 链路修复总结

## 背景
- 目标：修复工具链中的高风险问题，重点解决配置失效、Wiki 串库检索、无效重试等问题。
- 范围：`llmServer/llmPolling.py`、`llmServer/tools.py`、`llmServer/promptTemplate.py`。

## 已完成项

### P0：正确性修复
- **布尔解析修复**
  - 在 `llmServer/llmPolling.py` 新增 `_parse_bool()`。
  - 替换 `ENABLE_WIKI_RETRIEVAL`、`METRICS_ENABLED` 等原先 `bool(...)` 解析。
  - 结果：`"false" / "0" / "off"` 可正确解析为 `False`。

- **Wiki 目标定位修复**
  - 新增 `_resolve_project_wiki_json_path()`。
  - 路径优先级：
    1. `PROJECT_WIKI_JSON_PATH` 显式配置；
    2. `wiki/<当前项目名>/PROJECT_WIKI.json`；
    3. 最后才 fallback 到首个目录并打印告警。
  - 结果：多项目 Wiki 共存时，检索串库风险显著降低。

- **工具重试机制修复**
  - 处理 `invalid_arguments` 时，不再同参重复执行。
  - 改为向模型回灌结构化错误，要求其修正参数再调用。
  - 结果：减少无效重试，提升参数错误场景下的恢复能力。

### P1：稳健性修复
- **长度语义拆分**
  - 保留 `RESPONSE_MAX_TOKENS_*` 控制模型生成长度。
  - 新增 `MAX_OUTPUT_CHARS_*` 控制后处理裁剪长度。
  - 结果：配置语义清晰，调参可预期。

- **工具解析兜底**
  - 增加 `<tools>` 格式错误兜底提示。
  - 增加每轮工具调用上限 `MAX_TOOLS_PER_ROUND`。
  - 统一工具返回结构（`ok/tool/error_type` 等）。
  - 结果：异常输出场景下轮转更稳定。

- **cmd 编码配置化**
  - `llmServer/tools.py` 中 `cmd()` 支持编码优先级与回退链：
    - 显式参数 > 环境变量 `RADISH_CMD_ENCODING` > 系统默认 > `utf-8` > `gbk`。
  - 结果：跨终端编码鲁棒性更好，中文输出乱码概率降低。

### P2：策略增强
- **tool_docs 软约束**
  - 记录本轮是否读取了对应工具文档。
  - 未读取时在工具结果中追加 warning（软提醒，不阻断）。
  - 结果：减少工具误用，保持主流程连续性。

## 同步更新
- `llmServer/promptTemplate.py` 增补工具规则：
  - `invalid_arguments` 后需修正参数重试；
  - `write_file` 使用 compact 格式优先。
- `llmServer/tools.py` 写入链路更新：
  - `write_file` 已直接切换到 v2 内核；
  - 禁用 `code_chunk` 输入，统一 `edits(JSON)`；
  - 返回契约统一为 `error_code/retryable/suggested_action/diagnostics`。

## 验证结果
- 语法检查：`compileall` 通过。
- Lint 检查：无错误。
- 布尔解析冒烟测试：
  - 输入 `false/0/off/true` 输出 `False/False/False/True`。

## 当前收益
- 配置项行为与预期对齐，避免隐式开关失效。
- Wiki 检索目标更准确，减少串库导致的回答偏差。
- 工具调用容错增强，减少无效轮次和死循环风险。

## 后续建议
- 增加配置示例（`config.yaml`）以显式声明新键默认值。
- 增加工具链回归脚本，覆盖以下场景：
  - 工具参数错误修复链路；
  - 多 Wiki 共存检索；
  - 编码回退链路；
  - tool_docs 软提醒触发。
