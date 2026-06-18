# AGENTS.md

These instructions are for Codex and other coding agents working on this cloud
project.

## Project Layout

- Main repository and management worktree: `/root/materials2textbook`
- Data processing worktree: `/root/work-data`
- Manuscript and textbook writing worktree: `/root/work-manuscript`
- Persistent project data: `/ai/data/materials2textbook`
- Model weights: `/ai/data/models`
- Model caches: `/ai/data/model-cache`
- Service scripts and runtime config: `/ai/data/services`

Use `/root` for code, tools, environments, and editor state. Use `/ai/data` for
anything large or persistent.

## Collaboration Rules

- Do not use `/root/materials2textbook` for daily feature work. Treat it as the
  `main` branch and management entry point.
- Use `/root/work-data` for data ingestion, material inventory, cleaning,
  processing, and resource-bank work.
- Use `/root/work-manuscript` for manuscript, textbook generation, chapter
  writing, figures, and deliverable work.
- Before editing, run `git branch --show-current` and `git status`.
- Do not have two people edit the same worktree at the same time.
- Keep branch-specific work on the matching branch unless the user explicitly
  asks to merge or sync branches.

## Data Rules

Git must stay code-only. Do not add raw materials, generated outputs, model
weights, caches, downloaded archives, or large intermediate files to Git.

Current persistent data roots:

```text
/ai/data/materials2textbook/raw
/ai/data/materials2textbook/work_material1
```

Current raw material locations:

```text
/ai/data/materials2textbook/raw/谢志怡工作整理
/ai/data/materials2textbook/raw/潘俊屹工作整理
/ai/data/materials2textbook/raw/”1+X“职业技能等级培训教材——特殊焊接技术（初级）.pdf
/ai/data/materials2textbook/raw/_incoming_baidu
```

Current working/output layout:

```text
/ai/data/materials2textbook/work_material1/01_manifest_inventory
/ai/data/materials2textbook/work_material1/02_working_processing
/ai/data/materials2textbook/work_material1/03_review_manual_check
/ai/data/materials2textbook/work_material1/04_assets_by_course
/ai/data/materials2textbook/work_material1/05_final_deliverables
```

Generated digital textbook output defaults to:

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book
```

If old local paths such as `work_materials/`, `baidu_download/`, `data/`, or
`models/` appear inside a worktree, treat them as legacy local conveniences or
mistakes. Do not rely on them as the source of truth.

## Path Configuration

The project defaults are centralized in:

```text
material_paths.py
scripts/material_paths.py
```

Default environment variables:

```text
MATERIALS2TEXTBOOK_DATA=/ai/data/materials2textbook
MATERIALS2TEXTBOOK_RAW=/ai/data/materials2textbook/raw
MATERIALS2TEXTBOOK_WORK=/ai/data/materials2textbook/work_material1
MATERIALS2TEXTBOOK_MODELS=/ai/data/models
```

Prefer these helpers and environment variables over hard-coded repo-local data
paths.

## Safe Commands

Run tests from a worktree with:

```bash
pytest
```

Common status checks:

```bash
git status
git branch --show-current
df -h / /ai/data
du -sh /ai/data/materials2textbook
```

## Cleanup Rules

Safe cleanup candidates:

```text
/tmp/*
/root/.cache/*
**/__pycache__/
.pytest_cache/
*.log
*.out
*.err
```

Do not delete these without explicit confirmation:

```text
/root/materials2textbook
/root/work-data
/root/work-manuscript
/root/anaconda3
/root/.ssh
/ai/data/materials2textbook
```

`/root/materials2textbook` still owns the Git metadata used by the worktrees.
Do not remove it unless the repository is first migrated to a bare Git layout.

## Documentation

Human handoff documentation lives in:

```text
COLLABORATOR_HANDOFF.md
docs/COLLABORATOR_HANDOFF.md
```

Keep these documents aligned when changing data paths, branch workflow, cleanup
rules, or cloud platform directory conventions.
