# 调研报告 1:小蜗问答链路架构 × 同类校园 AI 助手对比

> 日期:2026-08-29 · 依据:本地代码实测 + GitHub 开源项目调研(README 缓存于 `research/`)

## 一、小蜗当前链路架构(代码实测)

```
用户 → Web(FastAPI+React,SSE 流式) / Streamlit 旧版
        │  CAS 认证 / demo 身份;隐私规则:个人数据问题强制 local 模式
        ▼
┌─ QA 核心 LangGraph(≤4 轮)──────────────────────────┐
│ ① embedding_parse  意图分类(15 类,示例句 embedding      │
│                    相似度,失败降级关键词)+ 候选召回      │
│                    (ChromaDB 向量 + BM25 混合,top12)   │
│ ② think            确定性路由优先(意图+关键词双条件)     │
│                    → LLM 决策(JSON: clarify/retrieve/  │
│                      call_tool/compose)                │
│                    → LLM 失败降级确定性规则             │
│ ③ act              执行工具/重检索;参数兜底             │
│                    (原始问题确定性提取 + 登录画像注入)    │
│ ④ compose          LLM 合成(COMPOSE_PROMPT 20+ 硬规则) │
│                    失败降级格式化                       │
└─────────────────────────────────────────────────────┘
        │
        ├─ 工具层:28 内置 + eco 生态(推荐/方案/日程/教务/FAQ/冲突/活动)
        ├─ 数据层:SQLite 双库 + ChromaDB 知识库(95篇/1000块) + CAS 实时 + young
        └─ Web 增强:联网证据管线(SearXNG→URL安全→信任→Crawl4AI→声明核验→引用)
                    评课治理(review store) · SSE 事件流 · 隐私强制本地
```

可靠性:LLM 熔断窗、四级降级链、不编造铁律、敏感/闲聊/个人模板快速通道、测试基线 40+49+53+100+11。

## 二、同类开源项目(9 个)

| 项目 | 链路模式 | 检索 | 个人化 | 评估 |
|---|---|---|---|---|
| [SAGE](https://github.com/datazenith-labs/agentic-ai-student-assistant) | Claude+MCP 17 工具三域 | LlamaIndex+Chroma | 个人语料 PDF | 无 |
| [UStudent AI](https://github.com/Fakercoke/ustudent-ai) | LangGraph ReAct 三选一 | 纯稠密+距离门控 | 模拟教务身份 | 161 测试+22 金标+LLM-judge |
| [BU-Life-AI](https://github.com/Kushal9889/BU-Life-AI) | supervisor→3 子 agent 转工具 | BM25+向量 Ensemble | 无 | RAGAS 消融 |
| [lingnan-university-rag](https://github.com/wuziqing2003/lingnan-university-rag) | LangGraph think→act→observe | 向量+BM25+RRF+rerank | 无 | Ragas 50 题+拒答回归 |
| [qwer-agent-assistant](https://github.com/qwerqtqrer/qwer-agent-assistant) | LangGraph 状态机 | FTS5 trigram | 无 | 无 |
| [Campus-AI-RAG](https://github.com/liweisi91010/Campus-AI-RAG) | 规则流水线 | 关键词+Milvus | 无 | 无(人审) |
| campus-docs-assistant / campus-info-agent / ai-campus-knowledge-assistant | LangGraph+意图→工具 | 向量为主 | 无 | 大多无 |

## 三、结论与小蜗差异化

- 小蜗独有:真实 CAS 教务对接、确定性+LLM 双轨决策、证据核验管线、隐私强制本地、参数兜底
- 可借鉴:rerank 重排、显式拒答状态、Ragas/金标端到端评测、多轮记忆摘要、supervisor 多 agent(30+ 工具时)
- 共性短板(也即小蜗优势):评测集小、无生产级教务对接、个人化缺失
