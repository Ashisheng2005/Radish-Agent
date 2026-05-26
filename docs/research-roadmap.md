# Research Roadmap: Graph-Driven LLM Code Agent

> **出发方向**: 利用代码静态图（call graph + symbol graph）结构，提高 LLM 对项目的**检索速度**、**修改质量**和**整体效率**。
>
> **执行策略**: 分两阶段。第一阶段（RepoBench 检索实验）无需 Docker，快速验证 graph 在代码检索上的优势；第二阶段（SWE-bench 修改实验）补全修改质量验证。

---

## 一、核心研究问题

论文最终要回答以下三个问题（第一阶段聚焦 RQ1，第二阶段补全 RQ2/RQ3）：

| 编号 | 研究问题 | 所属阶段 |
|------|---------|---------|
| **RQ1** | 代码静态图是否能显著提升 LLM 在代码检索中的**召回率**和**速度**，相比于纯文本/embedding 基线？ | **Phase 1** |
| **RQ2** | 基于图拓扑的 "read-before-write" （SymbolReadGate）是否能降低代码修改的**冲突率和错误率**？ | Phase 2 |
| **RQ3** | 符号级抽象（symbol-level tools）比文件级抽象（file-level tools）是否能减少 LLM 的**工具调用轮数和 token 消耗**？ | Phase 2 |

---

## 二、Phase 1: RepoBench 检索实验（2-3 个月，无需 Docker）

### 2.1 为什么先做 RepoBench

| 因素 | RepoBench | SWE-bench |
|------|-----------|-----------|
| 环境要求 | 纯 Python + JSON 数据集 | Docker + GPU 服务器 |
| 部署时间 | 1 周 | 2-4 周 |
| 运行时间 | 几分钟跑完全部评估 | 数小时到数天 |
| 可重复性 | 高（无随机因素） | 中（LLM 采样方差） |
| 能回答的问题 | 检索精度对比 | 修改质量对比 |

### 2.2 RepoBench 简介

RepoBench（Liu et al., EMNLP 2023）是 repository-level 代码理解的评测基准，包含三个 track：

| Track | 任务 | 指标 | 我们用什么 |
|-------|------|------|-----------|
| **RepoBench-R** | 给定目标函数描述，从仓库中检索最相关的代码片段 | Recall@k, MRR | **主要实验** |
| RepoBench-P | 给定上下文，预测下一行代码 | Edit similarity | 辅助（暂不做） |
| RepoBench-XML | 跨文件上下文检索 | Accuracy | 辅助（暂不做） |

RepoBench-R 最核心的设定：
- 输入：一个目标函数的签名 + 文档字符串（例如 `def get_user_by_id(user_id: int) -> User`）
- 任务：从该仓库的其他文件中检索出与此函数最相关的 k 个代码片段
- Ground truth：人工标注或从 git 历史中提取的跨文件引用
- 数据规模：涵盖 Python、Java、Ruby、JavaScript 等多种语言

### 2.3 Baseline 设计与实验组

#### 五组对照（A1-A5）

| 编号 | 配置 | 检索方式 | 检索对象 | 目标 |
|------|------|---------|---------|------|
| **A1** | 纯文本检索（TF-IDF） | TF-IDF 关键词匹配 | 所有文件全部行 | 最朴素基线 |
| **A2** | BM25 稀疏检索 | BM25（pyserini / whoosh） | 按文件分片（chunk）索引 | 经典非神经检索基线 |
| **A3** | Embedding 密集检索 | CodeBERT / UniXCoder 向量相似度 | 按函数/类分片索引 | 语义检索基线（SOTA） |
| **A4** | **Graph 检索（你的方法）** | Call graph + Symbol 匹配 | symbol-level 节点 | **实验组** |
| **A5** | Graph + Embedding 混合 | Graph 结构排序 + Emb 语义补充 | graph 节点 + 语义补充 | 探索性混合方法 |

每个配置在 RepoBench-R 的指标上对比，包括：
- Recall@1 / Recall@5 / Recall@10
- MRR（Mean Reciprocal Rank）
- 平均检索延迟（ms/query）

#### 关键问题：你的 graph 检索怎么跑

**Graph 检索的逻辑（A4）：**

给定目标函数描述（比如 `get_user`），graph 检索的返回逻辑是：

```
Step 1: 用函数名关键词在 graph node 的 qualified_name 上做前缀/模糊匹配
Step 2: 如果有匹配，返回命中的 node + 它的 caller/callee neighbor 的 context
Step 3: 如果 Step 1 无结果，fallback 到基于 node.summary（函数摘要）的关键词匹配
Step 4: 利用图连接给出"二阶关联"结果（A 调用 B，B 调用 C，所以 C 也与查询相关）
```

这就是 graph 区别于 flat retrieval 的独特之处：**可沿调用链传播检索范围**。

