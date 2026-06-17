# 数字教材生成质量优化计划

记录时间：2026-06-17 16:50（Asia/Shanghai）

本文记录 2026-06-17 这轮“焊接技术数字教材”LLM 生成后的问题判断、原因分析和下一步优化方案。当前结论很明确：不是“LLM 不会写”，而是进入写作阶段的证据组织还不够像教材素材库，所以模型只能写出一个偏短、偏概括的样稿。

## 1. 本次运行情况

本次运行命令属于整本教材模式：

```text
--book-mode
--max-video-records 40
--max-document-records 500
--max-input-tokens 60000
--summarize-over-budget
--use-llm
```

本次产物位置：

```text
work_materials/work_material1/05_final_deliverables/agent_workflow/
work_materials/work_material1/05_final_deliverables/digital_book/
work_materials/work_material1/05_final_deliverables/digital_book.zip
```

本次关键指标：

```text
source_records: 540
video_source_records: 40
document_source_records: 500
token_budget_original_estimated_tokens: 102774
token_budget_max_input_tokens: 60000
token_budget_kept_source_chunks: 207
token_budget_dropped_chunks: 333
token_budget_summary_chunks: 42
token_budget_summarized_source_chunks: 333
evidence_chunks: 249
chapters: 5
knowledge_points: 6
fact_issue_count: 966
pedagogy_issue_count: 38
citation_coverage_rate: 0.4762
paragraph_support_rate: 0.0
claim_support_rate: 0.0
overall_quality_score: 0.509
```

补充问题记录：

- 第一次 LLM 运行中途被中断，后台残留了生成进程，后续已手动停止生成相关进程。
- 新模型曾返回一次空内容，导致进度打印处报错。已修复 LLM provider 和缓存层：空响应不会被当成成功，也不会写入缓存。
- 已给生成脚本补充进度输出，后续终端会显示 `[runner]`、`[workflow]`、`[llm] request ...`。

## 2. 当前问题判断

当前生成结果可以作为“LLM 生成教材样稿 / 可展示版本”，但还不能作为最终可交付教材稿。

主要原因不是本地电脑跑不动，也不是单纯模型能力不足，而是写作输入还没有被组织成真正的教材素材包：

- 证据是散的，直接来自 `video_segments.jsonl`、`ppt_assets.jsonl` 等处理结果。
- 章节和知识点粒度太粗，目前只有 5 个章、6 个知识点。
- 视频 ASR、PPT 文本中仍有乱码、错词、片段边界不稳等问题。
- 写作 agent 更像“根据证据生成摘要教材”，不是“按教材体例扩写完整章节”。
- 证据引用支撑率低，正文段落和证据块没有稳定绑定，审核阶段会把大量内容判成风险。

## 3. 为什么内容偏少

内容偏少主要有五个原因。

第一，本次是受限试跑，不是全量生产。命令限制了：

```text
--max-video-records 40
--max-document-records 500
```

也就是说，不是素材库只有这么多，而是为了降低第一次 LLM 生成的成本、时间和失败风险，主动限制了输入量。

第二，本次是整本模式。40 条视频和 500 条 PPT/文档记录被摊到整本 5 个章里，每章得到的有效证据不多。

第三，token 预算压缩了证据。原始证据估算约 102774 tokens，预算是 60000 tokens，因此脚本保留了 207 条原始证据，把 333 条证据压缩成 42 条摘要证据，最终进入正文组织的是 249 个 evidence chunks。

第四，知识点粒度太粗。教材要写厚，需要足够多的节、知识点、小任务和操作场景。只有 6 个知识点时，模型自然倾向于写成概括稿。

第五，证据质量不稳。ASR 错词、乱码、PPT 文本抽取噪声会让模型保守，不敢展开太多具体操作细节。

## 4. 总体优化方向

下一步不应该继续盲目整本生成，而应该先把“钨极氩弧焊”这一章做厚、做稳。单章跑通后，再扩展到其他章节，最后合成整本教材。

推荐路线：

```text
固定教材目录
-> 生成 chapter_evidence_pack
-> 生成 chapter_readiness_report
-> 只选“钨极氩弧焊”重跑
-> 改 writer prompt 按教材体例扩写
-> 审核引用和质量
-> 质量达标后再扩到整本
```

