# 云端协作交接说明

本文给共同使用云端开发环境的同伴阅读，说明当前仓库、数据目录、分支和日常协作方式。

## 当前结论

代码和数据已经分离：

```text
代码：/root/materials2textbook、/root/work-data、/root/work-manuscript
数据：/ai/data/materials2textbook
```

大文件和素材不再放进 Git。Git 只管理代码、脚本、文档、配置和小样例。

Codex/自动化编码助手的项目规则写在仓库根目录：

```text
/root/materials2textbook/AGENTS.md
```

以后如果调整云端目录、分支分工、数据位置或清理规则，需要同步更新 `AGENTS.md` 和本文档。

## 云平台目录

```text
/root/
├── materials2textbook/      # main 分支，主仓库/管理入口，不建议日常开发
├── work-data/               # data-pipeline 分支，数据处理日常开发
├── work-manuscript/         # manuscript 分支，教材写作日常开发
├── anaconda3/               # Python/conda 环境，不要删除
├── .ssh/                    # SSH 与 GitHub/远程访问密钥，不要删除
├── .vscode-server/          # VS Code Remote Server
├── .config/                 # 工具配置
├── .codex/                  # Codex 配置和缓存
└── 其它隐藏目录              # shell、Python、编辑器等工具配置

/ai/data/
├── materials2textbook/      # 项目持久化数据，最重要
├── models/                  # 模型权重目录
├── model-cache/             # HuggingFace / ModelScope / vLLM 等缓存
└── services/                # 本地服务脚本和运行配置
```

目录使用原则：

```text
/root                         放代码、环境、工具配置
/ai/data                      放需要持久化的大文件和项目数据
/tmp                          只放临时文件，可随时清理
/root/materials2textbook       管理入口，不做日常开发
/root/work-data                数据处理同伴日常开发
/root/work-manuscript          教材写作同伴日常开发
```

不要直接删除：

```text
/root/materials2textbook
/root/work-data
/root/work-manuscript
/root/anaconda3
/root/.ssh
/ai/data/materials2textbook
```

可以按需清理：

```text
/tmp/*
/root/.cache/*
Python __pycache__
.pytest_cache
运行日志 *.log / *.out / *.err
```

清理前先确认路径，不要对 `/ai/data/materials2textbook` 使用 `rm -rf`。

## Worktree 分工

```text
/root/materials2textbook
```

主 worktree，当前在 `main` 分支。只作为管理入口，不建议日常开发。

```text
/root/work-data
```

数据处理 worktree，当前在 `data-pipeline` 分支。适合做素材清洗、台账、批处理、资源库构建等工作。

```text
/root/work-manuscript
```

教材写作 worktree，当前在 `manuscript` 分支。适合做教材正文、电子教材生成、交付物整理等工作。

两个人不要同时在同一个 worktree 里改代码。开始工作前先确认：

```bash
git branch --show-current
git status
```

## 数据目录

原始材料：

```text
/ai/data/materials2textbook/raw
├── 谢志怡工作整理/
├── 潘俊屹工作整理/
├── ”1+X“职业技能等级培训教材——特殊焊接技术（初级）.pdf
└── _incoming_baidu/
```

加工和交付目录：

```text
/ai/data/materials2textbook/work_material1
├── 01_manifest_inventory/
├── 02_working_processing/
├── 03_review_manual_check/
├── 04_assets_by_course/
└── 05_final_deliverables/
```

后续百度网盘下载默认放到：

```text
/ai/data/materials2textbook/raw/_incoming_baidu
```

下载后再人工整理到 `raw/` 下合适的位置。不要把下载资料复制回 Git 仓库。

## 项目路径

项目脚本默认路径已经改到 `/ai/data`。路径配置集中在：

```text
material_paths.py
scripts/material_paths.py
```

默认环境变量：

```text
MATERIALS2TEXTBOOK_DATA=/ai/data/materials2textbook
MATERIALS2TEXTBOOK_RAW=/ai/data/materials2textbook/raw
MATERIALS2TEXTBOOK_WORK=/ai/data/materials2textbook/work_material1
MATERIALS2TEXTBOOK_MODELS=/ai/data/models
```

默认生成目录：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables
```

电子教材默认位置：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/index.html
```

不要继续依赖仓库内旧路径，例如：

```text
work_materials/
baidu_download/
data/
models/
```

这些只可能是旧残留或本地临时入口，真实数据以 `/ai/data` 为准。

## Git 当前状态

以下分支已经同步。查看当前提交号用：

```bash
git rev-parse --short HEAD
```

云端本地 worktree：

```text
/root/materials2textbook   main
/root/work-data            data-pipeline
/root/work-manuscript      manuscript
```

GitHub 上 `main`、`data-pipeline`、`manuscript` 也应保持同步。同步后再开始各自分支的日常工作。

## 日常工作流程

数据处理同伴建议使用：

```bash
cd /root/work-data
git status
git pull origin data-pipeline
```

完成修改后：

```bash
git status
git add <需要提交的文件>
git commit -m "说明这次做了什么"
git push origin data-pipeline
```

教材写作同伴建议使用：

```bash
cd /root/work-manuscript
git status
git pull origin manuscript
```

完成修改后：

```bash
git status
git add <需要提交的文件>
git commit -m "说明这次做了什么"
git push origin manuscript
```

需要把分支合回 `main` 时，再统一处理。不要随手在两个 worktree 里互相覆盖。

## 已完成的迁移和清理

已清理或迁移：

```text
/root/baidu_download
/root/shared-data
/root/shared-models
/root/model-cache
/root/services
临时安装包
pip/conda 缓存
运行日志
Python 测试缓存
```

当前保留的数据都在：

```text
/ai/data/materials2textbook
```

`/root/materials2textbook` 仍然保存 worktree 依赖的 Git 元数据，不要直接删除。若以后想取消主 worktree，需要先迁移成 bare Git 布局。

## 验证结果

迁移后已跑过测试：

```text
136 passed, 1 warning
```

数据迁移也已校验：

```text
谢志怡工作整理：源/目标文件数和字节数一致
潘俊屹工作整理：源/目标文件数和字节数一致
work_material1：主要子目录源/目标文件数和字节数一致
```

## 最容易踩的坑

1. 不要在 `/root/materials2textbook` 日常开发。
2. 不要把 `/ai/data` 里的数据复制回 Git 仓库。
3. 生成结果默认写到 `/ai/data/materials2textbook/work_material1/05_final_deliverables`。
4. 提交前先 `git status`，确认没有大文件。
5. 如果看到 `work_materials/` 出现在 `git status` 里，先停下来检查。
6. 改云端目录、数据路径、清理规则时，同步更新 `AGENTS.md`。
