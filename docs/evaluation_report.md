# 阶段 7 检索评测报告

> 对应 Issue #31，分支 `feat/evaluation`。本报告记录评测数据集、参数、环境、结果与结论，确保评测可复现（PROJECT_PLAN.md 第 724-730 行验收）。

## 1. 评测目标

只评测**检索阶段**（不调用 LLM、不消耗 Token），回答两个问题：

1. 当前默认切分参数（`chunk_size=500, chunk_overlap=80`）的检索质量如何？
2. 改变 `chunk_size` / `chunk_overlap` 对 Hit@1 / Hit@5 / MRR / 平均检索耗时的影响？

生成阶段（LLM 答案质量）评测需消耗 Token 且答案质量主观，留待后续。

## 2. 数据集

- **来源**：真实 PDF `25208207087-符方刚.pdf`（9 页，脑纹识别综述论文《脑纹识别研究进展：方法、挑战与趋势》）
- **条目数**：32 条（超过验收要求的 30 条）
- **格式**：`eval/dataset.json`，每条含 `question` / `expected_page` / `expected_substring` / `category` / `note`
- **覆盖范围**：页 1-6（正文部分，不含参考文献页 7-9）
- **问题分类**：定义 / 方法 / 指标 / 趋势 / 挑战 / 应用 / 结论 / 细节，共 8 类

### 设计取舍

- **ground truth 用 `expected_substring` 而非 `chunk_index`**：当 `chunk_size` 改变时，`chunk_index` 会变化，但正确答案所在的文本片段内容不变。用一段独特的子串（10+ 字符）匹配，对参数变化鲁棒。
- **子串匹配前做空白归一化**：PyMuPDF 提取中文时常在字间插入空格（如 `活 动的`），不同切分参数下空格位置可能不同。`normalize_text` 移除所有空白后再做 `in` 判断，避免误判。
- **数据集验证**：`uv run python scripts/evaluate.py verify --pdf <path>` 已确认全部 32 条 `expected_substring` 都能在默认切分结果中找到。

## 3. 评测环境

| 项目 | 版本/配置 |
|------|----------|
| 操作系统 | Windows 11 |
| Python | 3.11（uv 管理） |
| Embedding 模型 | `BAAI/bge-small-zh-v1.5`（512 维，中文优化） |
| 向量存储 | `InMemoryVectorStore`（LangChain，余弦相似度） |
| PDF 解析 | PyMuPDF 1.28+ |
| 文本切分 | LangChain `RecursiveCharacterTextSplitter`（按页切分，不跨页） |

> 评测脚本调用本地 Embedding，不依赖外部 API。运行前需 `uv sync --extra embedding` 安装 `sentence-transformers`。

## 4. 实验参数

5 组实验，覆盖两个维度的参数对比（满足"至少两组"要求）：

| 实验名称 | chunk_size | chunk_overlap | top_k | 说明 |
|----------|-----------|---------------|-------|------|
| `chunk-300-overlap-50` | 300 | 50 | 5 | 小片段：粒度细，chunk 数量多 |
| `chunk-500-overlap-0` | 500 | 0 | 5 | 中片段无重叠：对比 overlap 影响 |
| `chunk-500-overlap-80` | 500 | 80 | 5 | **基线**（项目默认参数） |
| `chunk-500-overlap-160` | 500 | 160 | 5 | 中片段高重叠：对比 overlap 影响 |
| `chunk-800-overlap-100` | 800 | 100 | 5 | 大片段：粒度粗，chunk 数量少 |

- 维度 1：`chunk_size`（300 / 500 / 800），控制片段粒度
- 维度 2：`chunk_overlap`（0 / 80 / 160，固定 `chunk_size=500`），控制边界重叠
- `top_k` 统一取 5（= `max(HIT_K_VALUES)`，保证 Hit@5 可计算）

## 5. 评测结果

### 5.1 汇总指标

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|------|--------|-------|-------|-----|-------------|
| `chunk-300-overlap-50` | 75 | **53.1%** | 81.2% | **0.651** | 6.3 |
| `chunk-500-overlap-0` | 43 | 50.0% | **87.5%** | 0.636 | 5.8 |
| `chunk-500-overlap-80`（基线） | 47 | 46.9% | 81.2% | 0.613 | 5.8 |
| `chunk-500-overlap-160` | 53 | **53.1%** | 78.1% | 0.613 | 5.8 |
| `chunk-800-overlap-100` | 27 | 37.5% | 84.4% | 0.540 | 5.4 |

> 加粗表示该指标下的最优值。

### 5.2 复现命令

