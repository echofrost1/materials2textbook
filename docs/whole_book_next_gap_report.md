# 整本焊接数字教材补齐缺口报告

记录时间：2026-06-17 21:27:19（Asia/Shanghai）

本报告用于把当前“整本精选演示版”继续推进到“整本试交付版”。核心原则是：不强行要求每章都有视频、PPT、参考文本，但每章必须有足够可追溯、可审核的证据。素材不足的章先进入缺口池，不硬写成正文。

## 总体结论

- 已具备较好素材基础的章节：焊接安全与设备基础、焊条电弧焊、钨极氩弧焊、焊接基本操作。
- 仍需补齐的章节：焊接缺陷与质量检验、综合实训与考核。
- 已固化两个关键脚本：`scripts/build_fixed_book_inputs.py` 和 `scripts/cut_reviewed_video_clips.py`。
- 下一版整本应固定目录，不再让 LLM 自动合并章节。

## 章节状态

### 焊接安全与设备基础

- 状态：ready
- 素材包记录数：168
- Agent_Keep 素材：视频 12，PPT 104，文档/结构化 52
- readiness：ready 8 / partial 0 / not_ready 0
- 当前知识点均达到 ready。

### 焊条电弧焊

- 状态：usable_with_gaps
- 素材包记录数：511
- Agent_Keep 素材：视频 35，PPT 390，文档/结构化 86
- readiness：ready 8 / partial 1 / not_ready 0
- 需补知识点：
  - 章节概述: partial_ready，当前证据 4 条
- 缺口记录：
  - 章节概述: 只有 4 条可用证据，只适合短段落或谨慎生成

### 钨极氩弧焊

- 状态：ready
- 素材包记录数：547
- Agent_Keep 素材：视频 40，PPT 415，文档/结构化 92
- readiness：ready 10 / partial 0 / not_ready 0
- 当前知识点均达到 ready。

### 焊接基本操作

- 状态：usable_with_gaps
- 素材包记录数：112
- Agent_Keep 素材：视频 20，PPT 2，文档/结构化 90
- readiness：ready 6 / partial 2 / not_ready 0
- 需补知识点：
  - 焊接姿势: partial_ready，当前证据 2 条
  - 收弧: partial_ready，当前证据 2 条
- 缺口记录：
  - 焊接姿势: 只有 2 条可用证据，只适合短段落或谨慎生成
  - 收弧: 只有 2 条可用证据，只适合短段落或谨慎生成

### 焊接缺陷与质量检验

- 状态：needs_material_backfill
- 素材包记录数：294
- Agent_Keep 素材：视频 12，PPT 131，文档/结构化 151
- readiness：ready 2 / partial 2 / not_ready 1
- 需补知识点：
  - 缺陷原因: not_ready，当前证据 0 条
  - 缺陷纠正: partial_ready，当前证据 1 条
  - 返修与预防: partial_ready，当前证据 2 条
- 缺口记录：
  - 缺陷原因: 没有可用证据，不能进入教材正文生成
  - 缺陷纠正: 只有 1 条可用证据，只适合短段落或谨慎生成
  - 返修与预防: 只有 2 条可用证据，只适合短段落或谨慎生成

### 综合实训与考核

- 状态：usable_with_gaps
- 素材包记录数：201
- Agent_Keep 素材：视频 0，PPT 1，文档/结构化 200
- readiness：ready 3 / partial 0 / not_ready 2
- 需补知识点：
  - 操作步骤: not_ready，当前证据 0 条
  - 题库练习: not_ready，当前证据 0 条
- 缺口记录：
  - 操作步骤: 没有可用证据，不能进入教材正文生成
  - 题库练习: 没有可用证据，不能进入教材正文生成

## 汇总表

| 章节 | 状态 | 知识点 | ready | partial | not_ready | Agent视频 | Agent PPT | Agent文档 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 焊接安全与设备基础 | ready | 8 | 8 | 0 | 0 | 12 | 104 | 52 |
| 焊条电弧焊 | usable_with_gaps | 9 | 8 | 1 | 0 | 35 | 390 | 86 |
| 钨极氩弧焊 | ready | 10 | 10 | 0 | 0 | 40 | 415 | 92 |
| 焊接基本操作 | usable_with_gaps | 8 | 6 | 2 | 0 | 20 | 2 | 90 |
| 焊接缺陷与质量检验 | needs_material_backfill | 5 | 2 | 2 | 1 | 12 | 131 | 151 |
| 综合实训与考核 | usable_with_gaps | 5 | 3 | 0 | 2 | 0 | 1 | 200 |

## 下一步执行建议

1. 用 `scripts/build_fixed_book_inputs.py` 生成固定目录整本输入，默认只纳入成熟章节。
2. 用 `scripts/run_full_digital_textbook.py --book-mode` 跑固定目录整本精选版。
3. 用 `scripts/cut_reviewed_video_clips.py` 对生成后的 `digital_book/` 做物理切片并重新打包。
4. 针对“焊接缺陷与质量检验”“综合实训与考核”回到 00/01 台账补找 PPT、视频、题库或评分表。
5. 修 writer prompt，把每个事实段落强制绑定证据 ID，目标是把 `citation_coverage_rate` 提升到 0.8 以上。
