#!/usr/bin/env python3
"""Full-corpus concurrent ResourceAnalystAgent for panjunyi material bank.

Reads the 5 main JSONL segment files, converts each record to EvidenceChunk via
the project adapters, then concurrently calls ResourceAnalystAgent._enhance_chunk
against a Qwen3-32B-AWQ served by vLLM.

Critical: uses NoThinkingProvider to disable Qwen3 thinking mode
(chat_template_kwargs.enable_thinking=False) AND strips markdown code fences,
otherwise _parse_json_object rejects the response and every chunk falls back.

Outputs (under OUT_DIR = 05_final_deliverables/full_ra_run/):
  - llm_cache_full_ra.json          {sha256(messages): response_str}
  - evidence_chunks_full_ra.jsonl   enhanced EvidenceChunk records
  - summary.json                    run statistics

Env vars:
  ECNU_PLUS_BASE_URL/_API_KEY/_MODEL/_TEMPERATURE/_MAX_TOKENS/_TIMEOUT_SECONDS
  RA_WORKERS   concurrency (default 32)
  RA_SMOKE_N   >0 = only run first N valid chunks
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

REPO_SRC = "/ai/data/repos/work-data/src"
sys.path.insert(0, REPO_SRC)

from materials2textbook.adapters.document_segments import document_segment_to_evidence_chunk
from materials2textbook.adapters.video_segments import video_segment_to_evidence_chunk
from materials2textbook.agents.resource_analyst import ResourceAnalystAgent
from materials2textbook.llm.cache import CachingLLMProvider, build_llm_cache_key
from materials2textbook.llm.provider import OpenAICompatibleConfig
from materials2textbook.llm.retry import RetryingLLMProvider

DATA_ROOT = Path(os.getenv("RA_DATA_ROOT", "/ai/data/materials2textbook/work_material_panjunyi"))
JSON_DIR = DATA_ROOT / "02_working_processing/json"
OUT_DIR = Path(os.getenv("RA_OUT_DIR", str(DATA_ROOT / "05_final_deliverables/full_ra_run")))
CACHE_PATH = OUT_DIR / os.getenv("RA_CACHE_NAME", "llm_cache_full_ra.json")
CHUNKS_PATH = OUT_DIR / os.getenv("RA_CHUNKS_NAME", "evidence_chunks_full_ra.jsonl")
SUMMARY_PATH = OUT_DIR / "summary.json"

MAX_WORKERS = int(os.getenv("RA_WORKERS", "32"))
SMOKE_N = int(os.getenv("RA_SMOKE_N", "0"))
DOC_FILES = ["ppt_assets.jsonl", "reference_text_assets.jsonl", "audio_segments.jsonl", "structured_assets.jsonl"]

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class NoThinkingProvider:
    """OpenAI-compatible provider that disables Qwen3 thinking and strips code fences.

    Qwen3-32B-AWQ defaults to emitting <think>...</think> reasoning before the JSON
    answer. We pass chat_template_kwargs.enable_thinking=False (the only flag vLLM
    honors; top-level enable_thinking is ignored). Even with thinking off the model
    sometimes wraps the JSON in ```json ... ``` fences, which we strip so the
    project's _parse_json_object can json.loads it directly.
    """

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self.config.is_configured:
            raise RuntimeError(
                "LLM provider not configured. Set ECNU_PLUS_BASE_URL/_API_KEY/_MODEL."
            )
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = urllib.request.Request(
            url=f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {body[:800]}") from exc
        content = data["choices"][0]["message"]["content"]
        fence_match = _CODE_FENCE_RE.search(content)
        if fence_match:
            content = fence_match.group(1)
        return content


class ThreadSafeCachingProvider:
    """Lock-guarded wrapper around CachingLLMProvider for concurrent access.

    Cache hit: held under lock (fast dict lookup).
    Cache miss: real LLM call is NOT held under lock (allows true concurrency).
    Cache write: held under lock (serializes file _save, which is cheap).
    """

    def __init__(self, caching: CachingLLMProvider) -> None:
        self.caching = caching
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        key = build_llm_cache_key(messages)
        with self.lock:
            cached = self.caching._cache.get(key)
            if cached is not None:
                self.hits += 1
                return cached
        self.misses += 1
        response = self.caching.provider.generate(messages)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("LLM returned empty response, refusing to cache.")
        with self.lock:
            self.caching._cache[key] = response
            self.caching._save()
        return response


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    video_file = Path(os.getenv("RA_VIDEO_FILE", str(JSON_DIR / "video_segments.jsonl")))
    doc_files_env = os.getenv("RA_DOC_FILES", "")
    if doc_files_env:
        doc_file_paths = [Path(p.strip()) for p in doc_files_env.split(":") if p.strip()]
    else:
        doc_file_paths = [JSON_DIR / fn for fn in DOC_FILES]
    print(f"[load] video_file={video_file}", flush=True)
    video_records = read_jsonl(video_file)
    doc_records: list[dict] = []
    for fp in doc_file_paths:
        print(f"[load] doc_file={fp}", flush=True)
        doc_records.extend(read_jsonl(fp))
    total_input = len(video_records) + len(doc_records)
    print(f"[load] video={len(video_records)}, document={len(doc_records)}, total={total_input}", flush=True)

    chunks = [video_segment_to_evidence_chunk(r) for r in video_records]
    chunks += [document_segment_to_evidence_chunk(r) for r in doc_records]
    valid = [c for c in chunks if c.chunk_id and c.asset_id]
    print(
        f"[adapt] converted={len(chunks)}, valid(chunk_id&asset_id)={len(valid)}, "
        f"dropped={len(chunks) - len(valid)}",
        flush=True,
    )

    if SMOKE_N > 0:
        valid = valid[:SMOKE_N]
        print(f"[smoke] RA_SMOKE_N={SMOKE_N}, limiting to first {len(valid)} chunks", flush=True)

    if not valid:
        print("[run] nothing to do, exiting", flush=True)
        return

    config = OpenAICompatibleConfig.from_env("ECNU_PLUS")
    if not config.is_configured:
        raise SystemExit(
            "ECNU_PLUS_* env vars not configured. Export ECNU_PLUS_BASE_URL, "
            "_API_KEY, _MODEL before running."
        )
    print(
        f"[provider] model={config.model} base_url={config.base_url} "
        f"temp={config.temperature} max_tokens={config.max_tokens} timeout={config.timeout_seconds}s "
        f"thinking=disabled",
        flush=True,
    )

    base = NoThinkingProvider(config)
    retrying = RetryingLLMProvider(base, max_retries=2, backoff_seconds=1.0)
    caching = CachingLLMProvider(retrying, CACHE_PATH)
    safe = ThreadSafeCachingProvider(caching)
    print(f"[cache] resumed existing entries={len(caching._cache)} from {CACHE_PATH}", flush=True)

    agent = ResourceAnalystAgent(llm_provider=safe, use_llm=True)

    results: list = [None] * len(valid)
    done = 0
    failures = 0
    run_t0 = time.time()
    print(f"[run] concurrent enhance start: workers={MAX_WORKERS} items={len(valid)}", flush=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        future_to_idx = {ex.submit(agent._enhance_chunk, chunk): i for i, chunk in enumerate(valid)}
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:
                failures += 1
                m = dict(valid[i].metadata)
                m["llm_resource_analysis"] = {
                    "enabled": True,
                    "fallback": True,
                    "error": str(exc)[:300],
                }
                results[i] = replace(valid[i], metadata=m)
            done += 1
            if done % 100 == 0 or done == len(valid):
                elapsed = time.time() - run_t0
                rate = done / elapsed if elapsed > 0 else 0.0
                eta = (len(valid) - done) / rate if rate > 0 else 0.0
                print(
                    f"[progress] {done}/{len(valid)} done, failures={failures}, "
                    f"cache hit={safe.hits} miss={safe.misses}, "
                    f"rate={rate:.2f}/s, elapsed={elapsed:.0f}s, eta={eta:.0f}s",
                    flush=True,
                )

    written = 0
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        for chunk in results:
            if chunk is None:
                continue
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
            written += 1

    summary = {
        "input_total": total_input,
        "converted_chunks": len(chunks),
        "valid_chunks": len(valid),
        "output_chunks": written,
        "failures": failures,
        "cache_hits": safe.hits,
        "cache_misses": safe.misses,
        "cache_entries_total": len(caching._cache),
        "cache_path": str(CACHE_PATH),
        "chunks_path": str(CHUNKS_PATH),
        "workers": MAX_WORKERS,
        "smoke_n": SMOKE_N,
        "model": config.model,
        "thinking": "disabled",
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {json.dumps(summary, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
