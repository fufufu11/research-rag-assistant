# 答案质量评测报告

> 本报告记录阶段 9.3 答案质量评测的方法、环境、结果与结论，用 LLM-as-judge 量化生成阶段答案质量。

## 1. 评测目标

扩展检索评测到生成阶段，量化 LLM 答案质量，回答：

1. **忠实度**：答案是否基于检索上下文，有无幻觉？
2. **相关性**：答案是否直接回答了用户问题？
3. **完整性**：答案是否覆盖了上下文中的相关要点？
4. **引用正确性**：答案中 `[C1]` 标记是否正确指向支持性上下文？

## 2. 评测方法

**LLM-as-judge**：用一个 LLM 对（问题、上下文、答案）三元组打分，四项指标各 1-5 分（5 分最好），并给出简短理由。引用正确性辅以服务端客观规则校验（编号是否越界、是否有引用）。

| 指标 | 含义 | 5 分 | 1 分 |
|---|---|---|---|
| faithfulness 忠实度 | 答案声明是否被上下文支持 | 全部有据无幻觉 | 大量编造 |
| relevancy 相关性 | 答案是否回答问题 | 完全切题 | 完全跑题 |
| completeness 完整性 | 是否覆盖上下文相关要点 | 完整覆盖 | 严重遗漏 |
| citation_correctness 引用正确性 | `[C1]` 是否正确指向支持性上下文 | 全部正确 | 无引用/全错 |

> 均值仅统计成功解析的题；解析失败的题不计入分子分母，避免拉低分数。

## 3. 评测环境

| 项 | 值 |
|---|---|
| Generator LLM | `deepseek-ai/DeepSeek-V3.2` |
| Judge LLM | `deepseek-ai/DeepSeek-V3.2` |
| Embedding 模型 | `BAAI/bge-small-en-v1.5` |
| Reranker 模型 | `BAAI/bge-reranker-base` |
| BM25 混合检索 | 启用 |
| 跨页切分 | 启用 |
| 生成阶段 top_k | 8 |
| 数据集 | `eval\dataset.json` |
| Python | 3.11 |

## 4. 评测数据集

共 30 条问题，每条跑完整 RAG 流程（检索 → 重排 → LLM 生成 → judge 打分）。

## 5. 评测结果

### 5.1 汇总指标

| 忠实度 | 相关性 | 完整性 | 引用正确性 |
|---|---|---|---|
| 5.00 | 4.96 | 4.62 | 4.54 |

### 5.2 引用客观校验统计

| 指标 | 值 |
|---|---|
| 含引用标记的答案数 | 27 / 30 |
| 引用全部在范围内（无越界）的答案数 | 30 / 30 |
| 证据不足（[INSUFFICIENT_EVIDENCE]）答案数 | 3 |
| judge 解析失败数 | 4 |

### 5.3 每条问题得分

| # | 问题 | 忠实度 | 相关性 | 完整性 | 引用正确性 | 引用校验 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | What type of network is EEGNet? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 2 | What convolutions did EEGNet introduce from computer vision? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 3 | What is a Brain-Computer Interface? | 5.0 | 5.0 | 3.0 | 5.0 | 有引用 |  |
| 4 | What are the keywords of the EEGNet paper? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 5 | What are Error-Related Negativity potentials? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 6 | What type of events are ERN related to? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 7 | What does the SMR task in EEGNet classify? | 5.0 | 4.0 | 1.0 | 1.0 | 无引用 |  |
| 8 | What does the EEGNet architecture figure show? | - | - | - | - | 有引用 | JSON 解析失败：Expecting ',' delimiter: line 1 column 372 (char 371) |
| 9 | What is the title of the EEGNet paper? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 10 | What EEG feature extraction methods does EEGNet encapsulate? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 11 | What mechanism is the Transformer based on? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 12 | What does the Transformer dispense with? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 13 | How much BLEU did the Transformer achieve on WMT 2014 Englis… | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 14 | How many GPUs and days were used to train the Transformer? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 15 | How many layers does the Transformer encoder have? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 16 | How many layers does the Transformer decoder have? | - | - | - | - | 有引用 | JSON 解析失败：Expecting ',' delimiter: line 1 column 156 (char 155) |
| 17 | What is the formula for Scaled Dot-Product Attention? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 18 | How many attention heads does the Transformer use? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 19 | What functions are used for positional encoding in the Trans… | - | - | - | - | 有引用 | JSON 解析失败：Expecting ',' delimiter: line 1 column 616 (char 615) |
| 20 | How is the residual connection implemented in the Transforme… | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 21 | How many images did AlexNet classify in ImageNet LSVRC-2010? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 22 | What are the top-1 and top-5 error rates of AlexNet? | 5.0 | 5.0 | 5.0 | 1.0 | 无引用 |  |
| 23 | How many parameters and neurons does AlexNet have? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 24 | How many convolutional and fully connected layers does AlexN… | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 25 | How does AlexNet accelerate convolution operations? | 5.0 | 5.0 | 1.0 | 1.0 | 无引用 |  |
| 26 | What top-5 error rate did AlexNet achieve in ILSVRC-2012? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 27 | How many GPUs was AlexNet trained on? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 28 | What is the second form of data augmentation in AlexNet? | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 29 | How many neurons does each fully connected layer in AlexNet … | 5.0 | 5.0 | 5.0 | 5.0 | 有引用 |  |
| 30 | What happens if middle convolutional layers are removed from… | - | - | - | - | 有引用 | JSON 解析失败：Expecting ',' delimiter: line 1 column 396 (char 395) |

