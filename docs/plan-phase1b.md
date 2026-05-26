# Phase 1b: 跨文件评测执行计划

> **目标**: 设计实验证明 code graph 具备 flat 方法（TF-IDF / BM25 / Embedding）**不可替代的能力**
>
> **核心洞察**: RepoBench-R 的结果已证明，在同文件 snippet 检索场景下 flat 方法和 graph 差距不大。
> Graph 的真正价值在**跨文件调用链关系**——embedding 不可能回答"谁调用了 Config.get_nested"这类问题。

---

## 一、选型：评测项目（3 个）

选择标准：
- 中等规模（5k-50k LOC），有跨文件调用关系
- Python 生态中知名项目，审稿人熟悉
- 能用现有 code_graph 多语言解析器

| 项目 | 版本 | 规模 | 选择理由 |
|------|------|------|---------|
| **Flask** | 3.0.x | ~30k LOC | Web 框架，路由→视图函数→服务的调用链清晰 |
| **Click** | 8.1.x | ~15k LOC | CLI 框架，装饰器驱动的命令注册关系典型 |
| **Pydantic** | 2.x | ~40k LOC | 数据验证，类继承层次复杂，适合测 class hierarchy |

---

## 二、三类评测任务

### Task A：调用链检索

> "给定一个函数，找出项目中所有直接或间接调用它的函数"

**示例**：
```
查询: 在 Flask 中, Flask.run() 被哪些函数调用？
真实答案: main() → cli.run() → run_simple() → Flask.run()
                   ↓
              serve_development_server()

graph 方法:  ✅ 沿 caller 边向上遍历，返回完整路径
embedding:   ❌ "run"太常见，语义匹配不可能找到调用链
TF-IDF:      ❌ 同上
```

**指标**：

| 指标 | 定义 | 备注 |
|------|------|------|
| Coverage@k | 检索结果覆盖了多少比例的真实调用链节点 | Graph 应该 100%（精确），flat 无法完成 |
| 任务完成率 | 该方法是否能返回结构化调用路径 | 二元指标 |
| 检索时间 | 毫秒 | 次要指标 |

**数据收集方式**：
- 从每个项目的 code graph 中提取 20-30 条调用链（随机采样 mid-depth 节点）
- 构造查询：给出函数名 + 签名
- 对每个查询，让 A3/A4 分别检索，对比结果

---

### Task B：Ripple Effect 波及分析

> "如果修改函数 X 的行为，项目中还有哪些代码需要同步修改？"

与 Task A 的区别：Task A 只问"谁调用了 X"，Task B 问"X 的变化会影响到谁"——需要**传**调用链和多跳。

**示例**：
```
查询: "修改 Pydantic 的 BaseModel.model_dump() 的默认行为"
波及影响:
  1. BaseModel.model_dump_json() → 调用了 model_dump()
  2. TypeAdapter().dump_python() → 间接依赖 model_dump 的输出格式
  3. SerializeProtocol → 接口实现类需要同步修改

graph 方法:  ✅ 向下游（callee）展开 + 向上游（caller）反向传播
embedding:   ❌ "model_dump" 语义匹配只能找到命名相似的文件，
                但找不到 TypeAdapter 这种间接依赖
```

**指标**：

| 指标 | 定义 |
|------|------|
| Ripple Recall@k | 前 k 个检索结果中覆盖了多少波及节点 |
| 影响完整性 | 是否覆盖了所有层次的波及（直接调用者 + 间接调用者 + 接口实现） |
| 误报率 | 检索结果中无关代码的比例 |

**数据收集方式**：
- 从 code graph 中挑选 15-20 个"接口/核心函数"
- 人工标注它们的影响范围（直接 + 间接）
- 对比 graph vs. flat 的覆盖情况

---

### Task C：SymbolReadGate 模拟实验

> "在修改代码前，graph 强制要求 LLM 先读取相关 symbol 的所有邻居。
> 这是否能降低修改冲突？与不读直接改对比，错误率差异多大？"

注意：这个实验**不需要 LLM**，可以用确定性模拟来替代。

**模拟方案**：
```
1. 从 git 历史中提取真实 commit（选 20-30 个跨文件修改 commit）
2. 对每个 commit：
   a. 记录 "修改了哪些文件/函数"（ground truth）
   b. 用 graph 找出 "必须预读的上下游文件"（SymbolReadGate 要求的集合）
   c. 对比：如果只读了目标文件（模拟无 gate）vs. 读了全部上下游（有 gate）
      哪种策略能正确覆盖所有需要修改的文件？
3. 统计 gate 覆盖了多少本可能遗漏的修改
```

