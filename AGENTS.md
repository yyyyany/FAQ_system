# AGENTS.md — 集成问答系统 · 协作约定与项目地图

> 本文件是 AI 助手的长期上下文入口：接手任何任务前先读它，结束任务前更新末尾的「进度账本」。
> 内容以**实测**为准；与 README 冲突时，先按本文件执行，并把差异记录下来。

---

## 1. 项目是什么

一个「**MySQL 精确检索 + RAG 语义检索**」双通道 IT 教育问答系统。

- 用户提问 → 先查 MySQL `jpkb` 问答库做 BM25 → 分数够高直接返回（快、准、零成本）
- BM25 不命中 → 走 RAG（向量混合召回 → 重排 → 大模型流式生成）
- 支持多轮会话（MySQL `conversations` 表，保留最近 5 轮）、WebSocket 流式输出、Redis 缓存

一句话：**用最便宜的检索挡住大部分问题，只把真正不会的问题交给大模型。**

## 2. 运行环境（硬约束）

| 项 | 值 |
| --- | --- |
| 解释器 | `D:\Program\Anaconda3\envs\edu_rag\python.exe`（Python 3.10.18） |
| 工作目录 | 项目根目录（多处相对路径：`static/`、`logs/`、`rag_qa/data`） |

**必须记住的坑**：
- 终端里裸敲 `pip` / `python` 可能指向 Anaconda **base** 环境，不是 `edu_rag`。做版本核对或装包时**一律写全路径**。
- 所有命令都在**项目根目录**执行，否则 `static/index.html`、`logs/`、相对导入都会失败。

## 3. 常用命令

```powershell
# 依赖（已装好，一般不需要重装）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" -m pip install -r requirements.txt

# 启动 Web 服务（http://localhost:8080）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" app.py

# 命令行交互测试（不带 Web，直接跑核心链路）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" new_main.py

# 灌 RAG 知识库（写 Milvus）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" -m rag_qa.main --data-processing --data-dir ./rag_qa/data

# 只跑 RAG 通道的命令行问答
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" -m rag_qa.main

# MySQL 建表 + 导入问答 CSV（仅首次；重复执行会重复插入）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" -c "from mysql_qa.db.mysql_client import MySQLClient; c=MySQLClient(); c.create_table(); c.insert_data('mysql_qa/data/JP学科知识问答.csv'); c.close()"
```

> **`rag_qa` 必须用 `-m` 方式运行**：`python -m rag_qa.main`。直接 `python rag_qa/main.py` 会因包相对导入失败。
> Windows 控制台中文乱码时先执行 `chcp 65001`。

## 4. 外部服务依赖

| 服务 | 地址 | 前置条件 | 缺失时的报错 |
| --- | --- | --- | --- |
| MySQL | `localhost:3306` | 需先建库 `subjects_kg` | `Unknown database 'subjects_kg'` |
| Redis | `localhost:6379` | 密码与 `config.ini` 一致（本地无密码则留空） | Redis 认证失败 |
| Milvus | `localhost:19530` | 需先建库 `itcast` | `database not found: itcast` |

```powershell
# Milvus 首次建库（只执行一次）
& "D:\Program\Anaconda3\envs\edu_rag\python.exe" -c "from pymilvus import MilvusClient; MilvusClient(uri='http://localhost:19530').create_database('itcast')"
```

**环境快照（2026-11 实测，复核后更新）**：依赖全部就位；`dashscope` 包未安装但**不需要**（LLM 走 `openai` SDK 兼容模式）；MySQL **UP**，Redis **DOWN**，Milvus **DOWN**。

## 5. 代码地图

