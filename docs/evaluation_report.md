# 检索评测报告

> 本报告记录多论文检索评测的数据集、参数、环境、结果与结论，确保评测可复现。

## 1. 评测目标

评测 RAG 系统检索阶段的质量，回答两个问题：

1. **chunk_size / chunk_overlap 参数如何影响检索质量？**
2. **多论文合并库场景下，检索系统能否准确定位到正确论文的正确片段？**

本评测只评测检索阶段，不调用 LLM，不消耗 Token。

## 2. 评测数据集

### 2.1 论文选择

选取 3 篇主题独立的英文 AI 经典论文，覆盖不同领域（BCI、序列建模、图像分类），避免论文间内容交叉干扰检索：

| 标识 | 论文 | 页数 | 领域 |
|---|---|---|---|
| EEGNet | EEGNet: A Compact Convolutional Neural Network for EEG-based Brain-Computer Interfaces (2018) | 30 | BCI / CNN |
| Transformer | Attention Is All You Need (2017) | 15 | 序列建模 / 注意力机制 |
| AlexNet | ImageNet Classification with Deep Convolutional Neural Networks | 7 | 图像分类 / CNN |

### 2.2 问题集

每篇论文 10 条问题，共 30 条。问题用英文（与论文语言一致），排除跨语言干扰，专注测试 chunking 参数对检索的影响。问题覆盖定义、方法、结果、细节四类：

| 论文 | 定义 | 方法 | 结果 | 细节 | 合计 |
|---|---|---|---|---|---|
| EEGNet | 2 | 4 | 0 | 4 | 10 |
| Transformer | 1 | 7 | 1 | 1 | 10 |
| AlexNet | 0 | 4 | 4 | 2 | 10 |

每条问题标注 `expected_substring`（答案所在 chunk 的独特子串）和 `expected_page`（答案所在页码）。匹配时对子串和 chunk 内容做空白归一化后判断包含关系。

### 2.3 评测模式

**多 PDF 合并库检索**：3 篇论文的所有 chunk 合并到同一个向量库中，每条问题在整个库中检索 Top-K。这模拟用户上传多份文献后的真实场景，能测试跨论文检索的难度。

## 3. 评测环境

| 项 | 值 |
|---|---|
| Embedding 模型 | `BAAI/bge-small-zh-v1.5` |
| 向量存储 | `InMemoryVectorStore`（LangChain） |
| PDF 解析 | PyMuPDF |
| 切分器 | LangChain `RecursiveCharacterTextSplitter` |
| 操作系统 | Windows |
| Python | 3.11 |

> **注意**：`bge-small-zh-v1.5` 是中文 Embedding 模型，对英文有基本支持但非最优。本评测使用英文问题+英文论文，旨在测试纯检索能力；中文问题对英文论文的跨语言检索效果见第 7 节局限性分析。

## 4. 实验配置

5 组参数对比，覆盖两个维度：

| 实验名 | chunk_size | overlap | 说明 |
|---|---|---|---|
| chunk-300-overlap-50 | 300 | 50 | 小片段：粒度细，chunk 数量多 |
| chunk-500-overlap-0 | 500 | 0 | 中片段无重叠：对比 overlap 影响 |
| chunk-500-overlap-80 | 500 | 80 | 基线（项目默认参数） |
| chunk-500-overlap-160 | 500 | 160 | 中片段高重叠：对比 overlap 影响 |
| chunk-800-overlap-100 | 800 | 100 | 大片段：粒度粗，chunk 数量少 |

## 5. 评测结果

### 5.1 汇总指标

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 681 | 16.7% | 43.3% | 0.269 | 16.5 |
| chunk-500-overlap-0 | 391 | **23.3%** | 43.3% | **0.298** | 12.7 |
| chunk-500-overlap-80（基线） | 411 | 13.3% | **50.0%** | 0.262 | 17.9 |
| chunk-500-overlap-160 | 492 | 13.3% | **50.0%** | 0.262 | 14.6 |
| chunk-800-overlap-100 | 264 | 13.3% | **50.0%** | 0.250 | 13.2 |

### 5.2 参数影响分析

#### chunk_size 的影响

- **小片段（300）**：Hit@1 较高（16.7%），但 Hit@5 最低（43.3%）。小片段粒度细，正确答案所在 chunk 信息少，容易被其他语义相近的片段挤掉。chunk 数量多达 681，检索噪声大。
- **中片段（500）**：Hit@5 达到 50%，信息密度适中。无重叠时 Hit@1 最高（23.3%），因为片段边界清晰，正确答案完整落在一个 chunk 内的概率高。
- **大片段（800）**：Hit@5 与中片段持平（50%），但 MRR 最低（0.250）。大片段虽然包含更多信息，但检索时可能匹配到片段中与问题无关的部分，导致排名靠后。chunk 数量少（264），检索快但粒度粗。

#### chunk_overlap 的影响

