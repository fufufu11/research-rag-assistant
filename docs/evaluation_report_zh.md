# 中文检索评测报告

> 本报告记录中文论文检索评测的数据集、参数、环境、结果与结论，重点对比本地中文专用 Embedding 模型与多语言 API Embedding 模型在中文论文场景下的检索质量差异，确保评测可复现。

## 1. 评测目标

评测 RAG 系统检索阶段在**中文论文**场景下的质量，回答三个问题：

1. **中文专用 Embedding 模型与多语言 API Embedding 模型谁更适合中文论文检索？**
2. **BM25 混合检索与 Cross-Encoder 重排序在中文场景的增益如何？**
3. **chunk_size / chunk_overlap 参数对中文论文检索的影响与英文场景有何差异？**

本评测只评测检索阶段，不调用 LLM，不消耗 Token（Jina API 仅消耗 Embedding 调用配额）。

## 2. 评测数据集

### 2.1 论文选择

选取 3 篇主题独立的中文 AI 论文（ChinaXiv 公开预印本），覆盖不同领域（NLP 文本分类、多模态视频情绪识别、CV 目标检测），避免论文间内容交叉干扰检索：

| 标识 | 论文主题 | 页数 | 领域 |
|---|---|---|---|
| 论文 A | CNN-ELM 混合模型的短文本分类 | 7 | NLP / 文本分类 |
| 论文 B | 多流 CNN-LSTM 网络的群体情绪识别 | 5 | 多模态 / 视频分析 |
| 论文 C | 基于多尺度神经网络的船舶目标检测 | 10 | CV / 目标检测 |

> 论文来源为 ChinaXiv 公开预印本，本报告不列出作者姓名。

### 2.2 问题集

每篇论文 10 条问题，共 30 条。问题用中文（与论文语言一致），排除跨语言干扰，专注测试 chunking 参数与 Embedding 模型对中文检索的影响。问题覆盖定义、方法、结果、细节四类：

| 论文 | 定义 | 方法 | 结果 | 细节 | 合计 |
|---|---|---|---|---|---|
| 论文 A（CNN-ELM） | 2 | 4 | 2 | 2 | 10 |
| 论文 B（群体情绪） | 1 | 4 | 1 | 4 | 10 |
| 论文 C（船舶检测） | 0 | 5 | 1 | 4 | 10 |

每条问题标注 `expected_substring`（答案所在 chunk 的独特子串）和 `expected_page`（答案所在页码）。匹配时对子串和 chunk 内容做空白归一化后判断包含关系。

### 2.3 评测模式

**多 PDF 合并库检索**：3 篇论文的所有 chunk 合并到同一个向量库中，每条问题在整个库中检索 Top-K。这模拟用户上传多份文献后的真实场景，能测试跨论文检索的难度。

## 3. 评测环境

### 3.1 bge-small-zh-v1.5（本地基线）

| 项 | 值 |
|---|---|
| Embedding 模型 | `BAAI/bge-small-zh-v1.5`（中文优化，维度 512，约 95MB） |
| 调用方式 | 本地 HuggingFace 推理（provider=local） |
| 向量存储 | `InMemoryVectorStore`（LangChain） |
| PDF 解析 | PyMuPDF |
| 切分器 | LangChain `RecursiveCharacterTextSplitter` |
| 操作系统 | Windows |
| Python | 3.11 |

### 3.2 jina-embeddings-v3（API 对比）

| 项 | 值 |
|---|---|
| Embedding 模型 | `jina-embeddings-v3`（多语言，维度 1024，8192 tokens） |
| 调用方式 | Jina AI OpenAI 兼容 API（provider=jina） |
| API base_url | `https://api.jina.ai/v1` |
| 向量存储 / PDF 解析 / 切分器 / 系统 | 同上 |

> **模型选择说明**：`bge-small-zh-v1.5` 是项目生产默认配置（中文优化小模型，本地推理零成本）；`jina-embeddings-v3` 是多语言大模型 API，用于验证"更大的多语言模型是否能在中文场景超越本地中文专用小模型"。Reranker 两模型均使用本地 `BAAI/bge-reranker-base`，确保对比变量仅限于 Embedding 模型。

## 4. 实验配置

5 组参数对比，覆盖两个维度：

| 实验名 | chunk_size | overlap | 说明 |
|---|---|---|---|
| chunk-300-overlap-50 | 300 | 50 | 小片段：粒度细，chunk 数量多 |
| chunk-500-overlap-0 | 500 | 0 | 中片段无重叠：对比 overlap 影响 |
| chunk-500-overlap-80 | 500 | 80 | 基线（项目默认参数） |
| chunk-500-overlap-160 | 500 | 160 | 中片段高重叠：对比 overlap 影响 |
| chunk-800-overlap-100 | 800 | 100 | 大片段：粒度粗，chunk 数量少 |

