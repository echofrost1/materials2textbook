# 教材素材处理精简前瞻版

这份文档是当前流水线的前瞻执行版。它不追求一次性做完整教材系统，而是先跑通一条可信、可复核、可扩展的素材处理链路。

## 1. 当前判断

完整方案的骨架是对的，但需要把几个风险前置讲清楚：

- 第一阶段不能假装“全量有意义分类完成”。只靠文件名、目录名、PPT 标题，很多素材只能粗分或待确认。
- `fallback` 不是可靠分类。它只能表示“已登记、未丢失、需要后续确认”，不能作为后续教材检索的主要依据。
- 素材大块不能强制单归属。一个视频可能同时属于“焊接基本操作”和“焊接安全”，后续检索不能因为单分类而漏掉。
- 去重应尽量提前，在建立素材大块前先识别完全重复和明显相似素材，避免重复文件干扰统计和大块覆盖度。
- 长视频自动切片是重工程。第一版应先跑 ASR + 人工时间码，验证片段表和教材引用链路，再逐步加入自动切片。

## 2. 一句话目标

先把原始资料变成一个可信素材索引：

```text
不动原始文件
→ 建台账
→ 先去重
→ 粗可信分类 + 待确认
→ 建立可多归属的素材大块
→ 只对选中大块做深处理
→ 长视频形成可审核片段
→ 再进入数据库 / 向量库
```

## 3. 目录定位

```text
Textbook_Project/
├─ 00_raw_client_materials/      原始资料，只读保存，不改名、不移动
├─ 01_manifest_inventory/        台账、分类表、去重表、素材大块映射表
├─ 02_working_processing/        深处理中间产物，如转写、关键帧、片段 JSONL
├─ 03_review_manual_check/       人工/审查记录区，可暂时为空
├─ 04_assets_by_course/          索引层：按科目、素材大块、知识点组织索引
└─ 05_final_deliverables/        最终教材、样章、导出片段包、交付说明
```

`04_assets_by_course/` 只保存索引文件，不直接复制原始大文件。索引中记录 `original_path`、必要时记录绝对路径和相对路径；不要依赖 Windows 软链接或 `.lnk` 快捷方式作为主机制。视频和 PPT 可能很大，复制多份会造成空间爆炸，也会让版本管理变复杂。

## 4. 精简流程

### 阶段 1：全量登记

输入：`00_raw_client_materials/`

处理：

```text
扫描全部文件
→ 生成 assets_manifest.xlsx
→ 记录路径、类型、大小、时长、hash
→ 顺手记录分类可用证据
→ 生成 asset_id
```

输出：

```text
01_manifest_inventory/assets_manifest.xlsx
02_working_processing/json/assets_manifest.json
```

登记阶段要尽量把后续分类需要的轻量证据一起记下来，避免分类阶段反复扫描文件：

```text
directory_path
source_week
source_folder
filename_hint
normalized_filename
file_title_if_readable
ppt_text_preview
document_text_preview
duration_or_pages
```

说明：

- `filename_hint` 来自文件名清洗后的关键词。
- `file_title_if_readable` 优先取 PPT 首页标题、PDF 标题或文档标题。
- `ppt_text_preview` / `document_text_preview` 只取前若干字符，作为轻量分类证据，不等于全文深处理。

验收：

- 每个原始文件都有一行台账。
- 原始文件不移动、不改名、不删除。
- 台账能通过 `original_path` 指回原文件。
- 台账中有后续分类可直接使用的轻量证据字段。

### 阶段 2：提前去重和相似关系识别

去重应在建立素材大块前进行。

处理：

```text
按 file_hash 识别完全重复
→ 按文件名、目录、时长识别明显同主题素材
→ 标记 exact_duplicate / similar / possible_shorter_or_extended
→ 生成 preferred_for_use 初稿
```

输出：

```text
01_manifest_inventory/duplicate_groups.xlsx
01_manifest_inventory/active_assets.xlsx
```

`active_assets.xlsx` 是后续建大块、建索引、进 02 的统一入口，避免每一步各自判断重复文件。