## 5. 优化一：先把教材骨架做厚

不要让 LLM 自由决定整本教材目录。先固定一个焊接教材目录草案，作为生成和素材组织的主骨架：

```text
第一章 焊接安全与基础知识
第二章 焊条电弧焊
第三章 钨极氩弧焊
第四章 焊接设备与材料
第五章 焊接缺陷与质量检验
第六章 综合实训与考核
```

每章下面要拆到二级、三级知识点。以“钨极氩弧焊”为例，至少要拆成：

```text
基本原理
设备组成
焊前准备
非接触引弧
送丝操作
收弧操作
打底焊
填充焊
盖面焊
常见缺陷与纠正
```

这样模型才有足够的结构空间写正文，而不是把一章写成几段概括。

## 6. 优化二：把散素材变成章节素材包

当前证据虽然已经被处理成 JSONL，但它们还不是教材可直接使用的“章节素材包”。

下一步应生成：

```text
chapter_evidence_pack.jsonl
```

每个知识点下固定收集：

```text
1-3 个主视频片段
1-3 页 PPT 图文
1-2 段参考文本
关键帧图片
可用术语
常见错误
操作步骤
注意事项
可出题点
```

也就是说，不是把 `video_segments.jsonl` 和 `ppt_assets.jsonl` 直接丢给 writer，而是先整理成“这一节能讲什么、能引用什么、能展示什么”。

建议新增脚本：

```text
scripts/build_chapter_evidence_pack.py
```

建议输入：

```text
01_manifest_inventory/asset_block_map.xlsx
02_working_processing/json/video_segments.jsonl
02_working_processing/json/ppt_assets.jsonl
02_working_processing/json/reference_text_assets.jsonl
02_working_processing/json/structured_assets.jsonl
```

建议输出：

```text
05_final_deliverables/chapter_work/<chapter_slug>/chapter_evidence_pack.jsonl
05_final_deliverables/chapter_work/<chapter_slug>/chapter_evidence_pack.xlsx
```

## 7. 优化三：增加证据质量门槛

现在 `Pending_Agent_Review`、`Pending_Manual_Timecode`、`Summary_Needs_Source_Review` 都进入了正文链路，导致风险很高。

建议改成：

```text
Agent_Keep
-> 优先进入正文，可作为主证据

Pending_Agent_Review
-> 只能作为参考证据，不作为核心定义或关键步骤来源

Pending_Manual_Timecode
-> 不能作为主视频，只能作为待复核素材

Summary_Needs_Source_Review
-> 可以辅助补充上下文，但不能替代原始证据

ASR 乱码严重或术语错误明显的片段
-> 进入待清理池，不进入正文主证据

没有知识点证据的 PPT 页
-> 不进入正文，只保留在素材索引中
```

这样正文可能会少一些杂质，但质量会明显更稳。

建议新增字段：

```text
evidence_role: primary / support / reference / hold
quality_gate_status: pass / weak / hold / reject
quality_gate_reason
source_review_status
```

## 8. 优化四：改写 writer prompt，让它按教材体例展开

当前 writer 更像“按证据生成摘要”。下一步要让它按教材章节体例写。

每个知识点固定生成：

```text
学习目标
知识讲解
操作步骤
工艺要点
常见错误
图/视频观察任务
小结
练习题
```

建议字数要求：

```text
每个小节正文不少于 600-1000 字
每章不少于 3000-5000 字
每个知识点至少引用 2 条证据
每章至少包含 5 个视频观察任务
每章至少包含 10 张关键图或 PPT 图
```

写作 prompt 还应该明确：

- 不允许只写泛泛的课程介绍。
- 必须围绕操作流程、工艺参数、常见错误、质量判断展开。
- 没有证据的结论要降级为“提示”或“不写”。
- 引用必须绑定到具体证据块，而不是只在章末堆引用。

## 9. 优化五：改成按章生成，再合成整本

本次整本运行触发了约 600 次 LLM 请求，成本高，而且问题不好定位。

下一步建议先跑：

```text
第三章 钨极氩弧焊
```

单章生成命令的目标不是“少量试跑”，而是“只跑一章，但这一章吃足相关素材”。