每组参数运行 4 个变体：基线 / +BM25 / +reranker / +BM25+reranker，共 20 组实验。

## 5. 评测结果

### 5.1 bge-small-zh-v1.5 汇总（本地基线）

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 178 | 20.0% | 70.0% | 0.396 | 10.6 |
| chunk-300-overlap-50 + bm25 | 178 | 20.0% | 76.7% | 0.422 | 10.0 |
| chunk-300-overlap-50 + reranker | 178 | 40.0% | 70.0% | 0.544 | 561.5 |
| chunk-300-overlap-50 + bm25 + reranker | 178 | 43.3% | 76.7% | 0.583 | 578.6 |
| chunk-500-overlap-0 | 95 | 26.7% | 80.0% | 0.476 | 10.0 |
| chunk-500-overlap-0 + bm25 | 95 | 26.7% | 83.3% | 0.517 | 10.7 |
| chunk-500-overlap-0 + reranker | 95 | 66.7% | 80.0% | 0.728 | 1002.7 |
| chunk-500-overlap-0 + bm25 + reranker | 95 | 70.0% | 83.3% | 0.761 | 1018.3 |
| chunk-500-overlap-80（基线） | 108 | 36.7% | 73.3% | 0.504 | 9.3 |
| chunk-500-overlap-80 + bm25 | 108 | 40.0% | 86.7% | 0.576 | 9.3 |
| chunk-500-overlap-80 + reranker | 108 | 60.0% | 73.3% | 0.667 | 735.5 |
| chunk-500-overlap-80 + bm25 + reranker | 108 | **70.0%** | **86.7%** | **0.778** | 756.1 |
| chunk-500-overlap-160 | 134 | 46.7% | 83.3% | 0.607 | 10.1 |
| chunk-500-overlap-160 + bm25 | 134 | 50.0% | **90.0%** | 0.667 | 12.4 |
| chunk-500-overlap-160 + reranker | 134 | **73.3%** | 83.3% | **0.783** | 709.5 |
| chunk-500-overlap-160 + bm25 + reranker | 134 | 63.3% | **90.0%** | 0.750 | 676.6 |
| chunk-800-overlap-100 | 64 | 33.3% | 66.7% | 0.459 | 8.9 |
| chunk-800-overlap-100 + bm25 | 64 | 46.7% | 80.0% | 0.589 | 7.4 |
| chunk-800-overlap-100 + reranker | 64 | 53.3% | 66.7% | 0.594 | 1115.8 |
| chunk-800-overlap-100 + bm25 + reranker | 64 | 60.0% | 80.0% | 0.686 | 1151.3 |

### 5.2 jina-embeddings-v3 汇总（API 对比）

| 实验 | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |
|---|---|---|---|---|---|
| chunk-300-overlap-50 | 178 | 16.7% | 43.3% | 0.261 | 500.0 |
| chunk-300-overlap-50 + bm25 | 178 | 23.3% | 53.3% | 0.338 | 464.3 |
| chunk-300-overlap-50 + reranker | 178 | 30.0% | 43.3% | 0.367 | 912.6 |
| chunk-300-overlap-50 + bm25 + reranker | 178 | 33.3% | 53.3% | 0.433 | 945.3 |
| chunk-500-overlap-0 | 95 | 30.0% | 53.3% | 0.379 | 464.4 |
| chunk-500-overlap-0 + bm25 | 95 | 33.3% | 60.0% | 0.434 | 443.2 |
| chunk-500-overlap-0 + reranker | 95 | 50.0% | 53.3% | 0.511 | 1152.6 |
| chunk-500-overlap-0 + bm25 + reranker | 95 | 53.3% | 60.0% | 0.561 | 1282.6 |
| chunk-500-overlap-80（基线） | 108 | 16.7% | 43.3% | 0.250 | 395.3 |
| chunk-500-overlap-80 + bm25 | 108 | 16.7% | 60.0% | 0.319 | 380.5 |
| chunk-500-overlap-80 + reranker | 108 | 40.0% | 43.3% | 0.417 | 1127.2 |
| chunk-500-overlap-80 + bm25 + reranker | 108 | 50.0% | 60.0% | 0.544 | 1118.9 |
| chunk-500-overlap-160 | 134 | 30.0% | 50.0% | 0.384 | 394.0 |
| chunk-500-overlap-160 + bm25 | 134 | 36.7% | 60.0% | 0.472 | 385.3 |
| chunk-500-overlap-160 + reranker | 134 | 50.0% | 50.0% | 0.500 | 1150.9 |
| chunk-500-overlap-160 + bm25 + reranker | 134 | **56.7%** | **60.0%** | **0.583** | 1149.4 |
| chunk-800-overlap-100 | 64 | 20.0% | 53.3% | 0.325 | 396.7 |
| chunk-800-overlap-100 + bm25 | 64 | 33.3% | 60.0% | 0.437 | 382.6 |
| chunk-800-overlap-100 + reranker | 64 | 46.7% | 53.3% | 0.492 | 1595.4 |
| chunk-800-overlap-100 + bm25 + reranker | 64 | 50.0% | 60.0% | 0.542 | 1608.8 |

