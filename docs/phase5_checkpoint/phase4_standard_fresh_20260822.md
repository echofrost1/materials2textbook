# Phase 5 前历史 benchmark 说明

## 产物

`D:\Code\vibe coding\materials2textbook\_archive\phase4_standard_fresh_20260822`

该目录不纳入 Git。本文件只保存可追溯说明，不复制大型教材、模型缓存或生成文件。

## 用途

这是 2026-08-22 的标准 production fresh artifact，用于验证 Phase 4A/4B 的语义执行、证据审计、下游教学闭合和 publication quality。它是进入 Phase 5 前的内容完整性诊断基线。

## 运行信息

- `artifact_manifest.json` 记录 `semantic_book_mode=true`、`semantic_evaluation_input` 为空、`planning_mode=llm` 和 verified-sequential runtime。
- 产物没有保存完整 CLI 命令，因此不能仅凭 artifact 证明具体调用脚本或 `skip-resource-analyst-llm` 是否启用；这两项在当前基线中标记为 **未记录**。
- 该 artifact 生成时早于当前 HEAD 的 Root A prerequisite planning 修复和 Root B fallback/conformance/grant 修复；没有新的 full-book 运行证明修复后的数量。

## 主要数字（历史观察值）

- 4 章、14 个 section、25 planned occurrences、12 rendered、13 blocked；7/14 任务没有 implementation 正文。
- 911 个 EvidenceChunk 中，BookPlan 唯一 primary 绑定 75 个，WritingBrief/writer 实际引用 41 个唯一 chunk；reference ownership 为 0。
- publication blocker 36（19 unsupported + 17 partial rendered claims）；这些数字不应当被当作当前 HEAD 的结果。

## 使用边界

该 artifact 只用于回归比较、内容完整性诊断和基线追踪。Phase 5 实现或新的 full-book 运行必须重新记录代码提交、正式入口、模型/provider、skip-resource-analyst-llm、输入 fingerprint 和输出目录，不能静默复用上述统计。
