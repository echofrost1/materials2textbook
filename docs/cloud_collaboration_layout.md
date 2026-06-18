# 云端协作目录与运行约定

> 本文件已按当前云平台持久化目录更新：实体数据统一放 `/ai/data`，不再使用软链接方案。

本文记录两人在同一台云服务器上协作时的目录规划。目标是：代码和教材可以分支协作，原始数据、中间产物、教材资源库和本地大模型只保留一份共享副本，避免重复占用磁盘和互相覆盖。

## 总体原则

- Git/worktree 只管理代码、教材正文、配置、脚本、说明文档和小样例。
- 原始数据、中间产物、稳定资源库和模型权重放在仓库外的共享目录。
- 数据处理者负责构建教材资源库，教材作者只读取稳定版本的资源库。
- 大文件不直接进普通 Git；必要时再考虑 Git LFS、DVC、对象存储或网盘。

## 推荐服务器目录

```text
/root/materials2textbook    # 主仓库，可作为 main 或管理入口
/root/work-data             # 数据处理 worktree
/root/work-manuscript       # 教材写作 worktree

/ai/data/materials2textbook           # 两边共用的数据和教材资源库，不进 git
/ai/data/models         # 本地大模型权重，不进 git
/ai/data/model-cache           # HuggingFace / ModelScope / vLLM 等缓存
/ai/data/services              # 本地模型服务启动脚本和运行配置
```

## Worktree 分工

同一个 Git 仓库可以开两个 worktree：

```bash
cd /root/materials2textbook

git switch -c data-pipeline
git switch main
git switch -c manuscript
git switch main

git worktree add ../work-data data-pipeline
git worktree add ../work-manuscript manuscript
```

分工建议：

```text
/root/work-data:
  scripts/
  docs/
  数据导入、清洗、资源库构建相关代码

/root/work-manuscript:
  manuscript/
  resources/figures/
  教材章节、图表、导出稿
```

数据处理者在 `work-data` 提交资源库构建逻辑；教材作者在 `work-manuscript` 提交正文和教材侧资源。资源库稳定后，可以通过合并分支、发布版本目录，或更新共享目录中的 `latest` 链接来交付。

## 共享数据目录

共享数据放在仓库外：

```bash
mkdir -p /ai/data/materials2textbook/{raw,external,interim,processed,textbook_bank}
```

推荐结构：

```text
/ai/data/materials2textbook/
├── raw/             # 原始数据，只读，不手工改，不覆盖
├── external/        # 外部下载资料、参考数据
├── interim/         # 清洗、切分、转换中的中间产物，可反复生成
├── processed/       # 已确认可复用的数据结果
└── textbook_bank/   # 给教材写作使用的稳定资源库
```

当前百度网盘下载目录保持在：

```text
/ai/data/materials2textbook/raw/_incoming_baidu
```

因为该目录可能仍在下载中，不移动、不重命名。共享数据目录中只建立软链接：

```text
/ai/data/materials2textbook/raw/baidu_download -> /ai/data/materials2textbook/raw/_incoming_baidu
```

下载完成后，数据处理者再决定是否将其中的稳定原始素材整理到 `raw/` 的日期或来源版本目录中。

两个 worktree 中都用软链接指向同一份数据：

```bash
cd /root/work-data
ln -s /ai/data/materials2textbook data

cd /root/work-manuscript
ln -s /ai/data/materials2textbook data
```

这样两边都能访问：

```text
data/raw
data/interim
data/processed
data/textbook_bank
```

但实际数据只有一份，位于：

```text
/ai/data/materials2textbook
```

## 教材资源库版本

教材作者不应直接依赖 `interim` 中间产物，而应读取稳定资源库：

```text
/ai/data/materials2textbook/textbook_bank/v0.1/
/ai/data/materials2textbook/textbook_bank/v0.2/
/ai/data/materials2textbook/textbook_bank/latest -> v0.2
```

更新 `latest`：

```bash
cd /ai/data/materials2textbook/textbook_bank
ln -sfn v0.2 latest
```

建议：

- 正式写作尽量引用固定版本，例如 `v0.2`。
- 快速协作可以引用 `latest`，但每次更新都要在 `docs/changelog.md` 或资源库说明中记录。
- 原始数据放入 `raw` 后不修改；若来源更新，新增日期或版本目录。

## 本地大模型目录

模型权重也放在仓库外，供两个 worktree 共用：

```bash
mkdir -p /ai/data/models
mkdir -p /ai/data/model-cache/{huggingface,modelscope,vllm}
mkdir -p /ai/data/services/{llm,embedding}
```