建议参数方向：

```text
--chapter "钨极氩弧焊"
--max-video-records 0
--max-document-records 0
--max-input-tokens 120000
--max-chunks-per-knowledge-point 12
--use-llm
```

后续建议新增脚本：

```text
scripts/run_chapter_digital_textbook.py
```

职责：

```text
输入章节名
-> 筛选该章视频、PPT、参考文本、图片
-> 生成 chapter_evidence_pack.jsonl
-> 生成 chapter_readiness_report.xlsx
-> LLM 写该章正文
-> 输出 chapter_textbook_final.md
-> 输出 chapter_digital_book.json
```

等单章质量稳定后，再新增：

```text
scripts/run_all_chapters_digital_textbook.py
```

支持：

```text
--parallel 3
```

但现在不建议马上并行。应该先串行跑通一章，确认章节长度、引用、视频、图片和质量审核都稳定，再开启并行。

## 10. 优化六：生成教材扩写前检查报告

在真正写正文前，应先输出：

```text
chapter_readiness_report.xlsx
```

它用于判断某章是否适合生成正文。建议字段：

```text
chapter
knowledge_point_count
video_segment_count
primary_video_count
ppt_page_count
ppt_image_count
reference_text_count
structured_asset_count
agent_keep_count
pending_review_count
manual_timecode_count
summary_needs_source_review_count
asr_risk_count
ocr_or_text_noise_count
missing_video_knowledge_points
missing_ppt_knowledge_points
missing_reference_text_knowledge_points
readiness_status
readiness_reason
recommended_action
```

判断规则：

```text
ready
-> 知识点足够，主视频/PPT/文本证据基本齐全，可以生成正文

partial_ready
-> 可以生成样稿，但要标注哪些小节证据不足

not_ready
-> 不建议硬写，先补素材或清理证据
```

如果某章证据不足，就不要硬写。硬写出来通常就是空话多、内容薄、引用弱。

## 11. 单章试点验收标准

以“钨极氩弧焊”为第一章试点时，建议验收标准如下：

```text
至少 8-10 个知识点
每个知识点有视频/PPT/文本证据
章节正文不少于 5000 字
至少 10 张关键图或 PPT 图
至少 5 个视频观察任务
每个知识点至少 2 条有效证据
citation_coverage_rate >= 0.8
high_issue_count 明显下降
正文段落能追溯到具体 evidence chunk
生成的网页可正常打开，视频/图片路径可访问
```

## 12. 后续实施顺序

建议按以下顺序执行：

```text
1. 固化焊接教材目录和“钨极氩弧焊”知识点清单
2. 编写 build_chapter_evidence_pack.py
3. 编写 chapter_readiness_report.xlsx 生成逻辑
4. 修改证据质量门槛，区分 primary/support/reference/hold
5. 修改 writer prompt，按教材体例扩写
6. 新增 run_chapter_digital_textbook.py
7. 只跑“钨极氩弧焊”单章
8. 检查字数、引用率、图片、视频观察任务和审核报告
9. 单章达标后，再扩展到其他章节
10. 最后合并整本 digital_book
```

## 13. 当前不建议做的事

当前不建议继续直接整本 LLM 生成，因为：

- 成本高，单轮请求数可能达到数百次。
- 问题分散，难判断是哪一章、哪类证据导致质量低。
- 每章分到的上下文有限，正文会偏短。
- 章节粒度和证据包还没整理好，模型容易写成概括稿。

当前也不建议马上并行跑多章。并行可以做，但应该在单章质量验证通过后再做。

## 14. 当前结论

这次 LLM 生成证明主链路已经打通：

```text
本地素材处理结果
-> LLM 资源分析
-> 整本规划
-> 正文生成
-> 审核
-> 修订
-> 电子教材网页
-> zip 包
```

但它也暴露了下一阶段最核心的问题：我们需要把“素材处理结果”进一步组织成“教材章节素材库”。

下一步工作的重点不是换模型，也不是无限扩大输入量，而是：

```text
按章组织证据
按知识点做厚素材包
按教材体例扩写
按证据质量控制进入正文的材料
```

先把“钨极氩弧焊”这一章做厚、做稳。这个章一旦打磨通，整本教材的生产方式就清楚了。
