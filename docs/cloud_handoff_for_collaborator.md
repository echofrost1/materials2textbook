# 云端协作交接说明

本文给共同使用云端开发环境的同伴阅读，说明当前仓库、数据目录、分支和提交方式。

## 当前结论

代码和数据已经分离：

```text
代码：/root/materials2textbook、/root/work-data、/root/work-manuscript
数据：/ai/data/materials2textbook
```

大文件和素材不再放进 Git。Git 只管理代码、脚本、文档、配置和小样例。

## 目录现状

### 云平台总览

当前云平台主要目录如下：

```text
/root/
├── materials2textbook/      # 主 worktree，当前 main 分支，只建议做管理入口
├── work-data/               # 数据处理 worktree，当前 data-pipeline 分支
├── work-manuscript/         # 教材写作 worktree，当前 manuscript 分支
├── anaconda3/               # Python/conda 环境，不要删除
├── .ssh/                    # SSH 与 GitHub/远程访问密钥，不要删除
├── .vscode-server/          # VS Code Remote Server，不建议删除
├── .cache/                  # 普通缓存，已清理过，后续可按需清
├── .config/                 # 工具配置
├── .codex/                  # Codex 配置和缓存
└── 其它隐藏目录              # shell、Python、编辑器等工具配置

/ai/data/
├── materials2textbook/      # 项目持久化数据，最重要
├── models/                  # 预留模型权重目录
├── model-cache/             # 预留模型缓存目录
└── services/                # 预留本地服务脚本目录
```

当前大致大小：

```text
/root/materials2textbook       9.2G
/root/work-data                1.6M
/root/work-manuscript          1.6M
/root/anaconda3                18G
/root/.cache                   15M
/ai/data/materials2textbook    119G
/ai/data/models                0
/ai/data/model-cache           0
/ai/data/services              512B
```

目录使用原则：

```text
/root                         放代码、环境、工具配置
/ai/data                      放需要持久化的大文件和项目数据
/tmp                          只放临时文件，可随时清理
/root/materials2textbook       不做日常开发
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

但清理前最好先确认路径，不要对 `/ai/data/materials2textbook` 使用 `rm -rf`。

### Git worktree 目录

```text
/root/materials2textbook
```

主 worktree，目前在 `main` 分支。只作为管理入口，不建议日常开发。

```text
/root/work-data
```

数据处理 worktree，目前在 `data-pipeline` 分支。适合做素材清洗、台账、批处理、资源库构建等工作。

```text
/root/work-manuscript
```

教材写作 worktree，目前在 `manuscript` 分支。适合做教材正文、电子教材生成、交付物整理等工作。

```text
/ai/data/materials2textbook
```

持久化数据盘。原始材料、加工结果、最终交付物都放这里。

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

下载后再人工整理到 `raw/` 下合适的位置。

## 路径改动

项目脚本默认路径已经改到 `/ai/data`。

默认素材和加工目录：

```text
/ai/data/materials2textbook/work_material1
```

默认原始材料目录：

```text
/ai/data/materials2textbook/raw
```

默认生成目录：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables
```

例如电子教材默认位置：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book/index.html
```

## Git 当前状态

以下分支已经合并同步到同一个提交：

```text
main                                  36510f5
feature/whole-book-after-main-merge-output 36510f5
data-pipeline                         36510f5
manuscript                            36510f5
```

云端本地 worktree：

```text
/root/materials2textbook   main           36510f5
/root/work-data            data-pipeline  36510f5
/root/work-manuscript      manuscript     36510f5
```

GitHub 也已经推送到以上提交。

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

需要把分支合回 `main` 时，再统一处理，不要随手在两个 worktree 里互相覆盖。

## 重要约定

不要把大文件放进 Git。

不要再创建或提交：

```text
work_materials/
baidu_download/
models/
model-cache/
download_logs/
outputs/
```

这些内容应该放到：

```text
/ai/data/materials2textbook
/ai/data/models
/ai/data/model-cache
```

不要两个人同时在同一个 worktree 里改代码。

推荐分工：

```text
/root/work-data        数据处理
/root/work-manuscript  教材写作
```

`/root/materials2textbook` 只作为管理入口，不建议日常开发。

## 已完成的清理

已清理：

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

## 验证结果

清理和迁移后已跑过测试：

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
6. 如果要改提交作者，先确认自己在哪个 worktree。
