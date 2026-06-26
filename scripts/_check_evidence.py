import json

# Check evidence chunks for video segments
path = "/ai/data/materials2textbook/topic_textbook_test/evidence_chunks.jsonl"
video_chunks = []
doc_chunks = []
with open(path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        chunk = json.loads(line)
        if chunk.get("source_type") in ("video_segment", "video", "audio_segment"):
            video_chunks.append(chunk)
        else:
            doc_chunks.append(chunk)

print(f"Total chunks: {len(video_chunks) + len(doc_chunks)}")
print(f"Video chunks: {len(video_chunks)}")
print(f"Doc chunks: {len(doc_chunks)}")

if video_chunks:
    c = video_chunks[0]
    print(f"\nSample video chunk:")
    print(f"  chunk_id: {c.get('chunk_id')}")
    print(f"  source_type: {c.get('source_type')}")
    print(f"  title: {c.get('title')}")
    print(f"  locator.path: {c.get('locator', {}).get('path', '')}")
    print(f"  asset_id: {c.get('asset_id')}")
else:
    print("\nNO VIDEO CHUNKS in evidence_chunks.jsonl!")

# Check if chapter plans reference video chunk IDs
plan_path = "/ai/data/materials2textbook/topic_textbook_test/chapter_plan.json"
if __import__("os").path.exists(plan_path):
    plan = json.load(open(plan_path, encoding="utf-8"))
    video_chunk_ids = {c["chunk_id"] for c in video_chunks}
    for ch in plan.get("chapters", plan if isinstance(plan, list) else []):
        if isinstance(ch, dict):
            ch_chunks = ch.get("evidence_chunk_ids", [])
            video_in_ch = [c for c in ch_chunks if c in video_chunk_ids]
            print(f"\n{ch.get('title','?')}: {len(ch_chunks)} chunks, {len(video_in_ch)} video")
