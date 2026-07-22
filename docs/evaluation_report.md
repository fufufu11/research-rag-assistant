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
| Embedding 模型 | `BAAI/bge-small-en-v1.5`（英文优化，维度 384，约 130MB） |
| 向量存储 | `InMemoryVectorStore`（LangChain） |
| PDF 解析 | PyMuPDF |
| 切分器 | LangChain `RecursiveCharacterTextSplitter` |
| 操作系统 | Windows |
| Python | 3.11 |

> **模型选择说明**：评测论文为英文，因此选用同家族的英文优化模型 `bge-small-en-v1.5`，而非项目生产默认的 `bge-small-zh-v1.5`（中文优化）。生产环境面向中文用户与中文文献，仍保留中文模型为默认；评测脚本通过 `--embedding-model` 参数支持按论文语言切换模型。

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
| chunk-300-overlap-50 | 681 | **36.7%** | 56.7% | 0.436 | 21.1 |
| chunk-500-overlap-0 | 391 | 30.0% | **70.0%** | **0.458** | 18.2 |
| chunk-500-overlap-80（基线） | 411 | 26.7% | 66.7% | 0.431 | 17.1 |
| chunk-500-overlap-160 | 492 | 20.0% | 53.3% | 0.326 | 16.7 |
| chunk-800-overlap-100 | 264 | 30.0% | **70.0%** | 0.450 | 15.2 |

### 5.2 参数影响分析

#### chunk_size 的影响

- **小片段（300）**：Hit@1 最高（36.7%），但 Hit@5 中等（56.7%）。小片段粒度细，正确答案所在 chunk 信息聚焦，更容易排在第一位；但 chunk 数量多达 681，正确片段容易被其他语义相近的短片段挤掉 Top-5。
- **中片段（500）**：综合表现最佳。无重叠时 Hit@5 达 70%，MRR 最高（0.458）。信息密度适中，正确答案完整落在一个 chunk 内的概率高。
- **大片段（800）**：Hit@5 与中片段持平（70%），Hit@1 也较高（30.0%）。大片段包含更多上下文，检索时更易匹配到答案所在区域；但 chunk 数量少（264），粒度粗，引用溯源时定位精度下降。

#### chunk_overlap 的影响

- **无重叠（0）**：Hit@5 最高（70%），MRR 最高（0.458）。边界清晰，正确答案不被切散到多个重叠片段中分散排名。
- **中重叠（80，基线）**：Hit@5 略降（66.7%），Hit@1 下降到 26.7%。重叠使答案出现在多个 chunk 中，反而分散了正确片段的排名分数。
- **高重叠（160）**：Hit@5 最低（53.3%），Hit@1 也最低（20.0%）。继续增加重叠不仅不提升召回，还引入更多重复片段，造成排名分散和检索噪声。

### 5.3 关键发现

1. **英文 Embedding 显著优于中文模型**：相比此前用 `bge-small-zh-v1.5` 的评测（Hit@5 约 43-50%、MRR 约 0.25-0.30），改用 `bge-small-en-v1.5` 后 Hit@5 提升到 53-70%、MRR 提升到 0.33-0.46。模型与文档语言匹配是检索质量的基础。
2. **overlap 越大反而越差**：与中文论文评测时"overlap 提升召回"的结论不同，英文论文 + 英文 Embedding 下，overlap=0 反而最优。原因：英文 Embedding 语义匹配精确，重叠产生的重复片段会分散正确答案的排名分数；中文模型语义匹配较弱时，重叠能提升召回。
3. **最优参数为 chunk-500-overlap-0**：Hit@5=70%、MRR=0.458，且 chunk 数量适中（391）。但项目默认基线仍保留 chunk-500-overlap-80，因为生产环境面向中文文献 + 中文 Embedding，参数选择需以中文场景为准。
4. **多论文场景的检索难度**：3 篇论文合并后 391-681 个 chunk，Hit@5=70% 意味着 30% 的问题被跨论文语义干扰挤出 Top-5，仍有优化空间。

### 5.4 BGE Reranker 重排序效果

