# 调研报告 2:小蜗「回答响应机制」× 消费级 AI 产品对比

> 日期:2026-08-29 · 依据:官方文档/技术博客/媒体报道调研,已区分【确认/推测/未公开】

## 一、消费级标准范式

| 产品 | 流式 | 过程可视化 | 引用 | 交互 | 记忆 |
|---|---|---|---|---|---|
| [DeepSeek 网页版](https://www.zgeo.com.cn/geo-101/deepseek-citation-mechanism) | 打字机逐 token | 思考区块**可折叠**+「正在搜索…」 | 正文 `[1][2]` 角标+文末来源列表 | 复制/重生成/点赞/停止/编辑重生成/「继续生成」 | 会话内延续 |
| [豆包 App](https://seed.bytedance.com/zh/SeedRealtime) | SSE delta + **语音全双工双通道** | 深度思考+「思考中/搜索中」 | Web Search 工具,annotations 承载引用 | 多模态输入、边生成边朗读 | 多轮深度对话 |
| [Kimi](https://www.kimi.com/zh-cn/help/features/search) | API SSE 流式 | 多步思考+搜索状态 | `[1][2]` 上标+文末来源 | 「继续」按钮 | **跨会话「记忆空间」** |
| [ChatGPT 网页版](https://ahmed-n-abdeltwab.github.io/blog/2025/08/25/chatgpt-sse-architecture.html) | fetch+ReadableStream 增量 | 推理模型**可折叠 reasoning 摘要**(不露思维链) | 上标→点击弹来源卡 | 停止/编辑重生成/Continue generating | 历史打包+Memory |

## 二、小蜗现状(改造前)与差距

- 流式:整篇一次性推送,无打字机 —— **大差距**
- 过程:仅 4 个 stage 文本,think 决策不上屏 —— 中差距
- 引用:证据模式来源列表已结构化(SourceList 组件),本地模式为文本 URL —— 小差距
- 交互:有停止按钮、重试;缺编辑重生成、继续生成 —— 中差距
- 记忆:会话历史 12 条+用户档案(不可见)—— 小差距

## 三、已落地改造(2026-08-29, B 类)

1. **B1 打字机渲染**:`TypewrittenAnswer` 流式期间 rAF 逐块渐显 markdown,完成一次全显
2. **B2 思考过程卡**:后端 `thought.step` SSE 事件(每轮 decision+reason,不露提示词),前端折叠卡展示
3. **B3 来源卡片**:已有 SourceList 组件(标题/信任级/日期/链接)
4. **B4 继续生成**:compose 检测 `finish_reason=length` → `truncated` 标记 → answer.completed 透出 → 前端「继续生成」按钮以续写指令重发起
5. **B5 编辑重生成**:用户消息悬停编辑 → textarea → 本地截断+重新提交;停止按钮已有

未做(暂缓):语音/多模态(非比赛必需)、档案可见化、rerank、多轮记忆摘要。
