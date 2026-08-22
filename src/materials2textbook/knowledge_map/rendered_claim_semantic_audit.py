"""Claim-level semantic evidence audit for already-rendered textbook output.

The audit is read-only with respect to textbook content.  It layers a
conservative deterministic check on top of the existing rendered evidence
verifier.  Only claims that cannot be decided deterministically (plus any
calibrated routing categories) are sent to an injected entailment judge.  The
judge receives the claim and the current occurrence's authorized evidence
only; it cannot change the text or evidence ownership.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from hashlib import sha256
from typing import Any, Protocol, Sequence

from materials2textbook.io_utils import write_json, write_text
from materials2textbook.knowledge_map.rendered_conformance import (
    RenderedOccurrence,
    extract_rendered_occurrences,
)
from materials2textbook.knowledge_map.rendered_evidence_verification import (
    _chinese_bigrams,
    _claim_type,
    _content_terms,
)
from materials2textbook.schemas import EvidenceChunk


class ClaimStatus:
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class DeterministicStatus:
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNCERTAIN = "UNCERTAIN"


class ClaimResolution:
    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC = "SEMANTIC"
    SEMANTIC_UNRESOLVED = "SEMANTIC_UNRESOLVED"
    MODEL_ERROR = "MODEL_ERROR"


class CalibrationResolution:
    SEMANTIC = "SEMANTIC"
    MODEL_ERROR = "MODEL_ERROR"


class CalibrationStratum:
    MODALITY_SCOPE = "modality_scope"
    CAUSAL = "causal"
    PROCEDURAL = "procedural"
    QUANTITATIVE_CONDITIONAL = "quantitative_conditional"
    ORDINARY_DESCRIPTIVE = "ordinary_descriptive"


# Phase 4B-2 blind calibration of the current real artifact found a material
# lexical false-positive rate in every source-fact stratum (causal,
# modality/scope, ordinary descriptive, procedural, and
# quantitative/conditional).  Keep this list limited to claim strata; it does
# not route headings, navigation, assessment templates, or other non-factual
# text to the model.  The production orchestrator passes this explicit,
# calibration-backed policy rather than silently treating lexical overlap as
# semantic support.
CALIBRATED_SEMANTIC_ROUTING_CATEGORIES: tuple[str, ...] = (
    CalibrationStratum.CAUSAL,
    CalibrationStratum.MODALITY_SCOPE,
    CalibrationStratum.ORDINARY_DESCRIPTIVE,
    CalibrationStratum.PROCEDURAL,
    CalibrationStratum.QUANTITATIVE_CONDITIONAL,
)


_CLAIM_SPLIT = re.compile(r"(?<=[。！？.!?；;])\s*|\n+")
_EVIDENCE_ID = re.compile(r"\bEvidence\s*:\s*([A-Za-z0-9_.:-]+)\b", re.IGNORECASE)
_INTERNAL_MARKER = re.compile(r"<!--.*?-->", re.DOTALL)
_TEMPLATE_ONLY = re.compile(
    r"^(?:学习方向|本节将|本节重点|本任务将|本任务重点|本章将|课后|小结|思考与练习|学习目标)"
    r"[^。！？.!?]*[。！？.!?]?$",
    re.IGNORECASE,
)
_NON_FACTUAL = re.compile(
    r"^(?:下面|接下来|综上所述|如前所述|本节|本任务|本章|例如|注意)\s*(?:将|重点|可以|需要)?$",
    re.IGNORECASE,
)

# These pairs are intentionally domain-independent.  They detect a claim
# strengthening signal; they do not decide entailment by themselves.
_WEAK_STRONG_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("可能", "有时", "通常", "往往", "一般", "有助于", "可以", "可", "may", "might", "often", "usually", "can", "helps"),
     ("必须", "一定", "总是", "完全", "保证", "避免", "导致", "所有", "must", "always", "completely", "guarantee", "eliminate", "causes", "all"),
     "modality_or_scope_strengthening"),
    (("减少", "降低", "改善", "reduce", "lower", "improve"),
     ("消除", "完全避免", "根除", "eliminate", "prevent", "guarantee"),
     "effect_strengthening"),
    (("有关", "相关", "关联", "associated", "related"),
     ("导致", "造成", "原因是", "因果", "causes", "leads to"),
     "causal_strengthening"),
)
_NEGATION = re.compile(r"(?:不|未|无|不能|不会|not|no|never|cannot)", re.IGNORECASE)


class EntailmentJudge(Protocol):
    """Restricted semantic judge.  It must not fetch evidence or rewrite text."""

    @property
    def call_count(self) -> int: ...

    def judge(
        self,
        *,
        claim: str,
        evidence: list[dict[str, str]],
        context: dict[str, str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ClaimEvidenceSpan:
    evidence_id: str
    span: str


@dataclass(frozen=True)
class SemanticEntailmentProposal:
    status: str
    supporting_evidence_ids: tuple[str, ...]
    rationale: str
    confidence: float
    unsupported_part: str = ""
    model: str = ""
    prompt_version: str = ""
    error: str = ""


@dataclass(frozen=True)
class RenderedClaimAuditRecord:
    claim_id: str
    occurrence_id: str
    claim_text: str
    claim_type: str
    authorized_evidence_ids: tuple[str, ...]
    evidence_spans: tuple[ClaimEvidenceSpan, ...]
    supporting_span: str
    deterministic_result: str
    semantic_result: str
    final_status: str
    resolution: str
    unsupported_part: str
    rationale: str
    confidence: float
    model: str = ""
    prompt_version: str = ""
    source_span: str = ""
    evidence_provenance_valid: bool = True
    strengthening_signals: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # `rationale` is the internal name; expose the audit contract's
        # neutral `explanation` field as well for downstream reviewers.
        data["explanation"] = self.rationale
        return data


@dataclass(frozen=True)
class LexicalCalibrationRecord:
    """One blind semantic adjudication of a lexical-SUPPORTED claim."""

    claim_id: str
    occurrence_id: str
    claim_text: str
    stratum: str
    authorized_evidence_ids: tuple[str, ...]
    evidence_spans: tuple[ClaimEvidenceSpan, ...]
    lexical_result: str
    semantic_result: str
    resolution: str
    supporting_evidence_ids: tuple[str, ...]
    supporting_span: str
    unsupported_part: str
    rationale: str
    confidence: float
    false_positive: bool
    false_positive_category: str = ""
    strengthening_signals: tuple[str, ...] = ()
    model: str = ""
    prompt_version: str = ""
    evidence_provenance_valid: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["explanation"] = self.rationale
        return data


@dataclass
class LexicalCalibrationReport:
    """Auditable, stratified blind calibration of lexical support."""

    artifact_root: str
    seed: int
    requested_sample_size: int
    eligible_lexical_supported: int
    records: list[LexicalCalibrationRecord] = field(default_factory=list)
    qwen_call_count: int = 0
    qwen_model: str = ""
    prompt_version: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def sample_size(self) -> int:
        return len(self.records)

    @property
    def semantic_supported(self) -> int:
        return sum(item.semantic_result == ClaimStatus.SUPPORTED for item in self.records)

    @property
    def partially_supported(self) -> int:
        return sum(item.semantic_result == ClaimStatus.PARTIALLY_SUPPORTED for item in self.records)

    @property
    def unsupported(self) -> int:
        return sum(item.semantic_result == ClaimStatus.UNSUPPORTED for item in self.records)

    @property
    def model_errors(self) -> int:
        return sum(item.resolution == CalibrationResolution.MODEL_ERROR for item in self.records)

    @property
    def false_positive_count(self) -> int:
        return sum(item.false_positive for item in self.records)

    @property
    def false_positive_categories(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.records:
            if item.false_positive:
                result[item.false_positive_category or "uncategorized"] = result.get(item.false_positive_category or "uncategorized", 0) + 1
        return result

    @property
    def systematic_categories(self) -> list[str]:
        """Categories with enough evidence to justify routing expansion."""
        sample_counts: dict[str, int] = {}
        false_counts = self.false_positive_categories
        for item in self.records:
            sample_counts[item.stratum] = sample_counts.get(item.stratum, 0) + 1
        categories: list[str] = []
        for category, count in false_counts.items():
            denominator = sample_counts.get(category, 0)
            if count >= 2 or (denominator >= 3 and count / denominator >= 0.4):
                categories.append(category)
        return sorted(categories)

    @property
    def false_positive_strengthening_signals(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in self.records:
            if not item.false_positive:
                continue
            for signal in item.strengthening_signals:
                result[signal] = result.get(signal, 0) + 1
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_root": self.artifact_root,
            "seed": self.seed,
            "requested_sample_size": self.requested_sample_size,
            "summary": {
                "eligible_lexical_supported": self.eligible_lexical_supported,
                "sample_size": self.sample_size,
                "qwen_entailment_call_count": self.qwen_call_count,
                "semantically_supported": self.semantic_supported,
                "partially_supported": self.partially_supported,
                "truly_unsupported": self.unsupported,
                "model_errors": self.model_errors,
                "lexical_false_positive_count": self.false_positive_count,
                "lexical_false_positive_rate": (self.false_positive_count / self.sample_size if self.sample_size else 0.0),
                "false_positive_categories": self.false_positive_categories,
                "false_positive_strengthening_signals": self.false_positive_strengthening_signals,
                "systematic_routing_categories": self.systematic_categories,
            },
            "model": self.qwen_model,
            "prompt_version": self.prompt_version,
            "notes": list(self.notes),
            "records": [item.to_dict() for item in self.records],
        }


@dataclass
class RenderedClaimSemanticEvidenceAudit:
    artifact_root: str
    records: list[RenderedClaimAuditRecord] = field(default_factory=list)
    qwen_call_count: int = 0
    qwen_model: str = ""
    prompt_version: str = ""
    source_claim_count: int = 0
    semantic_routing_categories: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def deterministic_supported(self) -> int:
        return sum(item.deterministic_result == DeterministicStatus.SUPPORTED for item in self.records)

    @property
    def deterministic_unsupported(self) -> int:
        return sum(item.deterministic_result == DeterministicStatus.UNSUPPORTED for item in self.records)

    @property
    def semantic_candidates(self) -> int:
        return sum(item.resolution in {ClaimResolution.SEMANTIC, ClaimResolution.SEMANTIC_UNRESOLVED, ClaimResolution.MODEL_ERROR} for item in self.records)

    @property
    def semantic_supported(self) -> int:
        return sum(item.resolution == ClaimResolution.SEMANTIC and item.final_status == ClaimStatus.SUPPORTED for item in self.records)

    @property
    def partially_supported(self) -> int:
        return sum(item.final_status == ClaimStatus.PARTIALLY_SUPPORTED for item in self.records)

    @property
    def truly_unsupported(self) -> int:
        return sum(item.final_status == ClaimStatus.UNSUPPORTED and item.resolution != ClaimResolution.SEMANTIC_UNRESOLVED for item in self.records)

    @property
    def unresolved_semantic(self) -> int:
        return sum(item.resolution in {ClaimResolution.SEMANTIC_UNRESOLVED, ClaimResolution.MODEL_ERROR} for item in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_root": self.artifact_root,
            "summary": {
                "total_source_fact_claims": self.source_claim_count,
                # Keep the public report vocabulary explicit.  The internal
                # deterministic layer remains named separately because it
                # also records deterministic UNSUPPORTED/UNCERTAIN results.
                "lexically_supported": self.deterministic_supported,
                "deterministic_supported": self.deterministic_supported,
                "deterministic_unsupported": self.deterministic_unsupported,
                "semantic_entailment_candidates": self.semantic_candidates,
                "qwen_entailment_call_count": self.qwen_call_count,
                "semantically_supported": self.semantic_supported,
                "partially_supported": self.partially_supported,
                "truly_unsupported": self.truly_unsupported,
                "semantic_unresolved": self.unresolved_semantic,
                "semantic_routing_categories": list(self.semantic_routing_categories),
            },
            "model": self.qwen_model,
            "prompt_version": self.prompt_version,
            "notes": list(self.notes),
            "claims": [item.to_dict() for item in self.records],
        }


PROMPT_VERSION = "rendered-claim-entailment-v1"
ENTAILMENT_PROMPT = """You are an evidence entailment auditor. Judge only whether the claim is supported by the authorized evidence below.
Do not use outside knowledge. Do not infer new evidence. Do not rewrite the claim.
Preserve modality, quantity, scope, causality, conditions, and negation.
Return JSON only:
{{"status":"SUPPORTED|PARTIALLY_SUPPORTED|UNSUPPORTED","supporting_evidence_ids":["..."],"rationale":"...","confidence":0.0,"unsupported_part":"..."}}