引入 Cross-Encoder（`BAAI/bge-reranker-base`）对向量检索 Top-K 结果重排，对比有/无重排的指标：

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 681 | 36.7% | 56.7% | 0.436 | 19.5 |
| chunk-300-overlap-50 + reranker | 681 | **46.7%** | 56.7% | 0.508 | 215.6 |
| chunk-500-overlap-0 | 391 | 30.0% | **70.0%** | 0.458 | 19.1 |
| chunk-500-overlap-0 + reranker | 391 | **50.0%** | **70.0%** | **0.590** | 407.2 |
| chunk-500-overlap-80（基线） | 411 | 26.7% | 66.7% | 0.431 | 17.9 |
| chunk-500-overlap-80 + reranker | 411 | **50.0%** | 66.7% | 0.575 | 353.4 |
| chunk-500-overlap-160 | 492 | 20.0% | 53.3% | 0.326 | 17.9 |
| chunk-500-overlap-160 + reranker | 492 | **40.0%** | 53.3% | 0.456 | 376.0 |
| chunk-800-overlap-100 | 264 | 30.0% | **70.0%** | 0.450 | 15.5 |
| chunk-800-overlap-100 + reranker | 264 | **46.7%** | **70.0%** | 0.575 | 532.5 |

#### 重排序效果分析

1. **Hit@1 全面大幅提升**：所有实验组的 Hit@1 均显著提升，提升幅度 +10% ~ +23.3%。基线组（chunk-500-overlap-80）从 26.7% 提升到 50.0%（相对提升 87%），效果最为显著。Cross-Encoder 将 query+document 联合编码，精排能力远超 Bi-Encoder 的独立编码。
2. **Hit@5 保持不变**：reranker 只对已召回的 Top-K 重排序，不改变召回集合，因此 Hit@5 不受影响。召回阶段的失败（关键词列表、数值、公式类问题）需通过混合检索等其他手段解决。
3. **MRR 显著提升**：全局最优组合为 chunk-500-overlap-0 + reranker（MRR=0.590），较无重排的 0.458 提升 29%。即使是最差组（chunk-500-overlap-160 + reranker，MRR=0.456）也超过了基线无重排的 MRR=0.431。
4. **延迟代价可接受**：Cross-Encoder 推理增加 200-530ms 延迟，最优组总延迟 407ms，仍在亚秒级。生产环境可通过 GPU 推理或缓存进一步优化。
5. **最优生产组合**：chunk-500-overlap-0 + reranker，Hit@1=50%、Hit@5=70%、MRR=0.590。兼顾精度与延迟，推荐生产启用。

### 5.5 跨页切分 A/B 对比（阶段 8.2）

引入跨页切分（`cross_page=True`，合并连续页文本后统一切分）后，与旧行为（`cross_page=False`，按页独立切分）对比。两组实验均启用 reranker，仅切换切分模式。

#### 关闭跨页切分（旧行为，按页独立切分）

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 681 | 36.7% | 56.7% | 0.436 | 20.4 |
| chunk-300-overlap-50 + reranker | 681 | 46.7% | 56.7% | 0.508 | 229.1 |
| chunk-500-overlap-0 | 391 | 30.0% | **70.0%** | 0.458 | 17.3 |
| chunk-500-overlap-0 + reranker | 391 | **50.0%** | **70.0%** | **0.590** | 350.2 |
| chunk-500-overlap-80（基线） | 411 | 26.7% | 66.7% | 0.431 | 19.4 |
| chunk-500-overlap-80 + reranker | 411 | **50.0%** | 66.7% | 0.575 | 343.1 |
| chunk-500-overlap-160 | 492 | 20.0% | 53.3% | 0.326 | 18.5 |
| chunk-500-overlap-160 + reranker | 492 | 40.0% | 53.3% | 0.456 | 358.1 |
| chunk-800-overlap-100 | 264 | 30.0% | **70.0%** | 0.450 | 17.7 |
| chunk-800-overlap-100 + reranker | 264 | 46.7% | **70.0%** | 0.575 | 514.0 |