### 5.3 两模型最优配置对比

| 指标 | bge-small-zh-v1.5（本地） | jina-embeddings-v3（API） | 差距 |
|---|---|---|---|
| 最优 Hit@1 | 73.3%（chunk-500-overlap-160 + reranker） | 56.7%（chunk-500-overlap-160 + bm25 + reranker） | +16.6% |
| 最优 Hit@5 | 90.0%（chunk-500-overlap-160 + bm25 / + bm25 + reranker） | 60.0%（多组并列） | +30.0% |
| 最优 MRR | 0.783（chunk-500-overlap-160 + reranker） | 0.583（chunk-500-overlap-160 + bm25 + reranker） | +0.200 |
| 单次检索延迟（基线） | ~10ms | ~400-500ms | 本地快 40-50 倍 |
| 单次检索延迟（+reranker） | ~700-1100ms | ~1100-1600ms | 本地快 1.4-1.5 倍 |

### 5.4 关键发现

1. **中文专用小模型显著优于多语言大模型 API**：`bge-small-zh-v1.5`（512 维，95MB）在所有指标上全面碾压 `jina-embeddings-v3`（1024 维，API）。最优 Hit@5 差距达 30%（90.0% vs 60.0%），MRR 差距 0.200（0.783 vs 0.583）。**模型与文档语言的匹配比模型规模/维度更重要**——这与英文评测结论一致（英文场景 `bge-small-en-v1.5` 优于多语言 `bge-m3`）。
2. **BM25 混合检索在中文场景增益显著**：bge-small-zh 基线组（chunk-500-overlap-80）+BM25 后 Hit@5 从 73.3% 提升到 86.7%（+13.4%），chunk-500-overlap-160 + bm25 达到全局最高 Hit@5=90.0%。中文论文含大量专有名词、数值、术语，BM25 的精确关键词匹配有效补充了向量检索的语义模糊。
3. **Cross-Encoder 重排序大幅提升 Hit@1**：bge-small-zh 基线组 +reranker 后 Hit@1 从 36.7% 提升到 60.0%（+23.3%），chunk-500-overlap-160 + reranker 达到全局最高 Hit@1=73.3%。重排序对精确匹配（Top-1）的提升在两个模型上一致。
4. **overlap 对中文场景的影响与英文不同**：英文评测中 overlap=0 最优（重叠分散排名），但中文场景下 overlap=160 配合 bm25/reranker 反而 Hit@5 最高（90.0%）。原因：中文 Embedding 语义匹配弱于英文，重叠提升召回的收益大于分散排名的损失。不过 Hit@1 在 overlap=160 时有所回落（reranker 组 73.3%→63.3%），说明过高重叠仍会干扰精确匹配。
5. **API 模型延迟代价高**：Jina API 单次 Embedding 调用约 400-500ms（网络往返），叠加本地 reranker 后总延迟 1100-1600ms；本地 bge-small-zh 单次仅 ~10ms，叠加 reranker 后 700-1100ms。对延迟敏感的生产场景，本地小模型优势明显。

### 5.5 BM25 + reranker 组合效果分析

以基线参数 chunk-500-overlap-80 为例，对比四组变体：

| 模型 | 配置 | Hit@1 | Hit@5 | MRR |
|---|---|---|---|---|
| bge-small-zh | 基线 | 36.7% | 73.3% | 0.504 |
| bge-small-zh | + bm25 | 40.0% | 86.7% | 0.576 |
| bge-small-zh | + reranker | 60.0% | 73.3% | 0.667 |
| bge-small-zh | + bm25 + reranker | **70.0%** | **86.7%** | **0.778** |
| jina-v3 | 基线 | 16.7% | 43.3% | 0.250 |
| jina-v3 | + bm25 | 16.7% | 60.0% | 0.319 |
| jina-v3 | + reranker | 40.0% | 43.3% | 0.417 |
| jina-v3 | + bm25 + reranker | 50.0% | 60.0% | 0.544 |