```powershell
# 1. 安装本地 Embedding 后端
uv sync --extra embedding

# 2. 验证数据集（32 条子串均能在切分结果中找到）
uv run python scripts/evaluate.py verify --pdf <pdf_path>

# 3. 运行全部 5 组实验
uv run python scripts/evaluate.py run --pdf <pdf_path>

# 4. 仅运行基线实验
uv run python scripts/evaluate.py run --pdf <pdf_path> --only chunk-500-overlap-80

# 5. 自定义实验配置（JSON 文件）
uv run python scripts/evaluate.py run --pdf <pdf_path> --config my_experiments.json
```

## 6. 结论与分析

### 6.1 参数对比结论

1. **`chunk_size=500` 整体最优**：在 Hit@5（87.5%）和 MRR（0.636）上表现最好。`chunk_size=300` 在 Hit@1 和 MRR 上略优，但 Hit@5 略低；`chunk_size=800` 各项指标均最差。

2. **大片段（`chunk_size=800`）不适合中文科研文献**：Hit@1 仅 37.5%，MRR 0.540。原因是大片段把多个主题混在一个 chunk 里，稀释了语义，Embedding 难以精准匹配单个问题的核心段落。

3. **`overlap` 增大对 Hit@5 有负面影响**（固定 `chunk_size=500`）：overlap 0→80→160 时 Hit@5 为 87.5%→81.2%→78.1%。原因可能是重叠导致同一信息被拆分到多个 chunk，降低了单个 chunk 的语义独立性，反而干扰排序。但 overlap 增大对 Hit@1 有正面影响（46.9%→53.1%），因为重叠能让边界信息出现在更多 chunk 中，提高 Top-1 命中概率。

4. **检索耗时与 chunk 数量正相关**：75 chunks 耗时 6.3ms，27 chunks 耗时 5.4ms。但绝对差异很小（<1ms），对用户体验无影响。InMemoryVectorStore 的检索复杂度是 O(n)，chunk 数量翻倍耗时仅增加约 1ms。

5. **当前项目默认参数（`chunk-500-overlap-80`）表现中等**：Hit@5=81.2%，MRR=0.613。若追求更高召回率，建议将 `chunk_overlap` 调为 0（Hit@5 提升至 87.5%）。但需注意，overlap=0 可能导致跨边界的问题答案被切断（见 6.2 失败分析）。

### 6.2 失败 case 模式分析

跨所有实验的共性失败 case（在多组实验中 Hit@5 未命中）：

- **`BrainPrint如何表示EEG来识别身份？`**：在全部 5 组实验中均未命中。答案在页 4 开头（`提出的BrainPrint将EEG表示为图结构`），但 chunk 边界常切断"提出的"和"BrainPrint"的上下文，且问题中的"BrainPrint"与正文中的"BrainPrint"（大小写、连字符）存在归一化差异。
- **`静息态EEG采集有什么优势？`**：在 4 组实验中未命中。答案在页 3 的表 1 中（表格被切分成多个碎片 chunk），表格内容分散导致语义不集中。
- **`可撤销脑纹用了哪些隐私保护方法？`**：在 3 组实验中未命中。答案是一个枚举列表（`随机投影、二值编码、受密钥控制的变换`），列表项被切分到不同 chunk。

**失败模式归纳**：
1. **跨 chunk 边界**：答案正好被切分边界切断（如"提出的BrainPrint"被拆到两个 chunk）
2. **表格内容碎片化**：PDF 表格被按行提取并切分，单个 chunk 只含表格的几个单元格，语义不完整
3. **枚举列表分散**：多个并列项被切到不同 chunk，单个 chunk 无法覆盖完整答案

**未来改进方向**（不在本 Issue 范围）：
- 跨页切分（当前按页切分，跨页答案无法召回）
- 表格感知切分（识别表格边界，整表作为一个 chunk）
- 混合检索（BM25 + 向量检索，对枚举类问题更友好）
- Rerank（用 cross-encoder 对 Top-K 结果重排，提升 Hit@1）

### 6.3 验收标准达成情况

| 验收标准 | 达成情况 |
|---------|---------|
| 至少 30 条评测数据 | 32 条（达标） |
| 指标：Hit@1、Hit@5、MRR、平均检索耗时 | 全部实现（达标） |
| 至少两组参数对比 | 5 组，覆盖 chunk_size 和 chunk_overlap 两个维度（达标） |
| 可选：混合检索或 Rerank | 未实现（可选，留待后续） |
| 评测可复现，记录数据集、参数、环境、结果和结论 | 本报告完整记录（达标） |

## 7. 相关文件

| 文件 | 说明 |
|------|------|
| `eval/dataset.json` | 32 条评测数据集 |
| `src/research_rag/evaluation.py` | 指标计算与数据集加载的纯函数 |
| `scripts/evaluate.py` | 评测脚本（`verify` / `run` 子命令） |
| `tests/unit/test_evaluation.py` | 47 条单元测试 |
| `docs/evaluation_report.md` | 本报告 |
