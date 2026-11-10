# 模型权重说明

本目录**不纳入 Git 版本管理**(约 6.3GB), 由 `.gitignore` 排除。
首次拉取代码后需自行下载下列模型, 目录名必须与代码中的引用保持一致。

| 目录 | 用途 | 来源 |
| --- | --- | --- |
| `bert-base-chinese` | 中文预训练底座 | https://huggingface.co/bert-base-chinese |
| `bert_query_classifier` | 查询分类(微调产物) | 本地训练产出, 需自行备份 |
| `bge-m3` | 向量化 embedding | https://huggingface.co/BAAI/bge-m3 |
| `bge-reranker-large` | 检索结果重排 | https://huggingface.co/BAAI/bge-reranker-large |
| `nlp_bert_document-segmentation_chinese-base` | 文档语义分段 | https://modelscope.cn/models/nlp_bert_document-segmentation_chinese-base |

## 下载示例

```bash
# HuggingFace 模型(需先配置镜像或代理)
huggingface-cli download BAAI/bge-m3 --local-dir rag_qa/models/bge-m3
huggingface-cli download BAAI/bge-reranker-large --local-dir rag_qa/models/bge-reranker-large

# ModelScope 模型
from modelscope import snapshot_download
snapshot_download('nlp_bert_document-segmentation_chinese-base',
                  cache_dir='rag_qa/models', revision='v1.0.1')
```

## 说明

- 模型根目录由 `base/config.py` 中的 `MODELS_DIR` 计算, 默认即本目录。
- `bert_query_classifier` 属于项目自训练产物, 建议在模型平台或对象存储另行备份, 不要只存在于本机。