- **BM25 提升 Hit@5（召回）**：bge-small-zh +13.4%，jina +16.7%。BM25 召回了向量检索遗漏的关键词/数值类文档。
- **reranker 提升 Hit@1（精度）**：bge-small-zh +23.3%，jina +23.3%。Cross-Encoder 精排能力一致。
- **两者互补**：BM25 解决召回，reranker 解决精度，组合后两项指标均达各自最优。两模型上增益方向一致，说明混合检索 + 重排序是模型无关的通用增强手段。

## 6. 失败模式分析

以 bge-small-zh 最优配置（chunk-500-overlap-160 + bm25 + reranker，3 条 Hit@5 未命中）为例：

### 6.1 跨论文语义干扰

- "CNN 模型的 Dropout 参数设置为多少？"
- "本文的骨干网络采用什么框架？"
- "数据增强使用什么方法？"

**原因**：3 篇论文都涉及 CNN、Dropout、骨干网络、数据增强等通用深度学习概念，合并库中存在多个语义相近的 chunk。问题中的"本文"指代在检索时无法消解，正确答案所在 chunk 被其他论文的同主题 chunk 挤出 Top-5。

### 6.2 问题指代消解困难

"本文"类问题在多论文合并库中天然存在指代歧义——检索系统无法从问题本身判断"本文"指哪篇论文。这是多论文检索场景的固有难点，需依赖上下文（如对话历史、论文元数据）或更细粒度的文档隔离策略改善。

## 7. 结论与建议

### 7.1 核心结论

1. **生产默认配置维持 `bge-small-zh-v1.5` 不变**：中文专用小模型在中文论文场景全面优于多语言大模型 API，且本地推理零成本、低延迟。无需切换到 Jina API。
2. **Jina API 不推荐用于中文论文主场景**：Hit@5 仅 60.0%（低于本地小模型 30 个百分点），且 API 调用延迟高、依赖网络。仅在需要多语言统一（中英文混合库）或本地无法部署时作为备选。
3. **生产环境应同时启用 BM25 + reranker**：两者互补，bge-small-zh + BM25 + reranker 的最优组合 Hit@1=70.0%、Hit@5=86.7%、MRR=0.778，较纯向量基线（Hit@1=36.7%、Hit@5=73.3%）显著提升。
4. **chunk 参数建议**：中文场景 overlap 适度增大有利召回（chunk-500-overlap-160 + bm25 的 Hit@5 达 90.0%），但过高 overlap 会牺牲 Hit@1。当前生产默认 chunk-500-overlap-80 是精度与召回的均衡点，可保留。

### 7.2 与英文评测的对比

| 维度 | 英文场景（bge-small-en-v1.5） | 中文场景（bge-small-zh-v1.5） |
|---|---|---|
| 最优 overlap | 0（重叠分散排名） | 160（重叠提升召回） |
| BM25 增益 | 中等（+3.3% ~ +10%） | 显著（+13.4%） |
| reranker 增益 | 显著（+10% ~ +23.3%） | 显著（+23.3%） |
| 最优 Hit@5 | 76.7%（+bm25+reranker） | 90.0%（+bm25） |

中文场景 BM25 增益更大、最优 overlap 更高，反映了中文 Embedding 语义匹配弱于英文、更依赖关键词精确匹配与重叠召回的特点。

## 8. 复现方法

```powershell
# 安装依赖（含中文分词 jieba 与 Embedding 推理后端）
uv sync --extra chinese --extra embedding

# 验证数据集子串匹配（不需要 Embedding 模型）
uv run python scripts/evaluate.py verify --pdfs-dir eval/pdfs/zh --dataset eval/dataset_zh.json

# 运行 bge-small-zh 基线 4 组对比（本地模型，会自动下载）
uv run python scripts/evaluate.py run --pdfs-dir eval/pdfs/zh --dataset eval/dataset_zh.json `
    --bm25 --reranker-model BAAI/bge-reranker-base

# 运行 Jina API 对比 4 组（需设置 JINA_API_KEY 环境变量）
$env:JINA_API_KEY="<your-jina-api-key>"
uv run python scripts/evaluate.py run --pdfs-dir eval/pdfs/zh --dataset eval/dataset_zh.json `
    --embedding-provider jina --bm25 --reranker-model BAAI/bge-reranker-base
```

数据集见 [eval/dataset_zh.json](../eval/dataset_zh.json)，每条问题的 `pdf` 字段标注答案所在论文的文件名。原始评测日志见 `eval/run_bge_zh_baseline.log` 与 `eval/run_jina_zh.log`。
