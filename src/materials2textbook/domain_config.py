from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DomainConfig:
    domain_name: str = "welding technology"
    audience: str = "secondary and higher vocational students"
    textbook_type: str = "digital textbook"
    domain_terms: list[str] = field(default_factory=lambda: ["welding", "arc", "joint", "safety"])
    operation_terms: list[str] = field(default_factory=lambda: ["operate", "inspect", "adjust", "practice"])
    quality_dimensions: list[str] = field(default_factory=lambda: ["safety", "procedure", "quality judgment"])
    observation_examples: list[str] = field(
        default_factory=lambda: ["observe the procedure, tool position, visible state, and abnormal phenomena"]
    )
    common_misconceptions: list[str] = field(default_factory=list)
    chapter_order: list[str] = field(default_factory=list)

    def normalized(self) -> "DomainConfig":
        self.domain_name = self.domain_name.strip() or "general vocational topic"
        self.audience = self.audience.strip() or "vocational students"
        self.textbook_type = self.textbook_type.strip() or "digital textbook"
        self.domain_terms = _clean_list(self.domain_terms)
        self.operation_terms = _clean_list(self.operation_terms)
        self.quality_dimensions = _clean_list(self.quality_dimensions)
        self.observation_examples = _clean_list(self.observation_examples)
        self.common_misconceptions = _clean_list(self.common_misconceptions)
        self.chapter_order = _clean_list(self.chapter_order)
        if not self.domain_terms:
            self.domain_terms = [self.domain_name]
        if not self.operation_terms:
            self.operation_terms = ["inspect", "explain", "practice"]
        if not self.quality_dimensions:
            self.quality_dimensions = ["learning objective", "procedure", "quality judgment"]
        if not self.observation_examples:
            self.observation_examples = ["connect the text evidence with observable details in the material"]
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())

    def prompt_context(self) -> str:
        data = self.normalized()
        return "\n".join(
            [
                f"domain_name: {data.domain_name}",
                f"audience: {data.audience}",
                f"textbook_type: {data.textbook_type}",
                f"domain_terms: {', '.join(data.domain_terms)}",
                f"operation_terms: {', '.join(data.operation_terms)}",
                f"quality_dimensions: {', '.join(data.quality_dimensions)}",
                f"observation_examples: {' | '.join(data.observation_examples)}",
            ]
        )


def default_domain_config() -> DomainConfig:
    return DomainConfig(
        domain_name="welding technology",
        audience="secondary and higher vocational students",
        textbook_type="digital textbook",
        domain_terms=["welding", "arc", "molten pool", "tungsten electrode", "groove", "weld quality"],
        operation_terms=["operate", "inspect", "start arc", "feed wire", "finish arc", "protect"],
        quality_dimensions=["safety risk", "procedure", "process phenomenon", "quality judgment"],
        observation_examples=[
            "observe tool angle, operation sequence, workpiece state, protection effect, and visible quality"
        ],
        chapter_order=[
            "welding equipment and safety",
            "basic welding operations",
            "shielded metal arc welding",
            "gas tungsten arc welding",
            "carbon dioxide gas shielded welding",
            "welding quality inspection",
        ],
    )


def load_domain_config(path: Path | None = None, **overrides: str) -> DomainConfig:
    config = default_domain_config()
    if path:
        data = _read_config_path(Path(path))
        config = DomainConfig(**{**config.to_dict(), **data})
    for key, value in overrides.items():
        if value:
            setattr(config, key, value)
    return config.normalized()


def write_domain_config(path: Path, config: DomainConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    path.write_text(_to_simple_yaml(config.to_dict()), encoding="utf-8")


def domain_config_from_mapping(data: dict[str, Any]) -> DomainConfig:
    allowed = set(DomainConfig.__dataclass_fields__)
    clean = {key: value for key, value in data.items() if key in allowed}
    return DomainConfig(**clean).normalized()


def _read_config_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Domain config not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            result.setdefault(current_key, []).append(line[4:].strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if not value:
            result[key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[key] = [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
        else:
            result[key] = value.strip("\"'")
    return result


def _to_simple_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;；、\n]+", values)
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