```
app.py                     Web 层：FastAPI + WebSocket + 静态页 + 问候语拦截 + 会话接口
new_main.py                编排层：IntegratedQASystem —— 双通道路由、多轮历史、流式协议（当前生效）
old_main.py                历史版本，仅供对照，不要改、不要引用
base/config.py             配置单例 config：环境变量 > config.ini > 代码 fallback
base/logger.py             日志单例 logger：控制台 + logs/app.log
mysql_qa/
  db/mysql_client.py       MySQLClient：连接、建表、CSV 导入、会话历史读写的底层
  retrieval/bm25_search.py BM25Search：分词 → BM25 打分 → softmax 归一化 → 阈值判定
  cache/redis_client.py    RedisClient：分词结果与问答结果缓存
  utils/preprocess.py      中文分词与文本清洗
rag_qa/
  core/vector_store.py     VectorStore：BGE-M3 稠密+稀疏、Milvus hybrid_search、父子块
  core/new_rag_system.py   RAGSystem（当前生效）：检索 → 重排 → 拼 prompt → 流式生成
  core/rag_system.py       RAGSystem V1（旧），对照用
  core/document_processor.py 解析 → 切分 → 灌库
  core/query_classifier.py BERT 查询分类（本地模型 bert_query_classifier）
  core/strategy_selector.py 依据问题类型选检索/生成策略
  core/prompts.py          所有提示词模板集中地
  edu_document_loaders/    PDF/DOCX/PPT/图片解析器
  edu_text_spliter/        中文递归切分 + 语义分段
  rag_assessment/rag_as.py RAG 效果评估脚本
static/index.html          前端单页，直连后端，无需构建
```

**依赖方向（只能单向）**：`base` ← `mysql_qa` / `rag_qa` ← `new_main` ← `app`。禁止反向引用，禁止 `mysql_qa` 与 `rag_qa` 互相 import。

## 6. 一次提问的真实链路

```
客户端
  ├─ POST /api/query        非流式：只处理问候语 + BM25 命中，未命中返回 is_streaming=true
  └─ WS   /api/stream       流式：完整链路
        ↓
app.py  check_greeting()           正则拦截「你好/你是谁/在吗/干嘛呢」→ 直接回模板
        ↓
new_main.IntegratedQASystem.query()
        ↓  取最近 5 轮历史（session_id 存在时）
        ↓  bm25_search.search(query, threshold=0.85)
        ├─ 命中（answer 非空）  → yield (answer, True)            一次吐完
        ├─ 未命中但 need_rag    → rag_system.generate_answer()     逐 token yield (token, False)
        └─ 都不满足             → yield ("未找到答案", True)
        ↓  回答完成后 update_session_history() 写回 MySQL（保留最近 5 轮）
```

**RAG 内部**：`query_classifier` 判类型 → `strategy_selector` 选策略 → `vector_store` 混合检索（BGE-M3 dense + sparse，`WeightedRanker`）→ `bge-reranker-large` 精排 → 子块命中后补**父块**上文 → `prompts.py` 拼上下文与历史 → LLM 流式生成。

**流式协议约定**（约定死的三元组 `(token, is_complete)`）：
- `(内容, False)` = 中间片段
- `(任意, True)` = 结束，最后一个 token 可能是空串
- 改 `query()` 的返回值格式会同时打断 `app.py`、`new_main.py` 的 CLI，**动之前先全量搜引用**。

## 7. 配置优先级

`环境变量` > `config.ini` > 代码 fallback（见 `base/config.py`）。

- `config.ini` **含真实密钥，永不入库**；模板是 `config.example.ini`。
- 仅 `config.ini` 可改的项：`LLM_MODEL`、`DASHSCOPE_BASE_URL`、`[retrieval]` 全部参数、`valid_sources`、`customer_service_phone`。
- `[retrieval]` 关键参数含义：`parent_chunk_size=1200`（父块）、`child_chunk_size=300`（子块，召回粒度）、`retrieval_k`（召回数）、`candidate_m`（重排后保留数）。

## 8. 禁止事项

1. 不提交 `config.ini`、`rag_qa/models/`、`logs/`、`.idea/`、`__pycache__/`；**永不用 `git add -f`** 绕过。
2. 不把密钥、token 写进代码或文档；新增配置一律走 `config.example.ini` + 环境变量。
3. 不删除 `old_main.py`、`rag_system.py`、`review.py` 等对照文件（教学参照物）。
4. 不改 `requirements.txt` 里的既有版本，除非确认 `edu_rag` 环境实测通过。
5. 不擅自 `git commit` / `push` / 改分支；提交前先 `git diff --cached --stat` 自查。
6. 不把大模型调用改成同步阻塞（会破坏流式）；不在请求路径上做全量重载模型。
7. 不为了"跑通"而注释掉异常处理或硬编码 fallback 值。

