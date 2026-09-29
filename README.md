# 基于 LangChain + RAG 的智能毕业设计管理系统

> FastAPI · Vue3 · TypeScript · MySQL8 · Redis(RediSearch) · Hybrid RAG · AI 模拟答辩

依据 `roadmap.md` 完整实现: 传统毕业设计业务管理 + RAG 智能能力深度融合的全栈系统。

## 一、功能总览

### 传统业务(三角色 RBAC + 对象级数据权限)

```
题目发布 → 学生选题 → 选题审核 → 开题报告 → 开题审核 → 中期答辩 → 中期审核
→ 终期答辩 → 终期审核 → 毕业材料 → 材料审核 → 毕业
```

- **管理员**: 数据统计(ECharts)、题目/选题/四阶段全局视图、用户管理、公告管理、全局进度
- **教师**: 我的题目、四阶段审核(含终期评分)、学生进度、AI 进度分析、一键提醒
- **学生**: 题目中心、我的选题、四阶段材料(版本管理+审核轨迹)、毕业材料上传、进度时间轴

### AI / RAG 能力(业务内嵌, 非独立聊天页)

- **文档入库流水线**: 上传论文 → PyMuPDF/python-docx 解析 → 文本清洗 → 章节识别 → 500~900字分块(overlap 120) → Embedding → Redis 向量库(HNSW+KNN, 无 Search 模块自动降级 MySQL 余弦) → 元数据落库
- **Hybrid RAG**: 向量 KNN + 关键词检索 → RRF 融合 → 强制 student_id 过滤(学生间知识库隔离)
- **AI 模拟答辩**: 按维度生成22题问题库(基础3/技术5/设计4/实现4/创新2/不足2/扩展2) → 逐题四维评分(准确35/逻辑25/表达20/深度20) → 模糊回答触发动态追问 → 答辩报告 → PPT大纲
- **多轮对话**: Query Rewrite 生成 standalone question 再检索; SSE 流式输出
- **教师进度分析**: 规则引擎算确定性进度/风险(阶段权重 10/20/25/25/20), LLM 只解释不编造
- **防幻觉与注入防护**: 检索内容标记 UNTRUSTED, System Prompt 限定只依据上下文

## 二、快速开始

### 1. 基础设施

```bash
docker compose up -d        # MySQL 8 (3306) + redis-stack (6380, 含 RediSearch)
```

### 2. 后端

```bash
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # 按需修改; 不填 LLM API Key 也可运行(内置离线 Mock)
.venv/bin/python seed.py    # 建表 + 种子数据 + 演示论文入知识库
.venv/bin/python -m uvicorn app.main:app --port 8000
```

健康检查: `curl http://127.0.0.1:8000/api/v1/health`

### 3. 前端

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5174 (已配置 /api 代理到 8000)
```

### 4. 演示账号

| 角色 | 账号 | 密码 | 说明 |
| ---- | ---- | ---- | ---- |
| 管理员 | admin | admin123 | 全局管理与统计 |
| 教师 | teacher01 / teacher02 | 123456 | 张伟(有AI演示数据) / 李娜 |
| 学生 | 20230001 | 123456 | **主演示账号**: 已到毕业材料阶段, 论文已入知识库, 可直接体验 AI 模拟答辩 |
| 学生 | 20230002~07 | 123456 | 各阶段分布(供统计/审核演示) |

### 5. 接入真实大模型(可选)

编辑 `backend/.env`:

```env
LLM_PROVIDER=auto           # 有 Key 自动启用真实 API
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # 智谱; DeepSeek/通义/OpenAI 均兼容
LLM_API_KEY=你的Key
LLM_MODEL=glm-4-flash

