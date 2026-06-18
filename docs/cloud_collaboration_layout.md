# 云端协作目录与运行约定

本文记录两人在同一台云服务器上协作时的目录规划。当前结论是：代码放 `/root`，持久化数据和模型放 `/ai/data`，不再使用仓库内软链接作为主要入口。

## 总体原则

- Git/worktree 只管理代码、教材正文、配置、脚本、说明文档和小样例。
- 原始数据、中间产物、稳定资源库、模型权重和模型缓存放在仓库外。
- 数据处理者负责构建教材资源库，教材作者读取稳定版本的资源库。
- 大文件不直接进普通 Git；必要时再考虑 Git LFS、DVC、对象存储或网盘。
- Codex 和其他自动化编码助手优先读取仓库根目录的 `AGENTS.md`。

## 推荐服务器目录

```text
/root/materials2textbook      # main 分支，主仓库/管理入口
/root/work-data               # data-pipeline 分支，数据处理 worktree
/root/work-manuscript         # manuscript 分支，教材写作 worktree

/ai/data/materials2textbook   # 项目数据和教材资源库，不进 Git
/ai/data/models               # 本地大模型权重，不进 Git
/ai/data/model-cache          # HuggingFace / ModelScope / vLLM 等缓存
/ai/data/services             # 本地模型服务启动脚本和运行配置
```

## Worktree 分工

当前已经建好三个 worktree：

```text
/root/materials2textbook   main
/root/work-data            data-pipeline
/root/work-manuscript      manuscript
```

分工建议：

```text
/root/work-data:
  scripts/
  docs/
  数据导入、清洗、资源库构建相关代码

/root/work-manuscript:
  manuscript/
  resources/
  教材章节、图表、导出稿、电子教材生成
```

`/root/materials2textbook` 只作为 `main` 分支和管理入口，不建议日常开发。需要同步分支时再统一从这里处理。

## 共享数据目录

共享数据放在仓库外：

```text
/ai/data/materials2textbook
```

当前实际结构：

```text
/ai/data/materials2textbook/
├── raw/
│   ├── 谢志怡工作整理/
│   ├── 潘俊屹工作整理/
│   ├── ”1+X“职业技能等级培训教材——特殊焊接技术（初级）.pdf
│   └── _incoming_baidu/
└── work_material1/
    ├── 01_manifest_inventory/
    ├── 02_working_processing/
    ├── 03_review_manual_check/
    ├── 04_assets_by_course/
    └── 05_final_deliverables/
```

后续百度网盘下载目录保持在：

```text
/ai/data/materials2textbook/raw/_incoming_baidu
```

下载完成后，数据处理者再决定是否将其中的稳定原始素材整理到 `raw/` 的来源目录中。

不要在 worktree 里重新建立或依赖 `data`、`models`、`work_materials` 这类旧入口。项目代码应该通过集中路径配置或环境变量访问 `/ai/data`。

## 路径配置

路径配置集中在：

```text
material_paths.py
scripts/material_paths.py
```

默认值：

```text
MATERIALS2TEXTBOOK_DATA=/ai/data/materials2textbook
MATERIALS2TEXTBOOK_RAW=/ai/data/materials2textbook/raw
MATERIALS2TEXTBOOK_WORK=/ai/data/materials2textbook/work_material1
MATERIALS2TEXTBOOK_MODELS=/ai/data/models
```

常用输出目录：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables
```

电子教材默认输出：

```text
/ai/data/materials2textbook/work_material1/05_final_deliverables/digital_book
```

## 教材资源库版本

如果后续需要稳定资源库版本，可以在 `/ai/data/materials2textbook` 下新增：

```text
/ai/data/materials2textbook/textbook_bank/v0.1/
/ai/data/materials2textbook/textbook_bank/v0.2/
/ai/data/materials2textbook/textbook_bank/latest -> v0.2
```

建议：

- 正式写作尽量引用固定版本，例如 `v0.2`。
- 快速协作可以引用 `latest`，但每次更新都要在资源库说明或变更记录中写清楚。
- 原始数据放入 `raw` 后不修改；若来源更新，新增日期或版本目录。

## 本地大模型目录

模型权重和缓存放在仓库外，供两个 worktree 共用：

```text
/ai/data/models
/ai/data/model-cache
/ai/data/services
```

推荐结构：

```text
/ai/data/models/
├── qwen/
├── deepseek/
├── bge/
└── reranker/

/ai/data/model-cache/
├── huggingface/
├── modelscope/
└── vllm/

/ai/data/services/
├── llm/
└── embedding/
```

缓存环境变量建议写入 shell 配置：

```bash
export HF_HOME=/ai/data/model-cache/huggingface
export TRANSFORMERS_CACHE=/ai/data/model-cache/huggingface/transformers
export MODELSCOPE_CACHE=/ai/data/model-cache/modelscope
```

## 本地模型服务示例

如果使用 vLLM 提供 OpenAI-compatible 接口，启动脚本可以放在：

```text
/ai/data/services/llm/start-vllm-qwen.sh
```

示例：

```bash
#!/usr/bin/env bash
set -e

export HF_HOME=/ai/data/model-cache/huggingface
export CUDA_VISIBLE_DEVICES=0

python -m vllm.entrypoints.openai.api_server \
  --model /ai/data/models/qwen/Qwen2.5-7B-Instruct \
  --served-model-name local-qwen \
  --host 0.0.0.0 \
  --port 8000
```

统一访问地址：

```text
http://127.0.0.1:8000/v1
```

## 工具配置

当前服务器使用 `root` 用户时，常见全局配置目录：

```text
/root/.config/
/root/.codex/
```

项目专属示例配置可以放在仓库内，例如：

```text
opencode.local.example.json
```

不要把真实密钥、私有 token、账号 cookie 或本地大文件提交进 Git。

## Git 和提交节奏

日常开发时：

```bash
git branch --show-current
git status
```

数据处理分支：

```bash
cd /root/work-data
git pull origin data-pipeline
```

教材写作分支：

```bash
cd /root/work-manuscript
git pull origin manuscript
```

合并回 `main` 时统一处理，确认测试和大文件状态后再推送。

当前三个主要分支已经同步到：

```text
main           71fc7af
data-pipeline  71fc7af
manuscript     71fc7af
```

## 测试与验证

常规测试：

```bash
pytest
```

迁移后曾验证：

```text
136 passed, 1 warning
```

数据迁移曾验证：

```text
谢志怡工作整理：源/目标文件数和字节数一致
潘俊屹工作整理：源/目标文件数和字节数一致
work_material1：主要子目录源/目标文件数和字节数一致
```

## 清理边界

可以按需清理：

```text
/tmp/*
/root/.cache/*
**/__pycache__/
.pytest_cache/
*.log
*.out
*.err
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

`/root/materials2textbook` 仍然保存 worktree 依赖的 Git 元数据。若以后要删除主 worktree，需要先迁移成 bare Git 布局。
