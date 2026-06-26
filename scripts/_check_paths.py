import json

# Check raw video_segments.jsonl for path fields
path = "/ai/data/materials2textbook/work_material1/02_working_processing/json/video_segments.jsonl"
with open(path, encoding="utf-8") as f:
    for i, line in enumerate(f):
        if i >= 3:
            break
        r = json.loads(line.strip())
        print(f"--- Record {i} (clip_id={r.get('clip_id')}) ---")
        for k in ("clip_output_path", "converted_mp4", "convert_status", "source_video", "original_path"):
            v = r.get(k, "")
            print(f"  {k}: {v}")

# Compare with selected_video_segments.jsonl (old pipeline input)
print("\n=== selected_video_segments.jsonl (old pipeline) ===")
old_path = "/ai/data/materials2textbook/digital_book_full/selected_video_segments.jsonl"
if __import__("os").path.exists(old_path):
    with open(old_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 2:
                break
            r = json.loads(line.strip())
            print(f"--- Record {i} (clip_id={r.get('clip_id')}) ---")
            for k in ("clip_output_path", "converted_mp4", "source_video"):
                v = r.get(k, "")
                print(f"  {k}: {v}")
else:
    print("NOT FOUND")
