# Examples

This repository intentionally does not include real teaching materials or generated textbook outputs.

For a small local smoke test, create your own toy evidence file under a temporary material root:

```text
D:\textbook_runs\smoke_topic\
└── work_material1\
    └── 02_working_processing\
        └── json\
            └── reference_text_assets.jsonl
```

Then run:

```powershell
python scripts/run_topic_textbook.py `
  --material-root D:\textbook_runs\smoke_topic\work_material1 `
  --title "Smoke Topic" `
  --use-llm false `
  --min-evidence-chunks 2 `
  --min-candidate-chapters 1
```

Keep real materials, processed JSONL files, and generated deliverables outside Git.
