from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from materials2textbook.adapters.video_segments import as_float, parse_time_to_ms, split_semicolon_list


REQUIRED_FIELDS = [
    "clip_id",
    "source_asset_id",
    "source_video",
    "original_path",
    "start_time",
    "end_time",
    "subject",
    "material_block",
    "material_block_code",
    "knowledge_point",
    "clip_summary",
    "recommended_chapter",
    "usefulness_score",
    "transcript_status",
    "evidence_text",
    "keyframe_paths",
    "review_status",
]


@dataclass
class SegmentValidationIssue:
    severity: str
    clip_id: str
    field: str
    message: str
    suggestion: str


@dataclass
class SegmentValidationReport:
    total_records: int
    issue_count: int
    high_issue_count: int
    medium_issue_count: int
    low_issue_count: int
    review_status_counts: dict[str, int] = field(default_factory=dict)
    transcript_status_counts: dict[str, int] = field(default_factory=dict)
    material_block_counts: dict[str, int] = field(default_factory=dict)
    issues: list[SegmentValidationIssue] = field(default_factory=list)


def validate_video_segments(records: list[dict[str, Any]]) -> SegmentValidationReport:
    issues: list[SegmentValidationIssue] = []
    clip_ids = [str(record.get("clip_id") or "") for record in records]
    duplicate_ids = {clip_id for clip_id, count in Counter(clip_ids).items() if clip_id and count > 1}

    for index, record in enumerate(records, start=1):
        clip_id = str(record.get("clip_id") or f"row_{index}")

        for field_name in REQUIRED_FIELDS:
            if _is_blank(record.get(field_name)):
                issues.append(
                    SegmentValidationIssue(
                        severity="high",
                        clip_id=clip_id,
                        field=field_name,
                        message=f"缺少必填字段 `{field_name}`。",
                        suggestion="请上游处理线补齐该字段后再进入正式教材生成。",
                    )
                )

        if clip_id in duplicate_ids:
            issues.append(
                SegmentValidationIssue(
                    severity="high",
                    clip_id=clip_id,
                    field="clip_id",
                    message="clip_id 重复。",
                    suggestion="为每个片段生成唯一 clip_id，避免证据引用混淆。",
                )
            )

        start_ms = parse_time_to_ms(str(record.get("start_time") or ""))
        end_ms = parse_time_to_ms(str(record.get("end_time") or ""))
        if start_ms is None:
            issues.append(
                SegmentValidationIssue("high", clip_id, "start_time", "start_time 格式无法解析。", "使用 HH:MM:SS 或 HH:MM:SS.mmm。")
            )
        if end_ms is None:
            issues.append(
                SegmentValidationIssue("high", clip_id, "end_time", "end_time 格式无法解析。", "使用 HH:MM:SS 或 HH:MM:SS.mmm。")
            )
        if start_ms is not None and end_ms is not None:
            if end_ms <= start_ms:
                issues.append(
                    SegmentValidationIssue("high", clip_id, "end_time", "end_time 必须晚于 start_time。", "重新确认片段边界。")
                )
            duration_seconds = (end_ms - start_ms) / 1000
            if duration_seconds < 10:
                issues.append(
                    SegmentValidationIssue("low", clip_id, "duration", "片段时长小于 10 秒。", "确认是否过短，是否需要与相邻片段合并。")
                )
            if duration_seconds > 300:
                issues.append(
                    SegmentValidationIssue("medium", clip_id, "duration", "片段时长超过 5 分钟。", "确认是否需要二次细分。")
                )

        transcript_status = str(record.get("transcript_status") or "").lower()
        evidence_text = str(record.get("evidence_text") or record.get("transcript_text") or "")
        if "pending" in transcript_status or "asr_pending" in evidence_text.lower():
            issues.append(
                SegmentValidationIssue(
                    "medium",
                    clip_id,
                    "transcript_status",
                    "片段转写仍待处理。",
                    "补充 ASR 或人工转写后再进入正式教材生成。",
                )
            )
        if len(evidence_text.strip()) < 20:
            issues.append(
                SegmentValidationIssue("medium", clip_id, "evidence_text", "证据文本过短。", "确认转写、OCR 或人工说明是否缺失。")
            )

        keyframes = split_semicolon_list(record.get("keyframe_paths"))
        if not keyframes:
            issues.append(
                SegmentValidationIssue("low", clip_id, "keyframe_paths", "缺少关键帧路径。", "如该片段用于操作演示，建议补充关键帧。")
            )

        usefulness = as_float(record.get("usefulness_score"), -1.0)
        if usefulness < 0:
            issues.append(
                SegmentValidationIssue("medium", clip_id, "usefulness_score", "usefulness_score 缺失或无法解析。", "补充 0-1 的教学价值评分。")
            )
        elif not 0 <= usefulness <= 1:
            issues.append(
                SegmentValidationIssue("medium", clip_id, "usefulness_score", "usefulness_score 不在 0-1 范围。", "将评分规范为 0-1。")
            )

        review_status = str(record.get("review_status") or "").lower()
        if "rejected" in review_status:
            issues.append(
                SegmentValidationIssue("medium", clip_id, "review_status", "片段已被标记为 rejected。", "正式生成时应过滤该片段。")
            )
        elif "pending" in review_status:
            issues.append(
                SegmentValidationIssue("low", clip_id, "review_status", "片段仍待人工复核。", "草稿可用，正式教材前需确认。")
            )

    severity_counts = Counter(issue.severity for issue in issues)
    return SegmentValidationReport(
        total_records=len(records),
        issue_count=len(issues),
        high_issue_count=severity_counts.get("high", 0),
        medium_issue_count=severity_counts.get("medium", 0),
        low_issue_count=severity_counts.get("low", 0),
        review_status_counts=dict(Counter(str(record.get("review_status") or "unknown") for record in records)),
        transcript_status_counts=dict(Counter(str(record.get("transcript_status") or "unknown") for record in records)),
        material_block_counts=dict(Counter(str(record.get("material_block") or "unknown") for record in records)),
        issues=issues,
    )


def render_segment_validation_markdown(report: SegmentValidationReport, title: str = "video_segments 校验报告") -> str:
    lines = [
        f"# {title}",
        "",
        "## 总览",
        "",
        f"- 记录数：{report.total_records}",
        f"- 问题总数：{report.issue_count}",
        f"- 高/中/低风险：{report.high_issue_count}/{report.medium_issue_count}/{report.low_issue_count}",
        "",
        "## review_status 分布",
        "",
    ]
    for status, count in sorted(report.review_status_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## transcript_status 分布", ""])
    for status, count in sorted(report.transcript_status_counts.items()):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## material_block 分布", ""])
    for block, count in sorted(report.material_block_counts.items()):
        lines.append(f"- {block}: {count}")

    lines.extend(["", "## 问题清单", ""])
    if not report.issues:
        lines.append("- 未发现结构化校验问题。")
    else:
        for issue in report.issues:
            lines.append(f"- [{issue.severity}] `{issue.clip_id}` `{issue.field}` {issue.message}")
            lines.append(f"  建议：{issue.suggestion}")

    return "\n".join(lines).rstrip() + "\n"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""
