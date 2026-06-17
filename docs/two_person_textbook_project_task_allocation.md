# 双人协作任务分配：焊接数字教材素材库与教材生成

记录时间：2026-06-17  
基于文档：`docs/textbook_generation_optimization_plan.md`

## 1. 当前核心判断

这个项目现在不应该继续把所有资料直接丢给 LLM 生成整本教材。当前问题不是“LLM 不会写”，而是进入写作阶段的证据组织还不够像教材素材库，所以模型只能写出偏短、偏概括的样稿。

接下来两个人要分成两个稳定角色：

```text
角色 A：数据清洗与素材库负责人
角色 B：教材生成与写作负责人
```

两个人不是各做各的，而是围绕同一个交接物协作：

```text
chapter_evidence_pack.jsonl
chapter_readiness_report.xlsx
```

角色 A 的目标是把原始资料整理成可检索、可追溯、可用于教材写作的章节素材包。  
角色 B 的目标是基于章节素材包生成可读、可审、可追溯的教材章节。

## 2. 当前状态

截至 2026-06-17，主链路已经打通：

```text
原始/处理后素材
-> video_segments / ppt_assets / reference_text_assets 等 JSONL
-> LLM 资源分析
-> 教材草稿
-> 审核
-> 电子教材网页
-> zip 包
```

但质量还不够稳定：

- 整本模式一次塞入 40 条视频、500 条 PPT/文档记录，素材被摊到 5 个章里。
- token 预算后，540 条输入记录变成 249 个 evidence chunks。
- 当前只有 5 个章、6 个知识点，粒度太粗。
- ASR、PPT 抽取文本、PDF 文本仍有乱码、错词和噪声。
- 引用支撑率低，正文段落和证据块绑定不稳。
- writer prompt 原来偏“摘要生成”，不是“教材体例扩写”。

已新增或正在调整的工程方向：

- `scripts/build_chapter_evidence_pack.py`
- `scripts/run_chapter_digital_textbook.py`
- `src/materials2textbook/prompts/textbook_writer.py`
- LLM 空响应处理和终端进度打印

最新单章试探结果显示，“钨极氩弧焊”素材包可以生成，但质量分布不均：

```text
总选中素材：549
视频：40
PPT：417
参考文本：80
结构化资料：12

ready 知识点：基本原理、设备组成
partial_ready 知识点：焊前准备、非接触引弧、送丝操作、收弧操作
not_ready 知识点：打底焊、填充焊、盖面焊、常见缺陷与纠正
```

这说明：现在可以先写“钨极氩弧焊”的部分样章，但不能假装整章所有知识点都已准备充分。

## 3. 总体工作方式

建议接下来按章推进，不再优先整本生成：

```text
先固定教材目录
-> 角色 A 按章整理素材包
-> 角色 A 输出 readiness 报告
-> 角色 B 只对 ready / partial_ready 的知识点生成正文
-> 角色 B 输出章节样稿和审核报告
-> 两人一起根据审核结果回填素材问题
-> 单章达标后扩展到下一章
```

第一阶段试点章：

```text
第三章 钨极氩弧焊
```

不建议马上并行跑多章。先把单章标准跑通，再考虑并行。

## 4. 角色 A：数据清洗与素材库负责人

### 4.1 主要职责

角色 A 负责让素材“能被教材写作可靠使用”。

具体包括：

- 维护 `01_manifest_inventory/` 中的素材台账和分类结果。
- 维护 `asset_block_map.xlsx`，确保素材到大块、知识点的映射准确。
- 处理视频、PPT、PDF、Excel、音频等非教材正文文件。
- 生成和维护 `02_working_processing/json/` 下的主素材 JSONL。
- 生成每章的 `chapter_evidence_pack.jsonl`。
- 生成每章的 `chapter_readiness_report.xlsx`。
- 标记素材质量：主证据、辅助证据、参考证据、暂缓使用。
- 修正 ASR 专业词、明显错词、乱码文本。
- 筛掉不适合进入正文的素材。

### 4.2 角色 A 当前优先任务

优先级 1：把“钨极氩弧焊”的章节素材包整理稳定。

目标文件：

