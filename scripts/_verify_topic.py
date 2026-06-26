import json, os
path = "/ai/data/materials2textbook/digital_book/digital_book.json"
d = json.load(open(path, encoding="utf-8"))
print(f"Title: {d['title']}")
print(f"Projects: {len(d.get('projects', []))}")
for p in d.get("projects", []):
    tasks = p.get("tasks", [])
    blocks = sum(len(t.get("blocks", [])) for t in tasks)
    print(f"  {p['title']}: {len(tasks)} tasks, {blocks} blocks")
    for t in tasks[:3]:
        kps = t.get("knowledge_points", [])
        print(f"    {t['title']}: {len(kps)} KPs")
print(f"\nFile size: {os.path.getsize(path)//1024} KB")