推荐结构：

```text
/ai/data/models/
├── qwen/
│   └── Qwen2.5-7B-Instruct/
├── deepseek/
│   └── DeepSeek-R1-Distill-Qwen-7B/
├── bge/
│   └── bge-large-zh-v1.5/
└── reranker/
    └── bge-reranker-large/

/ai/data/model-cache/
├── huggingface/
├── modelscope/
└── vllm/

/ai/data/services/
├── llm/
│   ├── start-vllm-qwen.sh
│   └── openai-compatible.env
└── embedding/
    └── start-embedding.sh
```

可选：在两个 worktree 中软链接模型目录：

```bash
cd /root/work-data
ln -s /ai/data/models models

cd /root/work-manuscript
ln -s /ai/data/models models
```

## 模型缓存环境变量

HuggingFace 缓存：

```bash
echo 'export HF_HOME=/ai/data/model-cache/huggingface' >> ~/.bashrc
echo 'export TRANSFORMERS_CACHE=/ai/data/model-cache/huggingface/transformers' >> ~/.bashrc
source ~/.bashrc
```

ModelScope 缓存：

```bash
echo 'export MODELSCOPE_CACHE=/ai/data/model-cache/modelscope' >> ~/.bashrc
source ~/.bashrc
```

## 本地模型服务示例

如果使用 vLLM 提供 OpenAI-compatible 接口，启动脚本可以放在：

```text
/ai/data/services/llm/start-vllm-qwen.sh
```

示例：

```bash
cat > /ai/data/services/llm/start-vllm-qwen.sh <<'EOF'
#!/usr/bin/env bash
set -e

export HF_HOME=/ai/data/model-cache/huggingface
export CUDA_VISIBLE_DEVICES=0

python -m vllm.entrypoints.openai.api_server \
  --model /ai/data/models/qwen/Qwen2.5-7B-Instruct \
  --served-model-name local-qwen \
  --host 0.0.0.0 \
  --port 8000
EOF

chmod +x /ai/data/services/llm/start-vllm-qwen.sh
```

启动：

```bash
/ai/data/services/llm/start-vllm-qwen.sh
```

统一访问地址：

```text
http://127.0.0.1:8000/v1
```

模型名：

```text
local-qwen
```

## opencode 配置位置

当前服务器用 `root` 用户时，全局配置目录是：

```text
/root/.config/opencode/
```

全局配置文件：

```text
/root/.config/opencode/opencode.json
```

项目专属配置可以放在 worktree 根目录：

```text
/root/materials2textbook/opencode.json
/root/work-data/opencode.json
/root/work-manuscript/opencode.json
```

学校 OpenAI-compatible 接口示例：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ecnu/ecnu-plus",
  "provider": {
    "ecnu": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "ECNU",
      "options": {
        "baseURL": "https://chat.ecnu.edu.cn/open/api/v1",
        "apiKey": "{env:ECNU_API_KEY}"
      },
      "models": {
        "ecnu-plus": {
          "name": "ecnu-plus"
        }
      }
    }
  },
  "autoupdate": false
}
```

如果学校接口要求 `access_token` 而不是标准 `Authorization: Bearer`，需要先用 `curl` 确认 token 传递方式；必要时再加一个本地代理，把 OpenAI-compatible 请求转换成学校接口需要的鉴权格式。

## .gitignore 建议

在仓库中忽略共享数据、模型和大文件：

```gitignore
data/
models/
logs/

*.zip
*.tar
*.tar.gz
*.7z

*.csv
*.xlsx
*.parquet
*.jsonl

*.safetensors
*.bin
*.gguf
*.pt
*.pth
*.onnx
```

如果某些小型样例数据需要进入 Git，建议单独放：

```text
examples/data/
tests/fixtures/
```

## 推荐协作流程

1. 数据处理者在 `/root/work-data` 写脚本和流程。
2. 原始数据放入 `/ai/data/materials2textbook/raw`，不修改、不覆盖。
3. 中间产物输出到 `/ai/data/materials2textbook/interim`。
4. 稳定结果输出到 `/ai/data/materials2textbook/processed`。
5. 教材资源库发布到 `/ai/data/materials2textbook/textbook_bank/vX.Y`。
6. 更新 `/ai/data/materials2textbook/textbook_bank/latest` 指向最新稳定版本。
7. 教材作者在 `/root/work-manuscript` 读取 `data/textbook_bank/latest` 或固定版本。
8. 代码、教材正文、配置和说明提交到 Git；大数据、模型权重和缓存不提交。