```text
work_materials/work_material1/05_final_deliverables/chapter_work/tig_welding/chapter_evidence_pack.jsonl
work_materials/work_material1/05_final_deliverables/chapter_work/tig_welding/chapter_evidence_pack.xlsx
work_materials/work_material1/05_final_deliverables/chapter_work/tig_welding/chapter_readiness_report.xlsx
```

优先级 2：补齐“钨极氩弧焊”的知识点粒度。

建议知识点：

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

优先级 3：处理 not_ready 知识点。

当前 not_ready：

```text
打底焊
填充焊
盖面焊
常见缺陷与纠正
```

角色 A 要判断：

- 是素材确实没有？
- 是素材有但没归到正确知识点？
- 是素材文本中有相关内容但关键词没命中？
- 是 PPT/PDF 里有图但没有 OCR/标签？
- 是视频时间码或 ASR 质量不够，导致不能作为主证据？

### 4.3 角色 A 交付标准

每个知识点至少要尽量达到：

```text
1-3 个视频片段
1-3 页 PPT 图文
1-2 段参考文本
关键帧或 PPT 图片
可用术语
常见错误或注意事项
可出题点
```

素材质量字段建议统一维护：

```text
evidence_role: primary / support / reference / hold
quality_gate_status: pass / weak / hold / reject
quality_gate_reason
review_status
source_type
knowledge_point
chapter
```

角色 A 交给角色 B 的最低交接条件：

```text
chapter_readiness_report.xlsx 已生成
至少 6 个知识点达到 ready 或 partial_ready
每个 ready 知识点至少有视频/PPT/文本中的两类证据
明显乱码和严重错词已标记或清理
不能写的知识点明确标为 not_ready
```

## 5. 角色 B：教材生成与写作负责人

### 5.1 主要职责

角色 B 负责把素材库里的内容写成教材，而不是继续清洗原始数据。

具体包括：

- 固定教材目录和章节结构。
- 维护教材写作 prompt。
- 维护单章生成脚本。
- 基于 `chapter_evidence_pack` 生成单章正文。
- 让正文按教材体例展开，而不是只做摘要。
- 检查引用、视频观察任务、图片使用和练习题。
- 输出可读的章节 Markdown、Word、电子教材网页。
- 根据审核报告向角色 A 反馈素材缺口。

### 5.2 角色 B 当前优先任务

优先级 1：把写作方式从整本生成改成按章生成。

目标脚本：

```text
scripts/run_chapter_digital_textbook.py
```

它应该完成：

```text
输入章节名
-> 读取该章素材包
-> 只使用该章视频、PPT、参考文本
-> 生成单章教材正文
-> 生成单章审核报告
-> 生成单章电子教材预览
```

优先级 2：改 writer prompt。

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

优先级 3：处理引用支撑。

正文必须保留证据引用：

```text
证据：C000001
证据：PPT_A000145_S001
证据：REF000001
```

如果证据不足，正文必须写清：

```text
本节证据不足，暂不展开完整操作步骤。
```

不能让 LLM 自己补教材知识。

### 5.3 角色 B 交付标准

“钨极氩弧焊”单章样稿的最低标准：

```text
至少 6 个知识点有正文
章节正文不少于 3000-5000 字
至少 5 个视频观察任务
至少 10 张关键帧或 PPT 图被引用或挂载
每个 ready 知识点至少 2 条证据
章节末尾列出 not_ready 知识点和原因
网页可以打开
视频/图片路径可以访问
```

达到展示版标准：

```text
citation_coverage_rate >= 0.8
high_issue_count 明显下降
paragraph_support_rate 不再为 0
正文段落能追溯到具体 evidence chunk
```

## 6. 两个人之间的交接协议

角色 A 不直接交“原始素材文件”给角色 B。  
角色 B 不直接从 `00_raw_client_materials/` 里挑文件写教材。

统一交接物是：

```text
chapter_evidence_pack.jsonl
chapter_evidence_pack.xlsx
chapter_readiness_report.xlsx
chapter_video_segments.jsonl
chapter_ppt_assets.jsonl
chapter_document_segments.jsonl
```

角色 B 如果发现正文写不厚，不能直接扩大 LLM 输入，而是回到 readiness 报告看：

- 是知识点太少？
- 是视频不足？
- 是 PPT 图文不足？
- 是参考文本不足？
- 是 ASR 乱码？
- 是素材归类错？
- 是 prompt 没要求扩写？

