# 架构护栏

本文档规定 Agent 修改教材生成 pipeline 时必须遵守的长期边界。它描述
不可破坏的约束和职责边界，不规定某个具体类名、prompt 或模型实现。

## 1. 大纲边界：先生成、再冻结

- 使用原始大纲工作流生成 source BookPlan。
- 生成后立即保存深度快照、outline signature 和 semantic fingerprint。
- 整个运行期间将该快照视为不可变参考数据。
- 结束时必须执行 deep equality 校验，不能只比较部分 ID 或 hash。
- normalize、display metadata、语义拆分、写作、repair、exporter 都不得
  修改 BookPlan。
- presentation 字段必须放在以 outline node 关联的 display/rendering overlay
  中，不能成为知识归属字段。

如果一个改动会改变章节、任务、节的顺序、标题、知识归属或目录元数据，
它就是大纲改动，不属于语义优化范围。

## 2. Overlay 边界：occurrence 不能变成目录节点

知识映射可以是精确映射、别名映射、拆分映射或未决映射。一个大纲节点
可以拥有多个 occurrence，但 occurrence 只是语义 overlay 记录，不得创建
新章节、按标题消费章节，或改变可见目录。

每个可渲染的 source outline node 在每种数字教材渲染中都必须有且只有一个
结构投影。多个 occurrence 必须通过稳定 occurrence ID 或稳定复合键绑定
到该投影，禁止依赖“按标题找到第一个”的匹配方式。

## 3. 知识与轨迹边界

- canonical knowledge identity 必须与教材结构和教学情境分离。
- 知识合并必须保守：漏合并的代价低于错误合并；不确定关系保持独立并
  进入审计。
- 按书中顺序跟踪 occurrence，并保存明确的 ordinal/position。
- 知识点可以多次出现；判断重点是是否承担新的教学职责，而不是是否只
  允许一个主讲位置。
- 不得为了改善指标强行制造 APPLY 或 RECALL。只有任务真实使用前文知识，
  或 recall policy 真实触发时，才应采用对应职责。

## 4. 语义规划边界

模型可以提出语义事实，确定性代码负责政策和状态。

LLM 可以提出 identity relation、prerequisite 含义、role 证据、贡献事实
和 semantic delta，但必须同时提供 confidence、rationale 和 evidence/source
引用。低置信度或未决结果必须保持为显式审核/拒绝状态。

确定性代码负责顺序、ID 白名单、prerequisite inclusion、availability
transition、role derivation、recall distance policy、issue triggering、
报告顺序和 publication decision。任何自由生成的模型调用都不能静默替代
这些决策。

同一个 occurrence 不得维护两套彼此独立的语义结论，例如 role 一套、
contribution facet 另一套。effective delta 应只编译一次，后续 occurrence
和 WritingBrief 必须消费同一编译结果。

## 5. 证据边界

证据访问按 occurrence 限定。允许的证据来源包括 canonical knowledge 的
source evidence、映射/拆分继承的 evidence，以及当前任务/情境允许的
evidence。整章或整本书不是默认授权范围。

每个 claim 必须声明所需的支持类型：

- SOURCE_FACT 由当前授权 EvidenceChunk 支持；
- TRAJECTORY_FACT 由 verified prior occurrence 和 availability state 支持；
- STRUCTURAL_FACT 由冻结 BookPlan 或任务上下文支持；
- MIXED 由上述多个来源联合支持。

证据检索和绑定必须可审计，至少记录旧 evidence ID、候选 ID、接受 ID、
检索理由和置信度。没有支持时，应收缩语义计划或停止进入审核，禁止扩大
证据范围或用模型常识补齐。

## 6. Writer 边界

Writer 接收 role-specific WritingBrief，但不得重新判断 role、canonical
identity、prerequisite 或 SemanticDelta。brief 应说明已有上下文、必需
facet、允许贡献、禁止重讲内容和授权证据。

职责约束是教学行为约束，不是写作风格约束：

- INTRO 建立方向和直觉，不提前替代后续完整讲授；
- TEACH 完整覆盖当前教学目标；
- RECALL 只恢复当前任务真正需要的最小前文信息；
- APPLY 默认已有知识，并明确当前动作/任务如何使用它；
- EXTEND 明确前文基础，只讲新的条件、变体、限制或推论。

Writer 不得编造事实，不得把 APPLY/EXTEND 静默写成 TEACH，也不得重新讲
brief 禁止的 facet。正文生成后必须立即执行 brief conformance 和证据检查。

## 7. Repair 与 Materialization 边界

Repair 是对 immutable upstream decision 的下游处理，不得修改 BookPlan、
canonical mapping、role、prerequisite graph、SemanticDelta 或 WritingBrief。

- 只有当 conformance checker 精确定位了 forbidden reteach span，并且删除
  后完整复检达到 MATCH 时，确定性删除才可自动接受。
- 生成式 repair 只能针对单个缺口生成最小、证据支持的 patch，并使用固定
  insertion strategy，禁止重写整个 occurrence。
- Recall capsule 必须短小、来自前文已验证 occurrence，不能引入新 facet
  或 extension。
- 任何 post-check 失败、证据失败、anchor mismatch 或 PARTIAL 结果都必须
  回滚本次 occurrence，并记录原因。
- 无证据的修复必须显式拒绝或进入人工审核，不得落回未记录的旧段落去重
  路径。

Markdown 和 DigitalBook 对同一 occurrence 必须应用同一个已接受的决定，
并保持语义一致。

## 8. 验证与发布边界

验证至少分为以下层次：

1. 大纲不可变校验；
2. 语义轨迹和 prerequisite availability 校验；
3. WritingBrief conformance 校验；
4. 正文 claim 的授权证据校验；
5. Markdown/DigitalBook occurrence 与 anchor 对齐校验；
6. publication-quality 校验；
7. final publication gate。

这些层次不能互相替代。publication-quality PASS 不证明语义证据通过；
词法相似度只能用于候选召回，不能直接作为最终蕴含结论。PARTIALLY_SUPPORTED
和 UNSUPPORTED 都必须出现在报告中，UNSUPPORTED 仍是 blocker。

最终门禁必须对以下情况 fail-closed：大纲或语义对象变化、静默 fallback、
未解决的高严重性问题、接受了 PARTIAL 的 repair、缺少审计记录，或双端
发生语义/锚点不一致。

## 9. 报告与可复现边界

每次运行必须保留足够信息来复现决策链：

- 冻结 source plan 及其签名；
- knowledge mapping 和 occurrence trajectory；
- effective SemanticDelta 和 evidence decision；
- WritingBrief 和 rendered anchor；
- conformance、evidence、repair、rollback、materialization 记录；
- publication-quality 和 publication-gate 结果。

报告必须区分：未改变的计划、证据收缩后的计划、被放弃的 occurrence 目标、
修复后的正文、证据拒绝、人工审核，以及真正的渲染或内容缺陷。

Baseline 与 semantic A/B 测试必须消费同一份冻结 source BookPlan。测试夹具
和构造样例不得进入生产产物。

## 10. Agent 修改流程

修改前必须：

1. 明确改动属于哪个不变量和哪一层；
2. 检查当前数据流和已有审计产物；
3. 证明改动不会修改冻结大纲或扩大证据范围；
4. 为受影响的不变量添加聚焦回归测试；
5. 运行相关测试，并报告剩余不确定性。

禁止通过放宽门禁、强行改变 role、扩大素材范围、隐藏 fallback、修改大纲
或加入学科专用 heuristic 来解决局部失败。证据或语义确实不足时，应保留
显式、可审核的失败状态。