### 2.4 需要额外的工程工作

```
baseline_retrieval/              # 新建
├── repobench_loader.py          # 下载并解析 RepoBench 数据集
├── corpus_builder.py            # 从代码库构建检索语料库
├── a1_tfidf_retriever.py        # A1: TF-IDF 基线
├── a2_bm25_retriever.py         # A2: BM25 基线
├── a3_embedding_retriever.py    # A3: CodeBERT/UniXCoder 嵌入基线
├── a4_graph_retriever.py        # A4: 你的 graph 检索核心
├── a5_hybrid_retriever.py       # A5: Graph + Embedding 混合
└── eval_retrieval.py            # 统一的评测脚本：Recall / MRR

code_graph/                      # 现有，需要扩展
├── retrieval.py  ← NEW         # 新增 graph 检索接口
│                               # 包含 call-graph based ranking
│                               # 包含 neighbor propagation 策略
```

#### A4 Graph 检索器的核心逻辑（需实现）

```python
# retrieval.py 的核心接口
class CodeGraphRetriever:
    def retrieve(self, query: str, top_k: int = 10) -> List[ScoredNode]:
        """
        1. keyword match on node.qualified_name
        2. keyword match on node.summary  
        3. expand to neighbors (callers + callees)
        4. rank by score
        """
        
    def retrieve_with_graph_propagation(self, query: str, top_k: int = 10, depth: int = 1) -> List[ScoredNode]:
        """
        1. 先做候选节点匹配
        2. 对每个候选项展开 depth 层 caller/callee
        3. 用 PageRank-like 传播分数
        4. 返回 top_k
        """
```

### 2.5 Phase 1 实验指标详解

| 指标 | 定义 | 为什么重要 |
|------|------|-----------|
| **Recall@k** | 在前 k 个检索结果中，包含 ground truth 相关代码的比例 | 直接衡量检索系统是否"找得到" |
| **MRR** | 第一个相关结果的排名倒数 | 衡量检索结果的排序质量 |
| **精确匹配率** | 检索到的节点名与 ground truth 完全一致的比例 | 衡量符号级别对齐程度 |
| **检索延迟** | 平均每个查询的检索时间（毫秒） | 速度对比（graph vs. 密集向量） |
| **索引大小** | 索引占用的磁盘空间 | 可扩展性对比 |

### 2.6 Phase 1 预期实验产出

```
实验 1: 检索精度总表（Recall@k / MRR）
=========================================
| 方法       | Recall@1 | Recall@5 | Recall@10 | MRR    |
|------------|----------|----------|-----------|--------|
| A1 TF-IDF  |  12.3%   |  28.7%   |  38.2%    | 0.195  |
| A2 BM25    |  15.8%   |  34.2%   |  45.6%    | 0.234  |
| A3 Emb     |  22.1%   |  41.5%   |  53.8%    | 0.312  |
| A4 Graph   |  28.6%   |  48.3%   |  61.2%    | 0.385  |
| A5 Hybrid  |  31.2%   |  52.7%   |  65.4%    | 0.412  |

实验 2: 按查询类型的精度细分
=========================================
| 查询类型       | A2 BM25 | A3 Emb | A4 Graph | A5 Hybrid |
|----------------|---------|--------|----------|-----------|
| 函数调用检索   |  21.3%  | 28.1%  |  41.2%   |  43.8%    |
| 类继承检索     |  18.7%  | 25.4%  |  35.6%   |  38.1%    |
| 变量使用检索   |  32.1%  | 38.5%  |  29.4%   |  40.2%    |

实验 3: 检索速度对比
=========================================
| 方法       | 平均延迟/查询 | 索引大小 (10k files) |
|------------|--------------|---------------------|
| A2 BM25    |   2.3 ms     |  12 MB              |
| A3 Emb     |  15.8 ms     |  240 MB             |
| A4 Graph   |   1.2 ms     |  4.5 MB             |

实验 4: 消融实验
=========================================
| Graph 配置变体                 | Recall@5 | 相对 A4 变化 |
|-------------------------------|----------|-------------|
| A4 完整 Graph                  |  48.3%   |   —         |
| A4 - neighbor propagation     |  41.2%   |  -7.1%      |
| A4 - name matching            |  35.8%   | -12.5%      |
| A4 - summary matching         |  43.1%   |  -5.2%      |
```

---

## 三、Phase 2: SWE-bench 修改实验（2-3 个月，需要 Docker）

### 3.1 需要在 Phase 1 基础上增加的

| 新增模块 | 说明 |
|---------|------|
| SWE-bench Lite 数据集下载 | `pip install swebench` + `swebench-download` |
| Task → Prompt 适配器 | 将 issue 描述转为 agent 提示 |
| Patch 生成管线 | agent 输出 → 标准化 patch |
| Docker 验证环境 | swebench harness 执行验证 |
| 冲突率统计 | git merge 级别的冲突检测 |

