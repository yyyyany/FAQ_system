# 集成问答系统 (MySQL + RAG 双通道)

基于 **FastAPI + Milvus + MySQL + Redis** 的智能问答系统。用户提问后先在 MySQL 问答库做 BM25 精确匹配,
命中则直接返回; 未命中再走 RAG 语义检索(混合向量召回 + 重排 + 大模型生成), 并支持多轮上下文与 WebSocket 流式输出。

## 功能特性

- **双通道检索**: MySQL 问答库 BM25(softmax 归一化 + 阈值 0.85) → 未命中转 RAG
- **混合检索**: BGE-M3 稠密 + 稀疏向量, Milvus `hybrid_search` + `WeightedRanker`, BGE-Reranker 精排
- **父子块切分**: 子块召回、父块补全上下文, 兼顾精度与完整性
- **多轮对话**: MySQL `conversations` 表保存会话历史(保留最近 5 轮)
- **流式输出**: WebSocket 逐 token 推送, 前端打字机效果
- **缓存加速**: Redis 缓存问题分词结果与问答结果

## 目录结构

```
integrated_qa_system/
├── app.py                     # FastAPI 服务入口 (HTTP + WebSocket + 静态资源)
├── new_main.py                # 集成问答核心 (BM25 → RAG 编排、会话历史、流式生成)
├── old_main.py                # 历史版本, 仅作参考
├── config.ini                 # 本地配置(含密钥, 不入库)
├── config.example.ini         # 配置模板(入库)
├── base/                      # 配置解析与日志封装
├── mysql_qa/                  # MySQL 通道: 客户端 / BM25 / Redis 缓存 / 分词
├── rag_qa/                    # RAG 通道
│   ├── core/                  # 向量库、文档处理、RAG 编排、提示词
│   ├── edu_document_loaders/  # PDF/Word/PPT/图片 解析
│   ├── edu_text_spliter/      # 中文语义切分
│   ├── models/                # 本地模型权重(不入库)
│   ├── data/ai_data/          # 语料
│   └── rag_assessment/        # 效果评估脚本
└── static/                    # 前端页面(index.html 直连后端, 无需构建)
```

## 一、环境准备

### 1. Python 环境

- Python **3.10**(推荐 conda 独立环境)
- 内存: 加载 BGE-M3 与 Reranker 建议 **8GB 以上**; GPU 可选, 有则自动使用

```bash
conda create -n edu_rag python=3.10 -y
conda activate edu_rag

pip install -r requirements.txt          # 运行依赖
pip install -r requirements-dev.txt      # 如需开发/测试
```