建议字段：

```text
asset_id
original_path
duplicate_group_id
is_exact_duplicate
preferred_asset_id
active_for_index
active_for_processing
reason
```

说明：

- 完全重复不删除，只标记推荐版本。
- 后续建立大块、统计覆盖度时，应优先看有效文件集，而不是把重复文件当成新增覆盖。
- 长短版本的最终判断可能要等 ASR/关键帧后才能确认，第一阶段只做疑似标记。

### 阶段 3：粗可信分类 + 待确认

分类可以在去重之后运行，也可以和去重并行运行；关键是分类输入应优先来自 `assets_manifest.xlsx` 里登记好的轻量证据字段，而不是每次重新扫描原始文件。

第一阶段目标不是“全量精准分类”，而是：

```text
全量登记
→ 能可信分类的先分类
→ 不可信的进入待确认
→ 不用 fallback 假装已经分类完成
```

分类状态建议：

| 状态 | 说明 | 是否可作为检索主依据 |
|---|---|---|
| `exact` | 证据明确，可以归到具体知识点 | 可以 |
| `coarse` | 只能归到大主题或粗知识点 | 可以作为粗筛 |
| `uncertain` | 有一些线索，但证据不足 | 不建议直接用于教材检索 |
| `fallback` | 仅用文件名临时挂起，等同待确认 | 不可作为可靠分类 |

输出：

```text
01_manifest_inventory/front_classification_all_materials.xlsx
```

建议字段：

```text
asset_id
subject_cn
subject_code
knowledge_point_cn
knowledge_point_code
classification_status
classification_basis
classification_confidence
needs_confirmation
uncertain_reason
```

验收：

- 每个文件都有登记状态。
- `exact/coarse/uncertain/fallback` 分清楚。
- `fallback` 和 `uncertain` 必须进入后续确认队列，不能当成分类完成。

### 阶段 4：建立可多归属的素材大块

一本教材不应该从全量资料里检索。要先选素材大块，再在大块内检索。

但素材大块不能强制单归属。建议使用映射表：

```text
asset_id | material_block_code | relation_type | confidence | scope_note
```

字段说明：

| 字段 | 说明 |
|---|---|
| `asset_id` | 素材编号 |
| `material_block_code` | 素材大块 code |
| `relation_type` | primary / secondary / candidate / exclude |
| `confidence` | 归属置信度 |
| `scope_note` | 归入或排除原因 |

大块定义表也要维护：

```text
material_block_code
material_block_cn
include_scope
exclude_scope
block_scope_note
```

两张表的关系要分清：

```text
material_blocks.xlsx = 大块字典表，定义有哪些素材大块、边界是什么
asset_block_map.xlsx = 素材-大块多对多关系表，记录每个素材属于哪些大块
```

初版维护方式：

```text
规则脚本根据 subject、knowledge_point、文件名、目录名生成初稿
→ AI 或人工抽查低置信度和跨块素材
→ 人工确认后更新 asset_block_map.xlsx
→ 后续新增素材只增量更新映射表，不重填全表
```

`relation_type` 建议规则：

- `primary`：该素材主要服务这个大块。
- `secondary`：该素材也可被这个大块引用，但不是主要用途。
- `candidate`：疑似相关，需要复核。
- `exclude`：明确不属于该大块，用于记录边界判断。

初版素材大块：

