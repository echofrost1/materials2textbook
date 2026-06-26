import json
path = "/ai/data/materials2textbook/digital_book/digital_book.json"
d = json.load(open(path, encoding="utf-8"))
video_blocks = []
for p in d.get("projects", []):
    for t in p.get("tasks", []):
        for b in t.get("blocks", []):
            if b.get("type") == "video":
                video_blocks.append({"task": t["title"], "title": b["title"], "src": b.get("src",""), "chunk_ids": b.get("evidence_chunk_ids",[])})
print(f"Video blocks: {len(video_blocks)}")
for v in video_blocks[:5]:
    print(f"  {v['task']} > {v['title']}: src={v['src']}, chunks={v['chunk_ids']}")

# Check what block types exist
from collections import Counter
types = Counter()
for p in d.get("projects", []):
    for t in p.get("tasks", []):
        for b in t.get("blocks", []):
            types[b.get("type","")] += 1
print(f"\nBlock types: {dict(types)}")
