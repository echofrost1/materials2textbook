"""Fix video paths from cloud-relative to local assets/videos/."""
import json
from pathlib import Path

book_path = Path(r"D:\Code\vibe coding\materials2textbook\digital_book\digital_book.json")
data = json.loads(book_path.read_text(encoding="utf-8"))

fixed = 0
for proj in data.get("projects", []):
    for task in proj.get("tasks", []):
        for block in task.get("blocks", []):
            if block.get("type") != "video":
                continue
            src = block.get("src", "")
            if "converted_mp4/" in src:
                filename = src.split("/")[-1]
                block["src"] = f"assets/videos/{filename}"
                fixed += 1
            poster = block.get("poster", "")
            if "keyframes/" in poster and "assets/" not in poster:
                filename = poster.split("/")[-1]
                block["poster"] = f"assets/keyframes/{filename}"

book_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Fixed {fixed} video paths")