| 素材大块 | code | 包含 | 排除/注意 |
|---|---|---|---|
| 焊接鉴定与试题 | `welding_assessment_exam` | 题库、鉴定 Excel | 通常不做视频深处理 |
| 焊条电弧焊 | `shielded_metal_arc_welding` | 焊条、药皮、焊芯、焊条电弧焊操作 | 如果只是通用安全，副归属到安全大块 |
| 焊接基本操作 | `welding_basic_operation` | 坡口、运条、收弧、焊缝连接、参数选择、缺陷 | 与安全/设备相关内容可多归属 |
| 焊接设备与安全 | `welding_equipment_safety` | 设备、电源、焊钳、电缆、面罩、安全防护 | 不把所有焊接操作都放进来 |
| 钨极氩弧焊 | `tig_welding` | 非接触引弧、送丝、钨极、TIG 操作 | 与坡口/安全交叉时允许副归属 |
| 气焊与气割 | `gas_welding_and_cutting` | 切割速度、预热火焰、焊炬角度、气割安全 | |
| 机械制图-投影法 | `mechanical_drawing_projection` | 中心投影、正投影、斜投影 | 不默认进入焊接教材检索 |
| 工程材料-性能测试 | `engineering_material_testing` | 布氏硬度、洛氏硬度、冲击韧性 | 不默认进入焊接教材检索 |
| 教材参考资料 | `textbook_reference` | PDF 教材、综合参考资料 | 用于纲目和术语参考，不直接当片段素材 |

输出：

```text
01_manifest_inventory/material_blocks.xlsx
01_manifest_inventory/asset_block_map.xlsx
```

后续教材检索必须先过滤素材大块。例如：

```text
目标教材：特殊焊接技术初级
允许检索大块：焊条电弧焊、焊接基本操作、钨极氩弧焊、气焊与气割、焊接设备与安全
不默认检索：机械制图、工程材料、无关题库
```

### 阶段 5：生成索引层

输出目录建议：

```text
04_assets_by_course/
└─ 焊接技术_welding_technology/
   └─ 焊接基本操作_welding_basic_operation/
      └─ 焊接坡口_welding_groove/
         ├─ assets_index.xlsx
         └─ asset_cards.jsonl
```

要求：

- 只保存索引文件。
- 不复制原始大文件。
- 如果一个素材多归属，可以出现在多个索引中，但都指向同一个 `original_path`。
- 索引里同时保留 `original_path` 和项目内相对路径；跨机器迁移时，以项目根目录 + 相对路径重建引用。

### 阶段 6：决定哪些素材进入 02 深处理

`02_working_processing/` 是必须存在的深处理工作区，但不是所有素材都要进入。

处理优先级决策表：

| 素材类型 | 高价值情况 | 默认处理路径 | 不建议做什么 |
|---|---|---|---|
| 长视频 | 属于目标大块，可能含多个教学动作 | 转码、ASR、人工时间码、关键帧 | 不要一开始全自动切片全库视频 |
| 短视频 | 明确知识点、画面可用 | ASR/抽帧，可直接作为候选片段 | 不重复处理完全重复版本 |
| 音频 | 讲解清楚、对应明确知识点 | ASR 转写、摘要、标签 | 不做关键帧/OCR |
| PPT/PPTX | 章节结构清楚、含核心知识 | 提取文本、图片、页码索引 | 不必按视频流程切片 |
| PDF/Word | 教材或标准参考 | 提取目录、正文、术语 | 不当作视频片段素材 |
| Excel/题库 | 题目、鉴定、清单 | 表格结构化、题目分类 | 不做 ASR/抽帧 |
| SWF | 内容独有且确实有价值 | 先标低优先级，必要时转码/录屏 | 不默认全量转码 |
| fallback/uncertain | 文件名弱、分类不可信 | 先确认价值，再决定是否深处理 | 不直接进入教材生成 |

进入 02 的条件：

```text
属于目标教材允许的大块
且不是低价值重复素材
且需要转写、抽帧、OCR、切片或结构化提取
```

02 中间产物：

```text
transcripts/
keyframes/
converted_mp4/
audio/
asset_cards/
json/
```

### 阶段 7：长视频片段处理

长视频最终要变成可插入教材的小片段，但第一版不要把自动切片做成黑盒。

#### 7.1 MVP 版本

先跑最简单、最可控的版本：

```text
长视频
→ 保留原始文件
→ 统一转码 MP4
→ 抽取音频
→ ASR 转写，生成带时间戳字幕
→ 人工或半人工标 start_time / end_time
→ 抽关键帧
→ AI 根据字幕 + 关键帧生成摘要、知识点、标签、推荐章节、有用程度评分
→ 人工复核片段是否可用
→ 写入 xlsx + jsonl
```