> `torch` 默认安装 CPU 版本; 需要 GPU 加速时先按 [PyTorch 官网](https://pytorch.org/get-started/locally/) 命令装好对应 CUDA 版本, 再装其余依赖。

### 2. 外部服务

| 服务 | 默认地址 | 要求 |
| --- | --- | --- |
| MySQL | `localhost:3306` | 需先创建库 `subjects_kg`(utf8mb4) |
| Redis | `localhost:6379` | 无密码或与配置一致 |
| Milvus | `localhost:19530` | Standalone 即可, 需先创建库 `itcast` |

```sql
-- MySQL: 建库
CREATE DATABASE IF NOT EXISTS subjects_kg DEFAULT CHARSET utf8mb4;
```

```bash
# Milvus: 建库(仅在首次部署时执行一次)
python -c "from pymilvus import MilvusClient; MilvusClient(uri='http://localhost:19530').create_database('itcast')"
```

### 3. 配置

```bash
# Windows
copy config.example.ini config.ini
# Linux / macOS
cp config.example.ini config.ini
```

然后编辑 `config.ini` 填写真实的数据库密码与 `dashscope_api_key`。

> `config.ini` 已被 `.gitignore` 排除, 不要提交。`base/config.py` 会**优先读取同名环境变量**,
> 因此 CI / 服务器上可以不落盘密钥, 直接注入环境变量。

### 4. 环境变量

支持环境变量覆盖的配置项(优先级高于 `config.ini`):

| 变量 | 说明 |
| --- | --- |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL 连接 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | Redis 连接 |
| `MILVUS_HOST` / `MILVUS_PORT` | Milvus 连接 |
| `MILVUS_DATABASE_NAME` / `MILVUS_COLLECTION_NAME` | Milvus 库名 / 集合名 |
| `DASHSCOPE_API_KEY` | 大模型 API Key |
| `HOST` / `PORT` | 服务监听地址, 默认 `0.0.0.0:8080` |

PowerShell 示例:

```powershell
$env:DASHSCOPE_API_KEY = "sk-xxxx"
$env:MYSQL_PASSWORD = "your_password"
$env:PORT = "8080"
```

> `LLM_MODEL`、`DASHSCOPE_BASE_URL`、检索参数(`parent_chunk_size` 等)、`valid_sources` 目前只能通过 `config.ini` 修改。

### 5. 模型权重下载

模型体积约 6.3GB, 不入库, 需自行下载到 `rag_qa/models/`, 目录名必须严格一致:

```bash
# HuggingFace(国内可先设置 HF_ENDPOINT=https://hf-mirror.com)
huggingface-cli download BAAI/bge-m3 --local-dir rag_qa/models/bge-m3
huggingface-cli download BAAI/bge-reranker-large --local-dir rag_qa/models/bge-reranker-large
huggingface-cli download google-bert/bert-base-chinese --local-dir rag_qa/models/bert-base-chinese
```

```python
# ModelScope: 语义分段模型
from modelscope import snapshot_download
snapshot_download('nlp_bert_document-segmentation_chinese-base', cache_dir='rag_qa/models')
```

| 目录 | 用途 |
| --- | --- |
| `bge-m3` | 稠密 + 稀疏向量生成 |
| `bge-reranker-large` | 检索结果重排 |
| `bert-base-chinese` | 中文底座 |
| `bert_query_classifier` | 查询分类(自训练产物, 需另行备份) |
| `nlp_bert_document-segmentation_chinese-base` | 文档语义分段 |

详见 [rag_qa/models/README.md](rag_qa/models/README.md)。

## 二、数据初始化

### 1. MySQL 问答库

建表 `jpkb` 并导入 `mysql_qa/data/JP学科知识问答.csv`(仅首次执行, 重复执行会追加数据):

```bash
python -c "from mysql_qa.db.mysql_client import MySQLClient; c=MySQLClient(); c.create_table(); c.insert_data('mysql_qa/data/JP学科知识问答.csv'); c.close()"
```

> 会话历史表 `conversations` 由系统启动时自动创建, 无需手工建表。

### 2. RAG 知识库灌库

解析 `rag_qa/data/ai_data/` 下文档并写入 Milvus(集合不存在时自动创建):

```bash
python -m rag_qa.main --data-processing --data-dir ./rag_qa/data
```

> 必须用 `python -m rag_qa.main`, 直接 `python rag_qa/main.py` 会因包导入路径问题失败。
> 需先启动 Milvus; 灌库完成后可用 `python -m rag_qa.main` 进入命令行交互测试。

## 三、启动服务

```bash
# 方式一
python app.py

# 方式二(推荐用于生产)
uvicorn app:app --host 0.0.0.0 --port 8080
```

首次启动需加载 embedding 与 reranker 模型, CPU 环境约 1~2 分钟, 之后进入可服务状态。

- 前端页面: <http://localhost:8080/>
- 健康检查: <http://localhost:8080/health>
- 接口文档: <http://localhost:8080/docs>

## 四、接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 前端页面 |
| GET | `/health` | 健康探针 |
| GET | `/api/sources` | 支持的学科类别 |
| POST | `/api/create_session` | 创建会话 ID |
| POST | `/api/query` | 非流式问答(仅命中 MySQL 时返回答案) |
| WS | `/api/stream` | 流式问答(自动 BM25 → RAG) |
| GET | `/api/history/{session_id}` | 查询会话历史 |
| DELETE | `/api/history/{session_id}` | 清除会话历史 |

`POST /api/query` 返回 `is_streaming: true` 表示该问题需要走 RAG, 请改用 WebSocket 接口。

## 五、常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `database not found: itcast` | Milvus 未创建目标库, 执行上文 `create_database('itcast')` |
| `Unknown database 'subjects_kg'` | MySQL 未建库, 执行建库 SQL |
| `Can't connect to MySQL server` | 服务未启动, 或 `config.ini` 主机/密码不符 |
| Redis 认证失败 | 本地 Redis 无密码时, 将 `[redis] password` 留空 |
| 启动卡住数分钟 | 正在加载 BGE-M3 / Reranker, 属正常现象; 磁盘慢时更久 |
| 检索结果为空 | 未灌库, 或 `source_filter` 与文档 `source` 不匹配 |
| 中文乱码(Windows 控制台) | 执行 `chcp 65001` 或使用支持 UTF-8 的终端 |

## 六、版本管理约定

- `config.ini`、`rag_qa/models/`、`logs/`、`.idea/`、`__pycache__` 均已忽略, 切勿用 `git add -f` 强制提交
- 提交前确认无大文件混入: `git diff --cached --stat`
- 分支约定: `main` 保持可运行, 功能开发走 `feat/xxx`, 里程碑打 tag
- 运行依赖变更后同步更新 `requirements.txt`, 需要完全一致的环境时额外生成 `requirements.lock.txt`