- **无重叠（0）**：Hit@1 最高（23.3%），MRR 最高（0.298）。边界清晰，正确答案不被切散。
- **中重叠（80，基线）**：Hit@5 最高（50%），但 Hit@1 下降到 13.3%。重叠使边界问题答案出现在多个 chunk 中，提高召回但分散排名。
- **高重叠（160）**：Hit@5 与基线持平（50%），Hit@1 不变（13.3%）。继续增加重叠不再提升召回，只增加 chunk 数量（492）和索引成本。

### 5.3 关键发现

1. **Hit@1 与 Hit@5 的取舍**：无重叠参数（chunk-500-overlap-0）在 Hit@1 和 MRR 上最优，适合"最相关结果排第一"的场景；有重叠参数在 Hit@5 上更优，适合"召回优先"的场景。
2. **overlap 的边际收益递减**：从 0→80 重叠，Hit@5 提升 6.7 个百分点；从 80→160 重叠，Hit@5 无变化。当前基线（80）已接近 overlap 的收益上限。
3. **多论文场景的检索难度**：3 篇论文合并后 411 个 chunk（基线参数），Hit@5=50% 意味着一半问题能在 Top-5 找到正确片段，另一半被跨论文的语义干扰挤出。

## 6. 失败模式分析

分析基线（chunk-500-overlap-80）的 15 条 Hit@5 未命中问题，归纳三类失败模式：

### 6.1 关键词列表类问题（3 条）

- "What are the keywords of the EEGNet paper?"

**原因**：关键词列表是多个术语的并列，与自然语言问题的语义距离远。Embedding 模型难以将"What are the keywords"映射到具体的术语列表 chunk。

### 6.2 公式与符号类问题（3 条）

- "What is the formula for Scaled Dot-Product Attention?"
- "What functions are used for positional encoding in the Transformer?"
- "How is the residual connection implemented in the Transformer?"

**原因**：公式含特殊符号（`softmax(QKT`、`sin`、`cos`、`LayerNorm(x + Sublayer(x))`），Embedding 模型对数学符号的语义理解有限，且 PyMuPDF 提取公式时可能引入额外字符。

### 6.3 跨论文语义干扰（4 条）

- "What convolutions did EEGNet introduce from computer vision?"
- "How does AlexNet accelerate convolution operations?"
- "How many convolutional and fully connected layers does AlexNet have?"
- "What happens if middle convolutional layers are removed from AlexNet?"

**原因**：3 篇论文都涉及 CNN 和卷积操作，合并库中存在多个语义相近的 chunk。例如"convolution"在 EEGNet、Transformer（提及 separable convolutions）、AlexNet 中都出现，正确答案所在 chunk 被其他论文的卷积相关 chunk 挤出 Top-5。

### 6.4 页面标题/元信息类问题（3 条）

- "What does the EEGNet architecture figure show?"
- "What is the title of the EEGNet paper?"
- "What type of events are ERN related to?"

**原因**：图标题（"Overall visualization of the EEGNet architecture"）、论文标题、定义性短句的语义信息稀薄，与问题的语义匹配度低。

## 7. 局限性与改进方向

### 7.1 当前局限

1. **Embedding 模型与文档语言不匹配**：`bge-small-zh-v1.5` 是中文模型，对英文文档的语义理解有限。改用多语言模型（如 `bge-m3`）或英文模型（如 `bge-small-en-v1.5`）预计能显著提升英文论文检索效果。
2. **跨语言检索效果差**：用中文问题测英文论文时，Hit@5 仅 23-43%（相比英文问题的 50%）。项目面向中文用户，但文献多为英文，这是核心矛盾。
3. **只评测检索阶段**：生成阶段（LLM 答案质量）未评测，需消耗 Token 且答案质量主观。
4. **问题类型单一**：当前问题多为事实型（"是什么""有多少"），缺少推理型、对比型问题。

### 7.2 改进方向

| 方向 | 预期收益 | 实施成本 |
|---|---|---|
| 换用 `bge-m3` 多语言 Embedding 模型 | 跨语言检索显著提升 | 中（需重新索引） |
| 混合检索（BM25 + 向量） | 关键词列表类问题改善 | 中（引入 BM25） |
| Cross-Encoder / BGE Reranker 重排序 | Hit@1 显著提升 | 中（增加推理延迟） |
| 跨页切分 | 跨页边界问题改善 | 低（修改切分逻辑） |
| 表格感知切分 | 表格类问题改善 | 高（需表格识别） |

## 8. 复现方法

```powershell
# 安装 Embedding 推理后端
uv sync --extra embedding

# 验证数据集子串匹配（不需要 Embedding 模型）
uv run python scripts/evaluate.py verify --pdfs-dir <含 3 篇 PDF 的目录>

# 运行全部 5 组实验
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录>

# 仅运行基线实验
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录> --only chunk-500-overlap-80
```

数据集见 [eval/dataset.json](../eval/dataset.json)，每条问题的 `pdf` 字段标注答案所在论文的文件名。