MVP 先不强依赖自动切片。原因是实操类焊接视频字幕可能稀疏，真正的边界经常在动作和画面里。

#### 7.2 增强版本

MVP 跑通后，再加入自动候选片段：

```text
字幕停顿
+ 语义变化
+ 画面变化
+ 关键帧 OCR
→ 自动生成候选片段
→ 人工 / 审查 Agent 复核
```

片段时长建议：

- 常规候选片段：30s-3min。
- 教材优先使用：1-2min。
- 不是硬规则。判断标准是“是否完整表达一个教学动作或知识点”。

MVP 片段表字段只保留当前能稳定生产和审核的内容：

```text
clip_id
source_asset_id
source_video
start_time
end_time
subject
material_block
knowledge_point
clip_summary
tags
recommended_chapter
usefulness_score
quality_score
transcript_text
ocr_text
keyframe_paths
evidence_text
boundary_reason
review_status
review_comment
clip_output_path
```

其中：

- `evidence_text` 记录支持这个片段有用的字幕、OCR 或人工说明。
- `boundary_reason` 记录为什么从这里开始、到这里结束。
- `clip_output_path` 可以先为空；MVP 阶段可以只记录原视频时间码，不一定马上导出片段文件。

增强版再加入：

```text
overlap_with
unique_time_range
duplicate_clip_group_id
auto_boundary_score
```

输出：

```text
01_manifest_inventory/video_segments.xlsx
02_working_processing/json/video_segments.jsonl
```

### 阶段 8：进入数据库 / 向量库

不要把原始长视频直接塞进向量库。优先入库：

```text
片段级字幕文本
片段摘要
知识点标签
推荐章节
原始路径和时间码
关键帧 OCR 文本
质量和可用性评分
```

检索时必须带过滤条件：

```text
subject
material_block
knowledge_point
review_status
quality_score
```

这样生成教材时不会从全量资料里乱搜。

## 5. 当前已完成和缺口

已完成或已有雏形：

- 已有全量扫描和台账。
- 已有前置分类表：`front_classification_all_materials.xlsx`。
- 已有素材大块初版：`material_blocks.xlsx`。
- 已有重复关系表和部分 MVP 输出。

需要调整：

- 前置分类表要把 `fallback` 改成待确认，不再视为可靠分类。
- `assets_manifest.xlsx` 要补充轻量分类证据字段，如 `filename_hint`、`directory_path`、`file_title_if_readable`。
- 去重后要生成统一入口 `active_assets.xlsx`。
- 素材大块要从单归属表升级为多归属映射表 `asset_block_map.xlsx`。
- 去重逻辑要提前到建大块之前。
- `04_assets_by_course/` 要明确只做索引层，不复制大文件，不依赖系统软链接。
- 长视频处理先用 ASR + 人工时间码跑通，不急着做全自动切片。

## 6. 建议下一步

建议按下面顺序跑一个更真实的最小闭环：

```text
1. 重新生成前置分类表：exact / coarse / uncertain / fallback 分清楚
2. 去重提前，生成 active_assets.xlsx
3. 基于有效文件集生成 asset_block_map.xlsx，允许一个素材多归属
4. 重建 04_assets_by_course 为纯索引层
5. 选一个素材大块，例如 焊接基本操作 或 钨极氩弧焊
6. 从该大块选 3-5 个视频进入 02
7. 先跑 ASR + 人工时间码 + 关键帧 + 片段表
8. 用这些片段测试教材章节检索和引用
```

跑通之后，再考虑自动切片、数据库和向量库。

试点验收标准：

```text
1. 3-5 个视频都能完成 ASR、关键帧、片段表生成。
2. 每个视频至少形成 3-10 个候选片段。
3. 每个片段能通过 source_asset_id + start_time + end_time 回到原视频。
4. 片段表中的 transcript_text、keyframe_paths、evidence_text、boundary_reason 可供人工审核。
5. 人工审核通过率达到 60% 以上。
6. 能根据某一教材章节检索到至少 3 个可用素材。
7. xlsx 给人审核可读，jsonl 给程序读取无异常。
```