### 3.2 Phase 2 实验矩阵

| 实验 | 对比组 | 指标 |
|------|--------|------|
| SWE-bench 解决率 | A1 vs A2 vs A3 vs A4 vs A5 | % resolved (pass@1) |
| Token 消耗对比 | A1 vs A4 | avg prompt/completion tokens |
| 工具调用轮数 | A1 vs A4 | avg tool rounds |
| 修改冲突率 | A4 vs A5 (去掉 SymbolReadGate) | conflict rate |
| 端到端耗时 | A1 vs A4 | avg completion time |

---

## 四、论文大纲（Phase 1 完成后可先写一版）

如果只完成 Phase 1（RepoBench 检索），可以支撑论文的 60%：

```latex
\title{CodeGraphRetrieval: Leveraging Static Call Graphs for 
       Repository-Level Code Retrieval}

\begin{abstract}
Code retrieval is crucial for LLM-based coding agents...
We propose a graph-based retrieval method that leverages...
On RepoBench-R, our approach achieves ... improvement over...
\end{abstract}

\section{Introduction}
\section{Related Work}
\section{Method: Graph-Based Code Retrieval}
  \subsection{Static Code Graph Construction}
  \subsection{Graph-Based Retrieval with Neighbor Propagation}
  \subsection{Integration with LLM Agent}
\section{Experimental Setup}
  \subsection{Dataset: RepoBench-R}
  \subsection{Baselines}
  \subsection{Implementation Details}
\section{Results} % ← Phase 1 数据填这里
  \subsection{Retrieval Accuracy (RQ1)}
  \subsection{Retrieval Speed (RQ1)}
  \subsection{Ablation Study}
  \subsection{Qualitative Analysis}
\section{Discussion}
  \subsection{Limitations}
  \subsection{Threats to Validity}
\section{Conclusion and Future Work}
% Phase 2 数据可以留作 future work 或 journal extension
```

Phase 2 数据完成后，可以扩展为 journal version 投 JSS/EMSE。

---

## 五、投稿策略

### 策略 A：检索论文先发（推荐）

```
Phase 1 完成（约 2-3 个月）
        ↓
投 ICPC 2027 / SANER 2027（CCF-B，检索方向会议）
或投 EMSE / JSS（第一次投稿作为期刊 short paper）
        ↓
Phase 2 完成（约 +2-3 个月）
        ↓
扩展为 journal full version 投 JSS / EMSE
```

### 策略 B：等全部做完再投

```
Phase 1 + Phase 2 全部完成（约 5-6 个月）
        ↓
直接投 ASE 2027 / ICSE 2027 SEIP
或 投 JSS / EMSE full paper
```

### 建议

走 **策略 A**。原因：
1. Phase 1 的 RepoBench 检索实验**门槛低、周期短**，适合快速验证"graph 是否有效"这个核心假设
2. 即使 graph 效果不好，也能及时止损调整方向，而不是投入半年才发现行不通
3. 先发一篇 short paper 积累审稿反馈，对后续 journal extension 非常有利
4. Phase 1 不需要 Docker，你现在的开发机器就能跑

---

## 六、策略调整说明（2025-05-25）

### 发现的问题
RepoBench-R 的候选池局限在同文件 snippets（平均 5-7 个），导致：
- Recall@10 对所有方法都是 100%（无区分度）
- Graph 的跨文件调用链优势完全无法体现
- "速度快"不足以成为独立发表贡献

### 修正后的方向
从"graph vs. flat 速度精度对比"转向 **"graph 能做 flat 根本做不到的事"**：

| 旧方向 | 新方向 |
|--------|--------|
| "Graph 比 embedding 更快" | "Graph 能实现跨文件调用链检索，embedding 做不到" |
| "Recall@k 精度对比" | "Ripple effect 波及分析 + SymbolReadGate 修改保护" |
| RepoBench-R 单文件候选池 | 自有数据集+跨文件任务 |

修正后方案变为三个子阶段：

```
Phase 1a: RepoBench-R baseline（进行中）
  → 证明 graph 在同文件检索中与 embedding 精度持平但更快
  → 200 条样本运行中，预计 Recall@1 接近 A3-Emb

Phase 1b: 跨文件评测（下一阶段）
  → 选 3-5 个开源项目，构建完整代码图
  → 三类任务：调用链检索 / 波及分析 / 修改保护
  → 重点展示 graph 的不可替代性

Phase 2: SWE-bench 端到端修改（后续）
  → 完整 agent 级对比
```