**指标**：

| 指标 | 定义 |
|------|------|
| 遗漏率 | 未预读的依赖文件中，有多少包含实际需要修改的代码 |
| 过度读取率 | gate 要求读但实际不需要修改的文件比例 |
| 平均读取量 | 每次修改需要预读的 symbol 数量 |

---

## 三、实验架构

### 数据准备

```
projects/                          # 新建，存放评测项目源码
├── flask/                         # git clone 到特定 tag
├── click/
└── pydantic/

eval2_crossfile/                   # 已有，扩展
├── build_projects.py              # 下载/更新 3 个项目
├── extract_call_chains.py         # 从 code_graph 提取调用链（Task A）
├── extract_ripples.py             # 提取波及范围（Task B）
├── extract_commits.py             # 从 git 提取真实 commit（Task C）
├── task_a_call_chain.py           # Task A 评测执行
├── task_b_ripple.py               # Task B 评测执行
├── task_c_read_gate.py            # Task C 模拟执行
└── report.py                      # 汇总三张结果表 + LaTeX 输出
```

### Baseline 对比方式

对于 Task A/B，flat 方法无法直接等价实现，对比策略：

| 方法 | 如何适配跨文件任务 |
|------|------------------|
| A3-Emb (CodeBERT) | 把整个代码库按函数分块编码 → 对 query 做语义检索 → 把 top-k 结果当"调用链" |
| A1-TFIDF | 同上，TF-IDF 检索 top-k |
| A4-Graph (完整) | 沿调用边精确遍历，返回 100% 精确结果 |
| **关键对比** | Embedding 只能凭名称相似度猜测（有上限），Graph 返回精确调用路径 |


## 四、预期结果

### Task A 预期

```
| 方法        | Coverage@1 | Coverage@5 | 任务完成率 | 延迟  |
|-------------|-----------|-----------|-----------|-------|
| A3-Emb      |  25-35%   |  40-55%   |  0% ❌    | 2s    |
| A4-Graph    | 100% ✅   | 100% ✅   | 100% ✅   | <5ms  |
```

关键结论：**Emb 无法完成跨文件调用链检索**——这不是速度问题，是能力天花板问题。

### Task B 预期

```
| 方法        | Ripple Recall@3 | 完整性 | 误报率 |
|-------------|----------------|--------|--------|
| A3-Emb      |  30-45%        | 低     | 高     |
| A4-Graph    | 90-100%        | 高     | 低     |
```

### Task C 预期

```
| 策略            | 遗漏率 | 过度读取率 | 平均读量  |
|----------------|--------|-----------|----------|
| 无 Gate（只读目标文件） | 40-60% | 0%       | 1.0      |
| 有 Gate（读全部上下游） | 5-15%  | 20-35%   | 5-8      |
```

---

## 五、执行步 Timeline

总时间：**2-3 周**（每天 2-3 小时）

```
Week 1: 项目 + 数据准备
├── Day 1: git clone 3 个项目 + 构建 code graph
├── Day 2-3: 实现调用链提取 + Task A 评测脚本
├── Day 4-5: 实现 Task A 对比评估 + 初步跑数

Week 2: Task B + Task C
├── Day 1-2: 实现波及分析提取 + Task B 评测
├── Day 3-4: 实现 commit 提取 + Task C 模拟
├── Day 5: Task B/C 跑数

Week 3: 汇总 + 论文框架
├── Day 1-2: 所有实验补跑 + 统计验证
├── Day 3-4: 结果分析 + 图表生成
├── Day 5: 整理成 paper-ready 格式
```

## 六、明天的第一步

```
1. 创建 projects/ 目录
2. git clone 三个项目（到特定稳定 tag）
3. 用现有的 code_graph/indexer.py 对每个项目构建 graph
4. 验证 graph 构建成功（节点数 > 100）
```

```bash
# 明天开始的命令
cd E:/Radish-Agent
mkdir -p projects
git clone https://github.com/pallets/flask.git projects/flask --branch 3.0.3 --depth 1
git clone https://github.com/pallets/click.git projects/click --branch 8.1.7 --depth 1
git clone https://github.com/pydantic/pydantic.git projects/pydantic --branch v2.9.0 --depth 1
```

完成这些后找我，我继续写评测代码。