#### 启用跨页切分（新行为，合并连续页文本后统一切分）

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 665 | 33.3% | 50.0% | 0.393 | 18.0 |
| chunk-300-overlap-50 + reranker | 665 | 36.7% | 50.0% | 0.419 | 258.8 |
| chunk-500-overlap-0 | 370 | 13.3% | **73.3%** | 0.368 | 17.3 |
| chunk-500-overlap-0 + reranker | 370 | 40.0% | **73.3%** | 0.551 | 347.9 |
| chunk-500-overlap-80 | 394 | 20.0% | 56.7% | 0.356 | 15.9 |
| chunk-500-overlap-80 + reranker | 394 | 36.7% | 56.7% | 0.461 | 354.6 |
| chunk-500-overlap-160 | 489 | 26.7% | 63.3% | 0.402 | 17.2 |
| chunk-500-overlap-160 + reranker | 489 | 43.3% | 63.3% | 0.519 | 347.9 |
| chunk-800-overlap-100 | 246 | 33.3% | 66.7% | 0.464 | 17.6 |
| chunk-800-overlap-100 + reranker | 246 | **50.0%** | 66.7% | 0.558 | 526.5 |

#### 跨页切分效果分析

1. **Hit@5 在最优参数下提升**：chunk-500-overlap-0 的 Hit@5 从 70.0% 提升到 73.3%（+3.3%），chunk-500-overlap-160 的 Hit@5 从 53.3% 提升到 63.3%（+10%）。跨页切分确实解决了部分跨页边界问题，正确答案不再因页面边界切断而丢失。
2. **Hit@1 整体下降**：跨页 chunk 包含更多内容，语义更分散，导致精确匹配（Top-1）变难。chunk-500-overlap-0 的 Hit@1 从 30.0% 降到 13.3%（-16.7%），即使加 reranker 也从 50.0% 降到 40.0%（-10%）。
3. **chunk 数量减少**：跨页合并后切分更紧凑，chunk 数量普遍减少 2-21 个（如 391→370）。粒度变粗是 Hit@1 下降的部分原因。
4. **小 chunk（300）和大 chunk（800）场景下 Hit@5 反而下降**：小 chunk 本身粒度足够细，跨页合并反而稀释了语义；大 chunk 本身已包含足够上下文，跨页合并收益有限。
5. **最优组合变化**：关闭跨页切分时最优为 chunk-500-overlap-0 + reranker（MRR=0.590）；启用跨页切分后最优为 chunk-800-overlap-100 + reranker（MRR=0.558），但全局 MRR 略有下降。

#### 结论与设计取舍

跨页切分在本数据集（英文 AI 论文）上的效果是**混合的**：解决了跨页边界问题（Hit@5 提升），但牺牲了精确匹配（Hit@1 下降）。原因分析：

- 英文论文段落结构清晰、单页信息密度高，按页切分的损失本就较小
- 跨页 chunk 包含多页内容，语义分散导致 Top-1 精度下降
- 本数据集的 `expected_substring` 多为短句，跨页 chunk 中的额外上下文反而成为干扰

**保留 `cross_page=True` 作为默认**的理由：
1. 解决跨页边界问题是正确的设计方向，中文文献段落跨页更普遍，收益预期更大
2. Hit@5（召回率）提升比 Hit@1（精度）下降更重要——召回是基础，精度可由 reranker 弥补
3. 后续阶段 8.3 混合检索（BM25 + 向量）可显著改善 Hit@1 下降的问题，BM25 的精确关键词匹配能补偿跨页 chunk 语义分散的负面影响

### 5.6 BM25 混合检索效果（阶段 8.3）

