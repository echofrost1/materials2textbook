#!/bin/bash
set -e
source /ai/data/use_ai_env.sh

cd /ai/data/repos/work-manuscript

export PYTHONPATH="src:scripts"

OUTPUT_DIR="/ai/data/materials2textbook/digital_book_full"
MANIFEST="/ai/data/materials2textbook/work_material1/01_manifest_inventory/chapter_asset_map_reviewed.xlsx"
DOC_SEGMENTS="/ai/data/materials2textbook/work_material1/02_working_processing/json/reference_text_assets.jsonl"

mkdir -p "$OUTPUT_DIR"

echo "=== Starting full TextbookWorkflow pipeline ==="
echo "Output: $OUTPUT_DIR"
echo "Manifest: $MANIFEST"
echo "ResourceAnalyst LLM: ENABLED (per-record, with cache)"
echo "LLM Cache: ENABLED (incremental, resumable)"
echo "Start: $(date)"
echo ""

python scripts/run_full_digital_textbook.py \
    --use-llm \
    --book-mode \
    --force-rebuild-chapters \
    --manifest-xlsx "$MANIFEST" \
    --document-segments "$DOC_SEGMENTS" \
    --output-dir "$OUTPUT_DIR" \
    --title "焊接技术数字教材" \
    --review-rounds 2 \
    --max-input-tokens 12000 \
    --max-tokens-per-evidence-chunk 1200 \
    --summarize-over-budget \
    --max-chapter-input-tokens 12000 \
    --copy-media-assets \
    --llm-max-retries 3 \
    --llm-retry-backoff 2.0 \
    2>&1

echo ""
echo "=== Pipeline complete ==="
echo "End: $(date)"