EMBEDDING_API_KEY=你的Key
EMBEDDING_MODEL=embedding-3
```

重启后端即可, 代码零修改(所有模型经 LLMService/EmbeddingService 抽象)。

### 6. 自测

```bash
cd backend && .venv/bin/python smoke_test.py
# 42 项断言: 登录鉴权/RBAC/选题并发/阶段状态机强校验/对象级权限/
#           RAG学生隔离/AI答辩全流程/PPT/进度分析/SSE/Dashboard
```

## 三、核心设计

### 统一状态机(roadmap 第一优先级)

- 阶段材料生命周期: `NOT_SUBMITTED → DRAFT → SUBMITTED → APPROVED / REJECTED → (修改) → SUBMITTED(v+1)`
- 开题/中期/终期共用 `app/modules/stages/engine.py` 状态机引擎, 各模块仅是配置
- **阶段前置校验全部在后端**: 开题未通过时 `POST /student/midterm` 返回 40001, 前端按钮禁用只是体验优化
- 提交创建版本快照(不可覆盖历史), 审核动作全部写入 `review_record` 支撑时间轴

### 并发安全

- 一人一题: `UNIQUE(student_id, active_flag)` + 历史记录 `active_flag=NULL` 归档
- 名额不超卖: 审核通过时 `SELECT ... FOR UPDATE` 锁题目行 + 复查已通过人数

### 数据权限(对象级)

- 教师 → 仅自己题目的学生; 学生 → 仅自己; RAG 检索强制 `student_id` TAG 过滤
- 测试场景: 教师A访问教师B学生材料 → 404; 学生A检索不到学生B论文 Chunk

### Redis Key 规划

```
auth:refresh:{user_id}  auth:blacklist:{jti}  rate:login:{ip}  rate:ai:{user_id}
rag:chunk:{chunk_id} → idx:graduation_chunks (HNSW COSINE + TAG 过滤)
```

## 四、目录结构

```
backend/
├── app/
│   ├── core/          # config/JWT/权限/异常/统一响应
│   ├── db/            # SQLAlchemy/Redis
│   ├── models/        # 用户/业务/系统/AI 四组模型 + 状态机枚举
│   ├── modules/       # auth user topic selection opening midterm final_defense
│   │   │              # material file progress announcement dashboard ai
│   │   └── stages/    # 通用阶段状态机引擎 + 路由工厂
│   ├── ai/
│   │   ├── llm/ embedding/     # Provider 工厂(OpenAI兼容 + 离线Mock/本地向量)
│   │   ├── document/           # 解析(PDF/DOCX/PPTX/XLSX/TXT) + 章节感知分块
│   │   ├── vector/             # Redis KNN / MySQL 余弦自动降级
│   │   ├── rag/                # Hybrid 检索(RRF) + RAGService + SSE
│   │   ├── defense/            # AI模拟答辩: 问题/评价/追问/报告/PPT
│   │   └── progress/           # 教师AI进度分析
│   ├── tasks/         # Celery(可选, 默认同步执行)
│   └── shared/        # 工作流状态机/进度规则引擎/存储/事件
├── seed.py            # 种子数据(含演示论文+索引)
└── smoke_test.py      # 42项核心链路自测

frontend/src/
├── api/ stores/ router/ layouts/
├── components/        # StatusTag/ProcessTimeline/ReviewHistory/MarkdownViewer
└── views/             # login dashboards student(含AiDefense/AiChat) teacher admin common
```

## 五、API 概览(统一前缀 /api/v1, 响应 {code, message, data})

- Auth: 登录/刷新/登出/资料/密码
- 业务: topics / selections / opening-report / midterm / final-defense / material / progress
- AI: `/ai/documents/*`(索引) `/ai/rag/query`(检索测试) `/ai/chat/stream`(SSE)
  `/ai/defense/*`(问题库/会话/回答/追问/报告) `/ai/ppt-outline/generate` `/ai/progress/student/{id}/analyse`
- 完整文档: 启动后访问 `http://127.0.0.1:8000/docs`

## 六、生产部署(Docker Compose 五容器)

```
nginx(前端静态+/api反代) ← backend(FastAPI×2) ← mysql8 + redis-stack
                            └─ celery-worker(文档索引等重任务)
```

```bash
cp .env.prod.example .env.prod    # 修改密钥与大模型 Key
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python seed.py   # 首次初始化演示数据(可选)
# 访问 http://localhost:8080
```

- 生产编排默认 `CELERY_ENABLED=true`, 论文解析/Embedding 由独立 Worker 执行
- nginx 已为 `/api` 关闭缓冲, SSE 流式回答可直接穿透
- 数据卷: `gs-mysql-data` / `gs-redis-data` / `gs-uploads`
- 后续演进: Alembic 迁移替代启动建表(`AUTO_CREATE_TABLES=false` + `alembic upgrade head`)

## 七、与 roadmap 的对应

| 阶段 | 内容 | 状态 |
| ---- | ---- | ---- |
| P0 基础框架 | 登录/JWT/RBAC/菜单/统一响应/异常 | ✅ |
| P1 业务流程 | 题目/选题/审核(并发控制)/四阶段状态机 | ✅ |
| P2 文件与进度 | 版本管理/文件表/进度权重/风险规则/提醒 | ✅ |
| P3 RAG | 解析/分块/Embedding/Redis向量/Hybrid检索/测试接口 | ✅ |
| P4 AI答辩 | 问题库/会话/四维评分/动态追问/报告/PPT大纲 | ✅ |
| P5 数据统计 | 三角色首页/ECharts 四图/AI用量 | ✅ |
| P6 测试 | 42项核心场景自测(含7个必测场景+RAG隔离) | ✅ |

> 说明: LLM/Embedding 未配置 API Key 时自动降级为内置离线实现(Mock LLM + 本地哈希向量),
> 全部链路可离线演示; 配置 Key 后无需改码即切换真实大模型。