## 七、里程碑与 Timeline
├── 第 2 个月: 数据采集
│   ├── W1-W2: 跑全部 5 组 × 多语言数据集
│   ├── W3: 图表制作 + 统计分析
│   └── W4: 论文初稿（Related Work + Method + Results）
├── 第 3 个月: 修改 + 投稿
│   ├── W1-W2: 内部修改 + 补充实验
│   ├── W3: 格式调整 + 终稿
│   └── W4: 投稿（ICPC / SANER 或 EMSE short paper）

Phase 2: SWE-bench 修改实验（2-3 个月，可选）
├── 第 1 个月: Docker 环境搭建 + 实验管线
├── 第 2 个月: 数据采集
└── 第 3 个月: Journal extension 撰写
```

---

## 七、Phase 1 检查清单（To-Do，按优先级排列）

### P0 — 必须做（影响论文发表与否）

- [ ] **1. 实现 `code_graph/retrieval.py`（Graph 检索器核心）**
  - 关键词在 node.qualified_name 上的匹配
  - 关键词在 node.summary 上的匹配
  - neighbor propagation（caller/callee 展开）
  - 排序打分函数
- [ ] **2. 下载并解析 RepoBench-R 数据集**
  - Python 子集（代码检索最相关的语言）
  - 理解 ground truth 的格式
- [ ] **3. 实现 A1（TF-IDF）基线**
- [ ] **4. 实现 A2（BM25）基线**
- [ ] **5. 实现 A3（Embedding）基线**
  - 使用 CodeBERT（codebert-base）或 UniXCoder（microsoft/unixcoder-base-nine）
- [ ] **6. 实现统一评测脚本**

### P1 — 应该做（显著提升论文质量）

- [ ] 实现 A5（Graph + Embedding 混合）探索性方法
- [ ] 消融实验（neighbor propagation / name matching / summary matching）
- [ ] 按检索类型细分（函数调用 vs. 类继承 vs. 变量使用）
- [ ] 检索延迟对比
- [ ] 多语言扩展性测试（除 Python 外再跑一种语言）

### P2 — 可做可不做（锦上添花）

- [ ] 索引大小对比
- [ ] 在 RepoBench-P（代码补全）上验证 graph 检索的实际效果
- [ ] 可视化分析（graph 检索 vs. 向量检索的 case study）
- [ ] 与现有检索工具（如 GitHub Code Search、Sourcegraph）的感性对比

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解方案 |
|------|------|------|---------|
| Graph 检索效果不如 BM25 | 低 | 高 | 加强 neighbor propagation，加入语义 fallback；如果 graph 单独不行，A5 Hybrid 保底 |
| RepoBench 数据文件格式不兼容 | 中 | 中 | 数据解析部分预留 1 周缓冲 |
| CodeBERT 模型下载慢 | 高 | 低 | 首次加载后缓存到本地；用 Hugging Face 镜像加速 |
| Graph 在大项目上构建慢 | 中 | 中 | RepoBench 的仓库规模有限（<500 files），不会成为瓶颈；可扩展性留作 future work |
| 检索精度差异不显著 | 中 | 高 | 增大消融实验维度（展开深度、匹配策略），找到 graph 确实有效的场景 |

---

## 九、实验环境需求

| 资源 | Phase 1 需求 | Phase 2 额外需求 |
|------|-------------|-----------------|
| CPU | 已有机器即可 | 同左 |
| RAM | 16GB+（embedding 索引需要） | 同左 |
| GPU | 可选（emb 检索用 GPU 快，CPU 也能跑） | 需要（LLM 推理） |
| 磁盘 | 10GB（数据集 + 索引） | 50GB+（Docker 镜像） |
| Docker | 不需要 | 需要 |
| Python | 3.9+ | 同左 |
| API 成本 | $0（离线评估，不需要 LLM） | $200-500（SWE-bench LLM 推理） |

**Phase 1 完全不依赖 LLM API 调用**，纯离线评估。你的开发机足够跑全部实验。

---

## 十、立即开始的第一步

```bash
# 1. 安装依赖
pip install swebench datasets sentence-transformers scikit-learn
pip install pyserini  # BM25（Java 依赖，可选 whoosh 替代）

# 2. 下载 RepoBench
python -c "
from datasets import load_dataset
dataset = load_dataset('repo-bench/RepoBench', 'repo_bench_r_python')
# 检查数据结构
print(dataset['train'][0].keys())
"

# 3. 开始实现 code_graph/retrieval.py
# 4. 实现 baseline_retrieval/ 目录下的基线
# 5. 跑 eval_retrieval.py 看初步结果
```

---

> **一句话总结**: 从现在开始，先花 1 周搭建 RepoBench 实验管线，再花 2 周实现 graph 检索器 + 3 个基线，1 个月后你就能看到第一组对比数据。如果 graph 在 Recall@k 上的优势显著（>5%），论文核心假设就成立了。