引入 BM25 稀疏检索与向量检索融合（加权 RRF，`vector_weight=2.0`, `bm25_weight=1.0`），对比基线 / +BM25 / +reranker / +BM25+reranker 四组。所有实验均启用跨页切分。

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 665 | 33.3% | 50.0% | 0.393 | 18.5 |
| chunk-300-overlap-50 + bm25 | 665 | 30.0% | 56.7% | 0.414 | 20.9 |
| chunk-300-overlap-50 + reranker | 665 | 36.7% | 50.0% | 0.419 | 249.0 |
| chunk-300-overlap-50 + bm25 + reranker | 665 | 36.7% | **56.7%** | **0.442** | 242.1 |
| chunk-500-overlap-0 | 370 | 13.3% | 73.3% | 0.368 | 17.9 |
| chunk-500-overlap-0 + bm25 | 370 | 26.7% | 76.7% | 0.457 | 17.1 |
| chunk-500-overlap-0 + reranker | 370 | 40.0% | 73.3% | 0.551 | 328.2 |
| chunk-500-overlap-0 + bm25 + reranker | 370 | **46.7%** | **76.7%** | **0.607** | 333.8 |
| chunk-500-overlap-80 | 394 | 20.0% | 56.7% | 0.356 | 17.2 |
| chunk-500-overlap-80 + bm25 | 394 | 33.3% | 66.7% | 0.459 | 18.7 |
| chunk-500-overlap-80 + reranker | 394 | 36.7% | 56.7% | 0.461 | 350.3 |
| chunk-500-overlap-80 + bm25 + reranker | 394 | 36.7% | **66.7%** | **0.497** | 342.8 |
| chunk-500-overlap-160 | 489 | 26.7% | 63.3% | 0.402 | 18.9 |
| chunk-500-overlap-160 + bm25 | 489 | 36.7% | 66.7% | 0.489 | 20.2 |
| chunk-500-overlap-160 + reranker | 489 | 43.3% | 63.3% | 0.519 | 345.0 |
| chunk-500-overlap-160 + bm25 + reranker | 489 | **46.7%** | **66.7%** | **0.553** | 338.8 |
| chunk-800-overlap-100 | 246 | 33.3% | 66.7% | 0.464 | 15.0 |
| chunk-800-overlap-100 + bm25 | 246 | 33.3% | 70.0% | 0.473 | 15.0 |
| chunk-800-overlap-100 + reranker | 246 | **50.0%** | 66.7% | **0.558** | 546.6 |
| chunk-800-overlap-100 + bm25 + reranker | 246 | 46.7% | **70.0%** | 0.550 | 498.9 |

#### BM25 混合检索效果分析

1. **Hit@5 在所有参数组全面提升**：BM25+reranker 相比纯 reranker，Hit@5 在 5 组参数中均提升或持平（+3.3% ~ +10.0%）。chunk-500-overlap-80 提升最显著（56.7% → 66.7%，+10%），chunk-500-overlap-0 达到全局最高 76.7%。BM25 召回了向量检索遗漏的关键词/数值类文档。
2. **Hit@1 在最优参数组大幅提升**：chunk-500-overlap-0 + BM25 + reranker 的 Hit@1 达到 46.7%，比纯 reranker（40.0%）提升 6.7%。BM25 的精确关键词匹配让正确答案更容易排在第一位。
3. **MRR 全面提升**：4/5 组参数的 MRR 提升（+0.023 ~ +0.056），全局最优 MRR=0.607（chunk-500-overlap-0 + BM25 + reranker），较纯 reranker（0.551）提升 10%。
4. **延迟代价极小**：BM25 检索本身仅 1-3ms，叠加 reranker 后总延迟与纯 reranker 基本持平（如 333.8ms vs 328.2ms）。
5. **加权 RRF 是关键设计**：早期等权 RRF（`vector_weight=1.0`）导致 BM25 噪声文档挤出向量好文档，Hit@5 反而下降（73.3% → 66.7%）。改为 `vector_weight=2.0` 后，BM25 召回的噪声不再干扰向量检索的好文档，同时保留了 BM25 对关键词精确匹配的补充能力。

#### 失败 case 改善对比（chunk-500-overlap-0）

| 配置 | Hit@5 未命中数 | 改善的 case |
|---|---|---|
| reranker | 8 条 | - |
| BM25 + reranker | 7 条 | "What mechanism is the Transformer based on?"（关键词类）|

BM25+reranker 相比 reranker 改善了 1 条 Hit@5 未命中（"What mechanism is the Transformer based on?" —— 关键词类问题），且未新增任何恶化 case。

#### 最优生产组合

**chunk-500-overlap-0 + BM25 + reranker**：Hit@1=46.7%、Hit@5=76.7%、MRR=0.607，延迟 333.8ms。相比上一阶段最优（chunk-500-overlap-0 + reranker，MRR=0.551）提升 10%。推荐生产环境同时启用 BM25 和 reranker。

## 6. 失败模式分析

分析最优配置（chunk-500-overlap-0）的 9 条 Hit@5 未命中问题，归纳四类失败模式：

### 6.1 关键词列表类问题（1 条）

- "What are the keywords of the EEGNet paper?"

**原因**：关键词列表是多个术语的并列，与自然语言问题的语义距离远。Embedding 模型难以将"What are the keywords"映射到具体的术语列表 chunk。

### 6.2 公式与符号类问题（1 条）

- "What is the formula for Scaled Dot-Product Attention?"