然后把问题反馈给角色 A。

## 7. 近期需要追加的修改方向

### 7.1 修复中文参数乱码

当前通过 `conda run` 嵌套调用脚本时，中文参数在终端中可能显示乱码，甚至传入子进程后变成乱码。

建议修改方向：

- 脚本内部提供稳定的默认章节代码，例如 `--chapter-code tig_welding`。
- 用代码映射中文名，不依赖命令行传中文。
- `run_chapter_digital_textbook.py` 默认使用 `tig_welding -> 钨极氩弧焊`。

建议字段：

```text
chapter_code
chapter_title
```

### 7.2 修复单章 zip 媒体校验失败

当前单章生成时，如果不复制媒体，打包 zip 的静态校验会提示媒体路径不在 `assets/` 下。

建议拆成两种输出：

```text
本机预览版：不复制大视频，只引用 02_working_processing
可离线分发版：复制必要小样本媒体到 digital_book/assets
```

角色 B 需要给脚本增加明确参数：

```text
--preview-only
--package-offline
```

预览版不应因为没有复制媒体而失败。离线版才要求媒体进入 `assets/`。

### 7.3 把章节素材包作为生成主入口

现在 `run_full_digital_textbook.py` 仍然偏整本入口。后续应该让单章入口优先读取：

```text
chapter_evidence_pack.jsonl
```

而不是重新从视频、PPT、文档 JSONL 里自己组织。

### 7.4 增加教材目录模板

角色 B 应维护一个固定目录模板，例如：

```text
docs/welding_textbook_outline_template.md
```

它记录：

```text
章
节
知识点
学习目标
建议素材类型
是否需要视频
是否需要 PPT 图
是否需要练习题
```

角色 A 按这个模板整理素材，角色 B 按这个模板生成教材。

### 7.5 建立素材缺口回填表

建议新增：

```text
chapter_evidence_gap_log.xlsx
```

字段：

```text
chapter
knowledge_point
gap_type
gap_description
found_by
assigned_to
status
resolution
updated_at
```

这样角色 B 发现写不出来的地方，能明确交回给角色 A。

## 8. 建议的下一周执行顺序

### 第一步：角色 A

整理“钨极氩弧焊”素材包。

输出：

```text
chapter_evidence_pack.xlsx
chapter_readiness_report.xlsx
```

重点处理：

```text
打底焊
填充焊
盖面焊
常见缺陷与纠正
```

### 第二步：角色 B

修复单章生成入口。

重点修：

```text
中文参数乱码
preview-only 输出
writer prompt 教材扩写
章节报告输出
```

### 第三步：角色 B

只生成“钨极氩弧焊”单章样稿。

不要整本跑。

### 第四步：两人一起审查

检查：

```text
字数是否够
每节是否有证据
视频任务是否能看
图片是否能打开
引用是否能追溯
哪些知识点 still not_ready
```

### 第五步：角色 A 回填素材

根据角色 B 的缺口表继续补素材或清理素材。

## 9. 两人分工一览

| 工作项 | 角色 A：数据清洗与素材库 | 角色 B：教材生成与写作 |
|---|---|---|
| 原始资料扫描 | 主责 | 不负责 |
| 素材分类 | 主责 | 提出缺口 |
| ASR / OCR / PPT 抽取 | 主责 | 使用结果 |
| 证据质量评分 | 主责 | 复核规则是否影响写作 |
| chapter_evidence_pack | 主责 | 使用并反馈 |
| chapter_readiness_report | 主责 | 作为生成前检查 |
| 教材目录模板 | 参与 | 主责 |
| writer prompt | 提供素材约束 | 主责 |
| 单章生成脚本 | 协助测试 | 主责 |
| 正文质量审核 | 参与 | 主责 |
| 缺口回填 | 主责 | 提出缺口 |
| 整本合成 | 提供稳定素材包 | 主责 |

## 10. 当前结论

下一步最重要的不是继续整本生成，而是建立稳定协作接口：

```text
角色 A 把素材整理成章节素材包
角色 B 基于章节素材包写单章教材
角色 B 把写不出来的地方反馈成缺口表
角色 A 再补素材或修素材
```

先把“钨极氩弧焊”这一章做厚、做稳。  
这章跑通后，再把流程复制到其他章节，最后再合成整本焊接数字教材。
