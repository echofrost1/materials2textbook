from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from materials2textbook.domain_config import (
    DomainConfig,
    default_domain_config,
    domain_config_from_mapping,
    parse_json_object,
)
from materials2textbook.io_utils import read_jsonl
from materials2textbook.llm.provider import LLMProvider
from materials2textbook.prompts.domain_config import build_domain_config_messages


class DomainConfigAgent:
    def __init__(self, llm_provider: LLMProvider | None = None, use_llm: bool = False) -> None:
        self.llm_provider = llm_provider
        self.use_llm = use_llm
        self.last_mode = "rule"
        self.last_warning = ""

    def run(self, *, title: str, material_root: Path, sample_size: int = 120) -> DomainConfig:
        samples = collect_material_samples(material_root, sample_size=sample_size)
        if self.use_llm and self.llm_provider is not None and samples:
            try:
                raw = self.llm_provider.generate(build_domain_config_messages(title=title, material_samples=samples))
                config = domain_config_from_mapping(parse_json_object(raw))
                self.last_mode = "llm"
                self.last_warning = ""
                return config
            except Exception as exc:  # pragma: no cover - provider behavior varies.
                self.last_mode = "rule_fallback"
                self.last_warning = f"LLM domain config failed: {exc}"
        else:
            self.last_mode = "rule"
            self.last_warning = "" if samples else "No material samples found; using default domain config."
        return infer_domain_config_from_samples(title=title, samples=samples)

    def render_review(self, config: DomainConfig, material_root: Path) -> str:
        lines = [
            f"# Domain Config Review",
            "",
            f"- mode: {self.last_mode}",
            f"- warning: {self.last_warning or 'none'}",
            f"- material_root: {material_root}",
            f"- domain_name: {config.domain_name}",
            f"- audience: {config.audience}",
            f"- textbook_type: {config.textbook_type}",
            f"- domain_terms: {', '.join(config.domain_terms)}",
            f"- operation_terms: {', '.join(config.operation_terms)}",
            f"- chapter_order: {', '.join(config.chapter_order)}",
        ]
        return "\n".join(lines) + "\n"


def collect_material_samples(material_root: Path, *, sample_size: int = 120) -> list[dict[str, Any]]:
    material_root = Path(material_root)
    json_dir = material_root / "02_working_processing" / "json"
    paths = [
        json_dir / "video_segments.jsonl",
        json_dir / "ppt_assets.jsonl",
        json_dir / "reference_text_assets.jsonl",
        json_dir / "audio_segments.jsonl",
        json_dir / "structured_assets.jsonl",
    ]
    samples: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            rows = read_jsonl(path)
        except Exception:
            continue
        for row in rows:
            samples.append(_sample_from_row(row, path.name))
            if len(samples) >= sample_size:
                return samples
    return samples


def infer_domain_config_from_samples(*, title: str, samples: list[dict[str, Any]]) -> DomainConfig:
    if not samples:
        config = default_domain_config()
        config.domain_name = title or config.domain_name
        return config.normalized()
    term_counts: Counter[str] = Counter()
    chapter_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    for sample in samples:
        for key in ("title", "knowledge_point", "summary", "subject", "material_block"):
            for token in _tokens(str(sample.get(key, ""))):
                term_counts[token] += 1
                if _looks_operational(token):
                    operation_counts[token] += 1
        chapter = str(sample.get("recommended_chapter") or sample.get("chapter") or sample.get("material_block") or "").strip()
        if chapter:
            chapter_counts[chapter] += 1
    domain_name = title or (chapter_counts.most_common(1)[0][0] if chapter_counts else "vocational topic")
    return DomainConfig(
        domain_name=domain_name,
        audience="secondary and higher vocational students",
        textbook_type="digital textbook",
        domain_terms=[term for term, _ in term_counts.most_common(16)] or [domain_name],
        operation_terms=[term for term, _ in operation_counts.most_common(8)] or ["inspect", "explain", "practice"],
        quality_dimensions=["safety", "procedure", "quality judgment", "tool use"],
        observation_examples=["observe the procedure, tool position, material state, and abnormal phenomena"],
        chapter_order=[chapter for chapter, _ in chapter_counts.most_common(12)],
    ).normalized()


def _sample_from_row(row: dict[str, Any], source_name: str) -> dict[str, Any]:
    keys = [
        "chunk_id",
        "clip_id",
        "ppt_asset_id",
        "reference_text_id",
        "title",
        "knowledge_point",
        "summary",
        "evidence_text",
        "content",
        "subject",
        "material_block",
        "recommended_chapter",
        "chapter",
        "source_type",
        "review_status",
    ]
    result = {key: row.get(key, "") for key in keys if row.get(key, "")}
    result["source_file"] = source_name
    if "evidence_text" in result:
        result["evidence_text"] = str(result["evidence_text"])[:700]
    if "content" in result:
        result["content"] = str(result["content"])[:700]
    return result


def _tokens(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if " " in text:
        return [item.strip(" ,.;:()[]") for item in text.split() if len(item.strip(" ,.;:()[]")) >= 3]
    parts = []
    for item in text.replace("，", " ").replace("；", " ").replace("、", " ").split():
        if len(item) >= 2:
            parts.append(item[:24])
    if not parts and len(text) >= 2:
        parts.append(text[:24])
    return parts


def _looks_operational(token: str) -> bool:
    lowered = token.lower()
    hints = ["操作", "检查", "安装", "调整", "测量", "诊断", "维修", "operate", "inspect", "install", "adjust", "measure"]
    return any(hint in lowered or hint in token for hint in hints)