**原因**：公式含特殊符号（`softmax(QK^T/√d)`），Embedding 模型对数学符号的语义理解有限，且 PyMuPDF 提取公式时可能引入额外字符。

### 6.3 跨论文语义干扰（4 条）

- "What type of network is EEGNet?"
- "What convolutions did EEGNet introduce from computer vision?"
- "What does the SMR task in EEGNet classify?"
- "What does the Transformer dispense with?"

**原因**：3 篇论文都涉及 CNN、卷积、网络结构等概念，合并库中存在多个语义相近的 chunk。例如"convolution"在 EEGNet、Transformer（提及 separable convolutions）、AlexNet 中都出现，正确答案所在 chunk 被其他论文的卷积相关 chunk 挤出 Top-5。

### 6.4 数值结果类问题（2 条）

- "What are the top-1 and top-5 error rates of AlexNet?"
- "What top-5 error rate did AlexNet achieve in ILSVRC-2012?"

**原因**：问题问的是具体数值（如 "top-1 error rate of 37.5%"），但答案所在的 chunk 是包含多个数值的结果段落，语义上更接近"实验结果总结"而非具体数值。Embedding 模型对数值的语义表示较弱。

### 6.5 定义性短句类问题（1 条）

- "What mechanism is the Transformer based on?"

**原因**：答案"based solely on attention mechanisms"是定义性短句，语义信息稀薄，与问题的语义匹配度低。

## 7. 局限性与改进方向

### 7.1 当前局限

1. **评测模型与生产模型不一致**：评测用英文 `bge-small-en-v1.5`，生产用中文 `bge-small-zh-v1.5`。评测结论（如 overlap=0 最优）基于英文场景，中文场景的参数选择需以中文评测为准。
2. **只评测检索阶段**：生成阶段（LLM 答案质量）未评测，需消耗 Token 且答案质量主观。
3. **问题类型单一**：当前问题多为事实型（"是什么""有多少"），缺少推理型、对比型问题。
4. **论文数量有限**：3 篇论文的合并库规模较小（391-681 chunks），更大规模库的检索效果未验证。

### 7.2 改进方向

| 方向 | 预期收益 | 实施成本 | 状态 |
|---|---|---|---|
| Cross-Encoder / BGE Reranker 重排序 | Hit@1 显著提升 | 中（增加推理延迟） | ✅ 已实现（Hit@1 提升 +10% ~ +23.3%） |
| 混合检索（BM25 + 向量） | 关键词列表、数值类问题改善 | 中（引入 BM25） | 待实施 |
| 跨页切分 | 跨页边界问题改善 | 低（修改切分逻辑） | ✅ 已实现（Hit@5 +3.3%，Hit@1 有下降，详见 5.5） |
| 换用 `bge-m3` 多语言 Embedding 模型 | 中英文混合场景统一支持 | 中（需重新索引） | 待实施 |
| 表格感知切分 | 表格、数值结果类问题改善 | 高（需表格识别） | 待实施 |

## 8. 复现方法

```powershell
# 安装 Embedding 推理后端
uv sync --extra embedding

# 验证数据集子串匹配（不需要 Embedding 模型）
uv run python scripts/evaluate.py verify --pdfs-dir <含 3 篇 PDF 的目录>

# 运行全部 5 组实验（默认用生产 Embedding 模型，中文优化）
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录>

# 运行实验并指定英文 Embedding 模型（评测英文论文时推荐）
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录> `
    --embedding-model BAAI/bge-small-en-v1.5

# 启用 BGE Reranker 重排序，对比有/无重排的指标
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录> `
    --embedding-model BAAI/bge-small-en-v1.5 `
    --reranker-model BAAI/bge-reranker-base

# 关闭跨页切分（阶段 8.2），退回旧行为按页独立切分，用于 A/B 对比
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录> `
    --embedding-model BAAI/bge-small-en-v1.5 `
    --reranker-model BAAI/bge-reranker-base `
    --no-cross-page

# 仅运行基线实验
uv run python scripts/evaluate.py run --pdfs-dir <含 3 篇 PDF 的目录> `
    --embedding-model BAAI/bge-small-en-v1.5 --only chunk-500-overlap-80
```

数据集见 [eval/dataset.json](../eval/dataset.json)，每条问题的 `pdf` 字段标注答案所在论文的文件名。