## 6. 失败案例分析

共 7 条低分或失败 case（任一指标 ≤ 2 或解析失败）：

### 案例 1：What does the SMR task in EEGNet classify?

- 忠实度: 5.0 — 当上下文证据不足以回答问题或回答为'[INSUFFICIENT_EVIDENCE]'时，该回应是忠实且诚实的。
- 相关性: 4.0 — 该回应直接解决了问题中关于分类具体内容的询问，表明没有足够证据，因此是相关的，但未能提供积极信息。
- 完整性: 1.0 — 回答仅表明证据不足，完全没有提供任何关于SMR任务分类内容的信息，关键信息完全缺失。
- 引用正确性: 1.0 — 根据规则，当答案是'[INSUFFICIENT_EVIDENCE]'时，该维度必须打1分。

### 案例 2：What does the EEGNet architecture figure show?

**错误**：JSON 解析失败：Expecting ',' delimiter: line 1 column 372 (char 371)

### 案例 3：How many layers does the Transformer decoder have?

**错误**：JSON 解析失败：Expecting ',' delimiter: line 1 column 156 (char 155)

### 案例 4：What functions are used for positional encoding in the Transformer?

**错误**：JSON 解析失败：Expecting ',' delimiter: line 1 column 616 (char 615)

### 案例 5：What are the top-1 and top-5 error rates of AlexNet?

- 忠实度: 5.0 — 答案明确指出证据不足，这与上下文内容一致，因为上下文包含多个数据集（如ILSVRC-2010、ILSVRC-2012和另一个未命名数据集）的top-1和top-5错误率，但没有明确指定AlexNet在哪个数据集上的结果。
- 相关性: 5.0 — 答案直接回应了问题，表明无法提供所请求的AlexNet top-1和top-5错误率，因为上下文证据不足。
- 完整性: 5.0 — 在给定上下文中，存在多个可能相关的错误率，但问题未指定数据集，答案正确地指出无法确定具体数值，这完整覆盖了上下文的模糊性。
- 引用正确性: 1.0 — 答案未提供任何引用标记（如[C1]、[C2]等），因为它返回了[INSUFFICIENT_EVIDENCE]。根据要求，在这种情况下，引用正确性得分为1分。

### 案例 6：How does AlexNet accelerate convolution operations?

- 忠实度: 5.0 — 答案为 [INSUFFICIENT_EVIDENCE]，表示上下文无相关证据，这本身是对事实的忠实描述。
- 相关性: 5.0 — 答案直接回应了问题，指出证据不足，因此是直接相关的。
- 完整性: 1.0 — 答案未覆盖任何可能解释卷积加速的方法，未能提供任何信息。
- 引用正确性: 1.0 — 答案未提供任何引用标记，而[INSUFFICIENT_EVIDENCE]的答案在此维度应得1分。

### 案例 7：What happens if middle convolutional layers are removed from AlexNet?

**错误**：JSON 解析失败：Expecting ',' delimiter: line 1 column 396 (char 395)

## 7. 结论与改进方向

- **忠实度**反映幻觉控制效果，低分提示需加强 Prompt 约束或检索质量。
- **相关性**低分多为检索未命中（问题与上下文语义距离远），可结合检索 Hit@5 分析。
- **完整性**低分提示上下文信息不足或切分过细导致答案片面。
- **引用正确性**低分结合客观校验：无引用需加强 Prompt 引用约束；越界需检查模型编号输出。