## 9. 已知坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动卡 1~2 分钟 | 正在加载 BGE-M3 + Reranker | 正常，等 |
| 检索结果为空 | 未灌库，或 `source_filter` 与文档 `source` 不匹配 | 先灌库，检查 `valid_sources` |
| 重复插入问答数据 | `create_table()`/`insert_data()` 幂等性弱 | 重导前先清表 |
| `ModuleNotFoundError: base` | 不在项目根目录运行 | 切回根目录 |
| Redis 连不上导致启动失败 | 本地 Redis 无密码但 `config.ini` 写了密码 | 把 `[redis] password` 留空 |

## 10. 教学式设计痕迹（本项目是培训机构教学项目）

本项目按“能讲清楚”而非“能维护好”设计。识别特征（**不要当成 bug 去改，也不要照抄进生产**）：

| 痕迹 | 位置 | 生产项目做法 |
| --- | --- | --- |
| `todo 1` / `2.1` / `2.8` 步骤编号注释 | 几乎所有文件 | 不写编号，靠函数名自解释 |
| 同一功能并存两版 | `old_main.py` / `new_main.py`、`rag_system.py` / `new_rag_system.py` | 交给版本控制，不并存 |
| `review.py` 复习文件 | `edu_document_loaders/`、`edu_text_spliter/` | 不保留 |
| `edu_` 前缀 | `edu_*.py` | 目录已表达层级，无需前缀 |
| 大段注释掉的旧实现 | `base/config.py` 约 30-50 行 | 直接删除 |
| 命名风格不统一 | `REDIS_PORT` vs `MILVUS_HOST`/`MYSQL_HOST` | 统一 |
| 密钥 fallback 兜底 `记得改成你的密钥` | `base/config.py` 约 107 行 | 缺失即报错，不兜底 |
| 阈值硬编码 `threshold=0.85` | `app.py` 与 `new_main.py` 两处 | 提到配置 |

**改造候选（按收益排序，动手前确认不破坏演示价值）**：
1. `eval()` → `json.loads()`（`VALID_SOURCES`）
2. `LLM_MODEL` / `DASHSCOPE_BASE_URL` / `[retrieval]` 补齐 `os.getenv`
3. `threshold=0.85` 收敛为配置项
4. 会话历史读写抽成 DAO，统一事务边界
5. 补单元测试（`requirements-dev.txt` 已有 pytest）

> 改动原则：**先写清“教学版为什么这么写”，再说“工程版该怎么做”**，避免把学习材料改到看不懂。

## 11. 进度账本（速通主线）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 环境与依赖核对 | ✅ 已完成（依赖齐全，MySQL UP / Redis DOWN / Milvus DOWN） |
| 1 | 配置体系（`config.ini` / 环境变量 / `base/config.py`） | ✅ 已完成 |
| 2 | 外部服务拉起（MySQL / Redis / Milvus） | 🟡 方案已定：本地 Docker；待启动 Docker Desktop |
| 3 | 数据层（建库建表、CSV 导入、会话表） | ⬜ |
| 4 | MySQL 通道（分词 → BM25 → 阈值 → 缓存） | ⬜ |
| 5 | RAG 通道（解析 → 切分 → 向量化 → 灌库 → 检索 → 重排 → 生成） | ⬜ |
| 6 | 编排层（`query()` 三分支 + 多轮 + 流式协议） | ⬜ |
| 7 | Web 层（路由 / WebSocket / 前端对接） | ⬜ |
| 8 | 评估与调优（`rag_assessment`、参数实验） | ⬜ |

> 每完成一个阶段：更新上表状态 → 在本节下方追加一行「阶段 N 结论」，记录**实测到的行为**而非文档说法。

### 阶段结论

- **阶段 0**：依赖齐全；`dashscope` 包不需要（走 `openai` SDK 兼容模式）；MySQL UP，Redis / Milvus DOWN 且本机无安装、无进程；Docker CLI 已装但引擎未启动。`logs/app.log` 显示 2026-06-27 曾端到端跑通，当时环境可能是远程 `up01`。
- **阶段 1**：`base/config.py` 三层降级实测有效（`MYSQL_DATABASE=probe_db` 覆盖成功）；`LLM_MODEL` 无法用环境变量覆盖；`config.ini` 与 `config.example.ini` 键集完全一致；`VALID_SOURCES` 经 `eval` 解析为 list。

### 协作方式（约定）

- 用户以提问方式驱动，边讲边跑；阶段之间会停下讨论。
- 目标是**从原理设计理顺到可运行实现**，并预期对教学版代码做改造与优化。