Claim:
{claim}

Fixed current-occurrence context:
{context}

Authorized evidence spans (the only evidence you may use):
{evidence}
"""


class OpenAICompatibleEntailmentJudge:
    """Adapter around the existing provider; no evidence retrieval is possible here."""

    def __init__(self, provider: Any, *, model: str = "") -> None:
        self.provider = provider
        self.model = model or str(getattr(getattr(provider, "config", None), "model", ""))
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def judge(self, *, claim: str, evidence: list[dict[str, str]], context: dict[str, str]) -> dict[str, Any]:
        self._call_count += 1
        prompt = ENTAILMENT_PROMPT.format(
            claim=claim,
            context=json.dumps(context, ensure_ascii=False, sort_keys=True),
            evidence=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        )
        raw = self.provider.generate([
            {"role": "system", "content": "Return one JSON object and nothing else."},
            {"role": "user", "content": prompt},
        ])
        payload = _parse_json_object(raw)
        payload["model"] = self.model
        payload["prompt_version"] = PROMPT_VERSION
        return payload


def audit_rendered_claims(
    *,
    markdown: str,
    briefs: list[dict[str, Any]],
    evidence_by_id: dict[str, EvidenceChunk],
    artifact_root: str = "",
    judge: EntailmentJudge | None = None,
    source_claim_count: int | None = None,
    semantic_routing_categories: Sequence[str] = (),
) -> RenderedClaimSemanticEvidenceAudit:
    """Audit Markdown occurrence bodies only; DigitalBook is not duplicated here."""
    brief_by_id = {str(item.get("occurrence_id")): item for item in briefs if item.get("occurrence_id")}
    records: list[RenderedClaimAuditRecord] = []
    for rendered in extract_rendered_occurrences(markdown):
        brief = brief_by_id.get(rendered.occurrence_id)
        if brief is None:
            continue
        allowed_ids = _allowed_evidence_ids(brief)
        allowed_chunks = [evidence_by_id[item] for item in allowed_ids if item in evidence_by_id]
        for index, claim_text in enumerate(_extract_claims(rendered.markdown), start=1):
            if not _is_auditable_claim(claim_text):
                continue
            record = _audit_claim(
                rendered=rendered,
                brief=brief,
                claim_text=claim_text,
                claim_index=index,
                allowed_ids=allowed_ids,
                allowed_chunks=allowed_chunks,
                judge=judge,
                semantic_routing_categories=semantic_routing_categories,
            )
            records.append(record)
    report = RenderedClaimSemanticEvidenceAudit(
        artifact_root=artifact_root,
        records=records,
        qwen_call_count=int(getattr(judge, "call_count", 0) if judge else 0),
        qwen_model=str(getattr(judge, "model", "") if judge else ""),
        prompt_version=PROMPT_VERSION if judge else "",
        source_claim_count=source_claim_count if source_claim_count is not None else len(records),
        semantic_routing_categories=tuple(sorted(set(semantic_routing_categories))),
    )
    if judge is None and any(item.resolution == ClaimResolution.SEMANTIC_UNRESOLVED for item in records):
        report.notes.append("Semantic candidates without an injected judge are fail-closed and are not counted as confirmed unsupported claims.")
    return report


def _audit_claim(
    *,
    rendered: RenderedOccurrence,
    brief: dict[str, Any],
    claim_text: str,
    claim_index: int,
    allowed_ids: tuple[str, ...],
    allowed_chunks: list[EvidenceChunk],
    judge: EntailmentJudge | None,
    semantic_routing_categories: Sequence[str] = (),
) -> RenderedClaimAuditRecord:
    claim_id = f"{rendered.occurrence_id}:claim:{claim_index}"
    cited_ids = tuple(dict.fromkeys(_EVIDENCE_ID.findall(claim_text)))
    clean_claim = _EVIDENCE_ID.sub("", claim_text).strip()
    claim_type = _claim_type(clean_claim)
    strengthening = tuple(_strengthening_signals(clean_claim, allowed_chunks))
    deterministic, deterministic_ids, deterministic_span, deterministic_reason = _deterministic_result(
        clean_claim,
        cited_ids=cited_ids,
        allowed_ids=allowed_ids,
        chunks=allowed_chunks,
        strengthening_signals=strengthening,
    )
    force_semantic = (
        deterministic == DeterministicStatus.SUPPORTED
        and _claim_calibration_category(clean_claim) in set(semantic_routing_categories)
    )
    evidence_spans = tuple(_evidence_spans(allowed_chunks))
    if deterministic in {DeterministicStatus.SUPPORTED, DeterministicStatus.UNSUPPORTED} and not force_semantic:
        return RenderedClaimAuditRecord(
            claim_id=claim_id,
            occurrence_id=rendered.occurrence_id,
            claim_text=clean_claim,
            claim_type=claim_type,
            authorized_evidence_ids=allowed_ids,
            evidence_spans=evidence_spans,
            supporting_span=deterministic_span,
            deterministic_result=deterministic,
            semantic_result="NOT_RUN",
            final_status=deterministic,
            resolution=ClaimResolution.DETERMINISTIC,
            unsupported_part="" if deterministic == DeterministicStatus.SUPPORTED else clean_claim,
            rationale=deterministic_reason,
            confidence=0.98 if deterministic == DeterministicStatus.SUPPORTED else 1.0,
            source_span=claim_text,
            strengthening_signals=strengthening,
        )
    if judge is None:
        return RenderedClaimAuditRecord(
            claim_id=claim_id,
            occurrence_id=rendered.occurrence_id,
            claim_text=clean_claim,
            claim_type=claim_type,
            authorized_evidence_ids=allowed_ids,
            evidence_spans=evidence_spans,
            supporting_span=deterministic_span,
            deterministic_result=deterministic,
            semantic_result="NOT_RUN",
            final_status=ClaimStatus.UNSUPPORTED,
            resolution=ClaimResolution.SEMANTIC_UNRESOLVED,
            unsupported_part="semantic judgement not run",
            rationale=(
                "Calibrated routing requires semantic judgement, but no semantic judge was supplied."
                if force_semantic
                else "Deterministic verifier could not decide this paraphrase; no semantic judge was supplied."
            ),
            confidence=0.0,
            source_span=claim_text,
            strengthening_signals=strengthening,
        )
    try:
        proposal_payload = judge.judge(
            claim=clean_claim,
            evidence=[{"evidence_id": item.evidence_id, "span": item.span} for item in evidence_spans],
            context={
                "role": str(brief.get("role", "")),
                "section_id": str(brief.get("section_id", "")),
                "current_occurrence": rendered.occurrence_id,
            },
        )
        proposal = _validate_proposal(proposal_payload, allowed_ids)
        support_ids = proposal.supporting_evidence_ids
        support_span = _supporting_span(support_ids, evidence_spans)
        return RenderedClaimAuditRecord(
            claim_id=claim_id,
            occurrence_id=rendered.occurrence_id,
            claim_text=clean_claim,
            claim_type=claim_type,
            authorized_evidence_ids=allowed_ids,
            evidence_spans=evidence_spans,
            supporting_span=support_span,
            deterministic_result=deterministic,
            semantic_result=proposal.status,
            final_status=proposal.status,
            resolution=ClaimResolution.SEMANTIC,
            unsupported_part=proposal.unsupported_part,
            rationale=proposal.rationale,
            confidence=proposal.confidence,
            model=proposal.model,
            prompt_version=proposal.prompt_version or PROMPT_VERSION,
            source_span=claim_text,
            evidence_provenance_valid=True,
            strengthening_signals=strengthening,
        )
    except Exception as exc:  # fail closed; preserve the audit cause
        return RenderedClaimAuditRecord(
            claim_id=claim_id,
            occurrence_id=rendered.occurrence_id,
            claim_text=clean_claim,
            claim_type=claim_type,
            authorized_evidence_ids=allowed_ids,
            evidence_spans=evidence_spans,
            supporting_span=deterministic_span,
            deterministic_result=deterministic,
            semantic_result="ERROR",
            final_status=ClaimStatus.UNSUPPORTED,
            resolution=ClaimResolution.MODEL_ERROR,
            unsupported_part="semantic judge error",
            rationale="Semantic judge output failed schema/provenance validation; claim is fail-closed.",
            confidence=0.0,
            source_span=claim_text,
            evidence_provenance_valid=False,
            strengthening_signals=strengthening,
            error=f"{type(exc).__name__}: {exc}",
        )


def _allowed_evidence_ids(brief: dict[str, Any]) -> tuple[str, ...]:
    values = [*(brief.get("source_chunk_ids") or []), *(brief.get("semantic_delta_evidence_ids") or [])]
    return tuple(dict.fromkeys(str(item) for item in values if str(item)))


def _claim_calibration_category(claim: str) -> str:
    """Assign a domain-independent routing stratum to a claim."""
    if re.search(r"(?:导致|造成|原因|因果|相关|关联|影响|causes|leads to|associated|related)", claim, re.IGNORECASE):
        return CalibrationStratum.CAUSAL
    if re.search(r"(?:\d+(?:\.\d+)?|如果|若|当|范围|厚度|流量|温度|电流|电压|大于|小于|between|if|when)", claim, re.IGNORECASE):
        return CalibrationStratum.QUANTITATIVE_CONDITIONAL
    if re.search(r"(?:操作|步骤|检查|设置|调整|安装|使用|完成|执行|过程|procedure|step|check|set|adjust|install|operate)", claim, re.IGNORECASE):
        return CalibrationStratum.PROCEDURAL
    if re.search(r"(?:必须|通常|可能|可以|能够|避免|保证|所有|仅|一定|may|might|often|usually|must|can|guarantee|all|only)", claim, re.IGNORECASE):
        return CalibrationStratum.MODALITY_SCOPE
    return CalibrationStratum.ORDINARY_DESCRIPTIVE


def _stable_claim_digest(claim_id: str, seed: int) -> str:
    return sha256(f"{seed}:{claim_id}".encode("utf-8")).hexdigest()


def _stratified_calibration_sample(
    eligible: list[RenderedClaimAuditRecord],
    *,
    sample_size: int,
    seed: int,
) -> list[RenderedClaimAuditRecord]:
    """Select a deterministic sample with chapter and semantic-stratum spread."""
    if sample_size <= 0 or not eligible:
        return []
    by_id = {item.claim_id: item for item in eligible}
    ordered = sorted(eligible, key=lambda item: _stable_claim_digest(item.claim_id, seed))
    selected: list[RenderedClaimAuditRecord] = []
    selected_ids: set[str] = set()

    # First reserve one claim per chapter/occurrence prefix, preventing a
    # large chapter from consuming the entire blind calibration sample.
    chapter_groups: dict[str, list[RenderedClaimAuditRecord]] = {}
    for item in ordered:
        chapter = item.occurrence_id.split(":")[1] if ":" in item.occurrence_id else item.occurrence_id
        chapter_groups.setdefault(chapter, []).append(item)
    for chapter in sorted(chapter_groups):
        if len(selected) >= sample_size:
            break
        candidate = chapter_groups[chapter][0]
        selected.append(candidate)
        selected_ids.add(candidate.claim_id)

    strata_order = (
        CalibrationStratum.MODALITY_SCOPE,
        CalibrationStratum.CAUSAL,
        CalibrationStratum.PROCEDURAL,
        CalibrationStratum.QUANTITATIVE_CONDITIONAL,
        CalibrationStratum.ORDINARY_DESCRIPTIVE,
    )
    strata: dict[str, list[RenderedClaimAuditRecord]] = {item: [] for item in strata_order}
    for item in ordered:
        strata.setdefault(_claim_calibration_category(item.claim_text), []).append(item)
    cursors = {item: 0 for item in strata}
    while len(selected) < min(sample_size, len(eligible)):
        progressed = False
        for category in strata_order:
            candidates = strata.get(category, [])
            while cursors[category] < len(candidates) and candidates[cursors[category]].claim_id in selected_ids:
                cursors[category] += 1
            if cursors[category] >= len(candidates):
                continue
            candidate = candidates[cursors[category]]
            cursors[category] += 1
            selected.append(candidate)
            selected_ids.add(candidate.claim_id)
            progressed = True
            if len(selected) >= min(sample_size, len(eligible)):
                break
        if not progressed:
            break
    return selected


def calibrate_lexically_supported_claims(
    *,
    audit: RenderedClaimSemanticEvidenceAudit,
    briefs: list[dict[str, Any]],
    judge: EntailmentJudge | None,
    artifact_root: str = "",
    sample_size: int = 30,
    seed: int = 20260822,
) -> LexicalCalibrationReport:
    """Blindly semantic-audit a stratified sample of lexical-SUPPORTED claims."""
    eligible = [
        item for item in audit.records
        if item.deterministic_result == DeterministicStatus.SUPPORTED
        and item.resolution == ClaimResolution.DETERMINISTIC
    ]
    selected = _stratified_calibration_sample(eligible, sample_size=sample_size, seed=seed)
    brief_by_id = {str(item.get("occurrence_id")): item for item in briefs if item.get("occurrence_id")}
    records: list[LexicalCalibrationRecord] = []
    for item in selected:
        stratum = _claim_calibration_category(item.claim_text)
        brief = brief_by_id.get(item.occurrence_id, {})
        if judge is None:
            records.append(LexicalCalibrationRecord(
                claim_id=item.claim_id,
                occurrence_id=item.occurrence_id,
                claim_text=item.claim_text,
                stratum=stratum,
                authorized_evidence_ids=item.authorized_evidence_ids,
                evidence_spans=item.evidence_spans,
                lexical_result=DeterministicStatus.SUPPORTED,
                semantic_result="NOT_RUN",
                resolution=CalibrationResolution.MODEL_ERROR,
                supporting_evidence_ids=(),
                supporting_span="",
                unsupported_part="semantic calibration judge not supplied",
                rationale="Blind calibration cannot be accepted without a semantic judge.",
                confidence=0.0,
                false_positive=False,
                false_positive_category="",
                strengthening_signals=item.strengthening_signals,
                evidence_provenance_valid=False,
                error="judge_not_supplied",
            ))
            continue
        try:
            payload = judge.judge(
                claim=item.claim_text,
                evidence=[{"evidence_id": span.evidence_id, "span": span.span} for span in item.evidence_spans],
                context={
                    "role": str(brief.get("role", "")),
                    "section_id": str(brief.get("section_id", "")),
                    "current_occurrence": item.occurrence_id,
                },
            )
            proposal = _validate_proposal(payload, item.authorized_evidence_ids)
            false_positive = proposal.status in {ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.UNSUPPORTED}
            records.append(LexicalCalibrationRecord(
                claim_id=item.claim_id,
                occurrence_id=item.occurrence_id,
                claim_text=item.claim_text,
                stratum=stratum,
                authorized_evidence_ids=item.authorized_evidence_ids,
                evidence_spans=item.evidence_spans,
                lexical_result=DeterministicStatus.SUPPORTED,
                semantic_result=proposal.status,
                resolution=CalibrationResolution.SEMANTIC,
                supporting_evidence_ids=proposal.supporting_evidence_ids,
                supporting_span=_supporting_span(proposal.supporting_evidence_ids, item.evidence_spans),
                unsupported_part=proposal.unsupported_part,
                rationale=proposal.rationale,
                confidence=proposal.confidence,
                false_positive=false_positive,
                false_positive_category=stratum if false_positive else "",
                strengthening_signals=item.strengthening_signals,
                model=proposal.model,
                prompt_version=proposal.prompt_version or PROMPT_VERSION,
            ))
        except Exception as exc:
            records.append(LexicalCalibrationRecord(
                claim_id=item.claim_id,
                occurrence_id=item.occurrence_id,
                claim_text=item.claim_text,
                stratum=stratum,
                authorized_evidence_ids=item.authorized_evidence_ids,
                evidence_spans=item.evidence_spans,
                lexical_result=DeterministicStatus.SUPPORTED,
                semantic_result="ERROR",
                resolution=CalibrationResolution.MODEL_ERROR,
                supporting_evidence_ids=(),
                supporting_span="",
                unsupported_part="semantic calibration judge error",
                rationale="Calibration output failed schema/provenance validation.",
                confidence=0.0,
                false_positive=False,
                false_positive_category="",
                strengthening_signals=item.strengthening_signals,
                evidence_provenance_valid=False,
                error=f"{type(exc).__name__}: {exc}",
            ))
    result = LexicalCalibrationReport(
        artifact_root=artifact_root,
        seed=seed,
        requested_sample_size=sample_size,
        eligible_lexical_supported=len(eligible),
        records=records,
        qwen_call_count=int(getattr(judge, "call_count", 0) if judge else 0),
        qwen_model=str(getattr(judge, "model", "") if judge else ""),
        prompt_version=PROMPT_VERSION if judge else "",
    )
    if not result.systematic_categories:
        result.notes.append("No stratum met the systematic false-positive threshold; no production routing expansion is recommended.")
    elif set(result.systematic_categories) == set(CALIBRATED_SEMANTIC_ROUTING_CATEGORIES):
        result.notes.append(
            "All observed source-fact strata met the systematic threshold; production routing is expanded only for these "
            "source-fact strata, while headings, navigation, templates, and other non-factual text remain out of scope."
        )
    else:
        result.notes.append("Only the listed systematic strata should be added to semantic routing; do not route all lexical claims.")
    return result


def _evidence_spans(chunks: list[EvidenceChunk]) -> list[ClaimEvidenceSpan]:
    result: list[ClaimEvidenceSpan] = []
    for chunk in chunks:
        source = " ".join(part.strip() for part in (chunk.summary, chunk.content) if part and part.strip())
        source = re.sub(r"\s+", " ", source).strip()
        result.append(ClaimEvidenceSpan(chunk.chunk_id, source[:1200]))
    return result


def _supporting_span(ids: tuple[str, ...], spans: tuple[ClaimEvidenceSpan, ...]) -> str:
    return "\n".join(item.span for item in spans if item.evidence_id in ids)


def _deterministic_result(
    claim: str,
    *,
    cited_ids: tuple[str, ...],
    allowed_ids: tuple[str, ...],
    chunks: list[EvidenceChunk],
    strengthening_signals: tuple[str, ...],
) -> tuple[str, tuple[str, ...], str, str]:
    if cited_ids and not set(cited_ids).issubset(set(allowed_ids)):
        return DeterministicStatus.UNSUPPORTED, cited_ids, "", "Claim cites evidence outside the occurrence-authorized evidence set."
    if not chunks:
        return DeterministicStatus.UNSUPPORTED, (), "", "The occurrence has no authorized evidence chunk."
    matching = [chunk for chunk in chunks if _has_direct_support(claim, chunk)]
    if not matching:
        return DeterministicStatus.UNCERTAIN, (), "", "No direct lexical evidence match; semantic entailment is required."
    matched_ids = tuple(item.chunk_id for item in matching)
    span = _supporting_span(matched_ids, tuple(_evidence_spans(matching)))
    if strengthening_signals:
        return DeterministicStatus.UNCERTAIN, matched_ids, span, "Lexical overlap exists, but claim-strengthening signals require semantic judgement."
    if cited_ids:
        return DeterministicStatus.SUPPORTED, cited_ids, span, "Cited evidence IDs are authorized and the claim has direct lexical support without strengthening signals."
    return DeterministicStatus.SUPPORTED, matched_ids, span, "Direct lexical support found in authorized evidence without strengthening signals."


def _has_direct_support(claim: str, chunk: EvidenceChunk) -> bool:
    """Require more than one generic Chinese overlap before bypassing entailment."""
    source = " ".join(part for part in (chunk.summary, chunk.content) if part)
    claim_terms = set(_content_terms(claim))
    source_terms = set(_content_terms(source))
    if claim_terms & source_terms:
        return True
    return len(_chinese_bigrams(claim) & _chinese_bigrams(source)) >= 2


def _strengthening_signals(claim: str, chunks: list[EvidenceChunk]) -> list[str]:
    source = " ".join(" ".join(part for part in (item.summary, item.content) if part) for item in chunks)
    result: list[str] = []
    for weak_words, strong_words, label in _WEAK_STRONG_PAIRS:
        claim_has_strong = any(word.lower() in claim.lower() for word in strong_words)
        source_has_weak = any(word.lower() in source.lower() for word in weak_words)
        if claim_has_strong and source_has_weak:
            result.append(label)
    if _NEGATION.search(claim) and not _NEGATION.search(source):
        result.append("negation_shift")
    return result


def _extract_claims(body: str) -> list[str]:
    body = _INTERNAL_MARKER.sub("", body)
    claims: list[str] = []
    for sentence in _CLAIM_SPLIT.split(body):
        clean = re.sub(r"^\s*[-*]\s*", "", sentence).strip()
        if not clean or _TEMPLATE_ONLY.match(clean):
            continue
        # A semicolon-delimited sentence contains independently auditable claims.
        for clause in re.split(r"(?<=[；;])\s*", clean):
            clause = clause.strip()
            if not clause or _TEMPLATE_ONLY.match(clause):
                continue
            if not _has_claim_terms(clause):
                continue
            if _NON_FACTUAL.match(clause) and not _has_claim_terms(clause, minimum=4):
                continue
            claims.append(clause)
    return claims


def _is_auditable_claim(text: str) -> bool:
    """Exclude pure navigation/trajectory prose; keep mixed factual claims."""
    return _claim_type(text) not in {"TRAJECTORY_FACT", "STRUCTURAL_FACT"}


def _has_claim_terms(text: str, *, minimum: int = 2) -> bool:
    """Count Chinese content by bigrams; a whole Chinese sentence is one regex term."""
    return len(_content_terms(text)) + len(_chinese_bigrams(text)) >= minimum


def _validate_proposal(payload: Any, allowed_ids: tuple[str, ...]) -> SemanticEntailmentProposal:
    if not isinstance(payload, dict):
        raise ValueError("semantic judge response must be an object")
    status = str(payload.get("status", "")).upper()
    if status not in {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.UNSUPPORTED}:
        raise ValueError(f"invalid entailment status: {status!r}")
    support_ids = tuple(dict.fromkeys(str(item) for item in (payload.get("supporting_evidence_ids") or [])))
    if not set(support_ids).issubset(set(allowed_ids)):
        raise ValueError("semantic judge returned an unauthorized evidence ID")
    confidence = float(payload.get("confidence", 0.0))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("semantic judge confidence must be between 0 and 1")
    rationale = str(payload.get("rationale", "")).strip()
    if not rationale:
        raise ValueError("semantic judge rationale is required")
    return SemanticEntailmentProposal(
        status=status,
        supporting_evidence_ids=support_ids,
        rationale=rationale,
        confidence=confidence,
        unsupported_part=str(payload.get("unsupported_part", "")),
        model=str(payload.get("model", "")),
        prompt_version=str(payload.get("prompt_version", "")),
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("semantic judge did not return JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("semantic judge JSON must be an object")
    return value


def render_audit_markdown(report: RenderedClaimSemanticEvidenceAudit) -> str:
    summary = report.to_dict()["summary"]
    lines = [
        "# Rendered Claim Semantic Evidence Audit",
        "",
        f"- artifact root: `{report.artifact_root}`",
        f"- total source-fact claims: {summary['total_source_fact_claims']}",
        f"- lexically SUPPORTED: {summary['lexically_supported']}",
        f"- deterministic SUPPORTED: {summary['deterministic_supported']}",
        f"- deterministic UNSUPPORTED: {summary['deterministic_unsupported']}",
        f"- semantic entailment candidates: {summary['semantic_entailment_candidates']}",
        f"- Qwen entailment call count: {summary['qwen_entailment_call_count']}",
        f"- semantically SUPPORTED: {summary['semantically_supported']}",
        f"- PARTIALLY_SUPPORTED: {summary['partially_supported']}",
        f"- truly UNSUPPORTED: {summary['truly_unsupported']}",
        f"- semantic unresolved: {summary['semantic_unresolved']}",
        "",
        "## Claim audit",
        "",
    ]
    for item in report.records:
        lines.extend([
            f"### {item.claim_id}",
            f"- occurrence: `{item.occurrence_id}`",
            f"- final: **{item.final_status}** (deterministic={item.deterministic_result}; semantic={item.semantic_result}; resolution={item.resolution})",
            f"- authorized evidence: {', '.join(item.authorized_evidence_ids) or '(none)'}",
            f"- supporting span: {item.supporting_span or '(none)'}",
            f"- unsupported part: {item.unsupported_part or '(none)'}",
            f"- strengthening signals: {', '.join(item.strengthening_signals) or '(none)'}",
            f"- confidence: {item.confidence:.2f}",
            f"- rationale: {item.rationale}",
            "- claim:",
            "```text",
            item.claim_text,
            "```",
            "",
        ])
    if report.notes:
        lines.extend(["## Notes", "", *[f"- {item}" for item in report.notes], ""])
    return "\n".join(lines).rstrip() + "\n"


def render_lexical_calibration_markdown(report: LexicalCalibrationReport) -> str:
    summary = report.to_dict()["summary"]
    lines = [
        "# Lexical Support Blind Calibration",
        "",
        f"- artifact root: `{report.artifact_root}`",
        f"- eligible lexical-SUPPORTED claims: {summary['eligible_lexical_supported']}",
        f"- stratified sample: {summary['sample_size']} / requested {report.requested_sample_size}",
        f"- Qwen calls: {summary['qwen_entailment_call_count']}",
        f"- semantic SUPPORTED: {summary['semantically_supported']}",
        f"- PARTIALLY_SUPPORTED: {summary['partially_supported']}",
        f"- UNSUPPORTED: {summary['truly_unsupported']}",
        f"- lexical false positives: {summary['lexical_false_positive_count']}",
        f"- false-positive rate: {summary['lexical_false_positive_rate']:.3f}",
        f"- false-positive strengthening signals: {summary['false_positive_strengthening_signals'] or '(none)' }",
        f"- systematic routing categories: {', '.join(summary['systematic_routing_categories']) or '(none)' }",
        "",
        "## Blind sample records",
        "",
    ]
    for item in report.records:
        lines.extend([
            f"### {item.claim_id}",
            f"- occurrence: `{item.occurrence_id}`",
            f"- stratum: `{item.stratum}`",
            f"- lexical result: `{item.lexical_result}` (not sent to the model)",
            f"- semantic result: **{item.semantic_result}**",
            f"- false positive: `{item.false_positive}`",
            f"- category: `{item.false_positive_category or '(none)'}`",
            f"- strengthening signals: {', '.join(item.strengthening_signals) or '(none)' }",
            f"- authorized evidence: {', '.join(item.authorized_evidence_ids) or '(none)' }",
            f"- confidence: {item.confidence:.2f}",
            f"- rationale: {item.rationale}",
            "- claim:",
            "```text",
            item.claim_text,
            "```",
            "",
        ])
    if report.notes:
        lines.extend(["## Notes", "", *[f"- {item}" for item in report.notes], ""])
    return "\n".join(lines).rstrip() + "\n"


def write_audit_artifacts(report: RenderedClaimSemanticEvidenceAudit, *, output_dir: str) -> tuple[str, str]:
    from pathlib import Path

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "rendered_claim_evidence_audit.json"
    markdown_path = root / "rendered_claim_evidence_audit.md"
    write_json(json_path, report.to_dict())
    write_text(markdown_path, render_audit_markdown(report))
    return str(json_path), str(markdown_path)


def write_lexical_calibration_artifacts(report: LexicalCalibrationReport, *, output_dir: str) -> tuple[str, str]:
    from pathlib import Path

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "rendered_claim_lexical_calibration.json"
    markdown_path = root / "rendered_claim_lexical_calibration.md"
    write_json(json_path, report.to_dict())
    write_text(markdown_path, render_lexical_calibration_markdown(report))
    return str(json_path), str(markdown_path)
