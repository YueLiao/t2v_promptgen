# 网页版使用说明

可点击通走的网页前端,FastAPI + Jinja2 + Alpine.js + Tailwind(全走 CDN,无构建)。

## 启动

```bash
pip install fastapi uvicorn jinja2 python-multipart pydantic
cd /path/to/t2v_benchmark
uvicorn t2v_promptgen.web.app:app --reload --port 8765
```

浏览器打开 `http://localhost:8765`。

> ⚠️ 任务状态目前是内存里的,**重启服务会丢**。要保留请下载产物文件,或等 v0.7 接入 SQLite 持久化。

---

## 操作流程

首页 2 个 tab,选一种:

### 🪄 从零生成(原有流程,P0-P5)

1. **首页** → 填"想测什么能力" + API 密钥,点开始
2. **确定评测维度页** → AI 列出检查项 + 测试变量,你看着满意点确认;不满意写意见点重新生成(最多 5 轮)
3. **生成中页** → 系统批量写测试用例,几分钟
4. **审核页** → 看用例列表 + 覆盖度图表;可以删单条,可以让 AI 重生(最多 3 轮);满意点通过
5. **导出页** → 下载 4 个文件,带走

不填 API 密钥也能跑 — 会用示例数据演示,产物质量一般,只为看流程。

### 📋 改写已有 prompt(新流程,R0-R6)

1. **上传** → 选 JSON/JSONL/TXT/CSV/XLSX 文件,最大 10MB / 5000 行;自动识别格式 + 编码
2. **字段映射** → AI 猜哪一列是 prompt 文本、哪一列是 id;你可调整
3. **改写指令** → 从 12 张卡片里选(主体替换 / 时序提升 / 因果链 / 风格转换 ...)+ 自由文本补充
4. **改写中** → 异步批量改写,有进度,可取消
5. **审核(diff 视图)** → 原 prompt ↔ 改写后并排显示 + keep/adherence 分数 + 单条接受/拒绝;拒绝的可以"让 AI 再改一遍"(最多 3 轮迭代)
6. **导出** → prompts.jsonl + rewrite_diff.jsonl(原 ↔ 改写映射)

详细设计:[`../docs/design_prompt_rewrite.md`](../docs/design_prompt_rewrite.md)

---

## 路由一览

### 页面

| 路由 | 模板 | 看到什么 |
|---|---|---|
| `GET /` | `index.html` | 首页:新建任务 + 已有任务列表 |
| `GET /runs/{id}` (维度阶段) | `dimensions.html` | 检查项 + 测试变量,可调整 |
| `GET /runs/{id}` (生成中) | `generating.html` | 转圈,自动刷新 |
| `GET /runs/{id}` (审核阶段) | `review.html` | 用例列表 + 覆盖度图 + 摘要 |
| `GET /runs/{id}` (导出阶段) | `export.html` | 4 个下载链接 + 任务摘要 |
| `GET /settings` | `settings.html` | API 设置 + 连接测试 |

### API

| 路由 | 方法 | 用途 |
|---|---|---|
| `/runs` | POST | 创建任务 |
| `/runs/{id}/p1/regenerate` | POST | 维度阶段重新生成 |
| `/runs/{id}/p1/confirm` | POST | 维度阶段确认,进入生成 |
| `/runs/{id}/p4/drop/{pid}` | POST | 审核阶段删一条用例 |
| `/runs/{id}/p4/regenerate` | POST | 审核阶段重新生成 |
| `/runs/{id}/p4/confirm` | POST | 审核阶段通过,进入导出 |
| `/runs/{id}/download/prompts.jsonl` | GET | 下载测试用例主文件 |
| `/runs/{id}/download/handbook.md` | GET | 下载评测员说明书 (Markdown) |
| `/runs/{id}/download/handbook.json` | GET | 下载评测员说明书 (JSON) |
| `/runs/{id}/download/coverage.json` | GET | 下载覆盖度报告 |
| `/runs/{id}/delete` | POST | 删除任务 |
| `/api/runs/{id}/state` | GET | 看任务完整 JSON 状态 |
| `/api/llm/test` | POST | 测试 API 服务能否连通 |

### 改写流(R0-R6 路由)

| 路由 | 方法 | 用途 |
|---|---|---|
| `/rewrite/upload` | GET/POST | R0 上传页 + 解析 |
| `/rewrite/{id}/map` | GET/POST | R1 字段映射 |
| `/rewrite/{id}/mapping/guess` | GET | LLM 猜列名 |
| `/rewrite/{id}/directive` | GET/POST | R2 卡片 + 自由文本 |
| `/rewrite/cards` | GET | 12 张卡片 spec JSON |
| `/rewrite/{id}/start` | POST | R3 启动异步改写 |
| `/rewrite/{id}/cancel` | POST | 取消进行中的改写 |
| `/rewrite/{id}/progress` | GET | 查询改写进度(轮询) |
| `/rewrite/{id}/accept/{pid}` | POST | R5 单条接受/拒绝 |
| `/rewrite/{id}/iterate` | POST | R5 让 AI 重改拒绝的(≤3 轮) |
| `/rewrite/{id}/confirm` | POST | R5 通过 → 进 R6 导出 |
| `/runs/{id}/download/rewrite_diff.jsonl` | GET | R6 diff 报告 |

---

## 真实 LLM 接入情况

| 步骤 | 状态 | 说明 |
|---|---|---|
| 能力分类 (P0) | **真实 LLM** | `phases/intake.py` LLM 选 slug,半开放词表,失败回退关键词 |
| 检查项 + 测试变量生成 (P1) | **真实 LLM** | 默认 `deepseek-v4-pro`,可换 |
| 维度评审 (P1) | **真实 LLM** | `qa/dimensions_judge.py` 评分 + 找漏 + 给改进建议 |
| 测试用例生成 (P2) | **真实 LLM** | 默认 `deepseek-chat`;每批注入 40 个具体场景标签 + 主体多样性配额 |
| 难度评分 | **真实** | 启发式(动作 + 时序 + 多主体 + 多 SL2 + 物理 + 遮挡),静态描述被丢 |
| 规则检查 (P3) | **真实** | 长度 / 禁词 / 必填字段 |
| 自然度评分 (P3) | **真实 LLM** | 批量打中英 0-10 分,阈值 7 |
| 覆盖反核 (P3) | **真实 LLM** | LLM 独立判断 SL2 实际覆盖,和自报对比 |
| 评测员手册 | **真实** | 从已生成数据渲染 |
| 任务状态持久化 | ❌ 内存(重启丢) | 待 v0.8 |

> 完全不填 API key 的话,所有步骤走 mock,产出固定示例数据(根据能力 slug 选模板:人手 / 人体 / 运镜 / 物理)。

---

## 支持的 AI 服务

通过 `llm/providers/openai_compat.py` 统一适配 OpenAI 兼容协议,覆盖:

**中转接口**(一个 key 调多家)
- yibuapi
- 任意 OpenAI 兼容自定义 endpoint

**官方接口**
- Anthropic Claude
- OpenAI GPT
- DeepSeek(默认)
- 阿里通义 Qwen
- 月之暗面 Moonshot
- 智谱 GLM
- SiliconFlow

切换方式:首页表单里选服务 + 填模型名 + 贴 key 即可。或者去 `/settings` 页面先测连通性。

---

## 代码文件

```
web/
├── app.py              # FastAPI 应用 + 所有路由
├── mock_data.py        # 没 API key 时的示例数据(按能力分支)
├── llm_phases.py       # 真实 LLM 调用:维度生成 + 用例生成
├── templates/
│   ├── base.html       # 公共框架 + 顶栏 + 阶段进度条
│   ├── index.html      # 首页(新建任务表单 + 任务列表)
│   ├── dimensions.html # 确定评测维度页
│   ├── generating.html # 生成中(自动刷新跳转)
│   ├── review.html     # 审核页(用例表 + 覆盖图)
│   ├── export.html     # 导出页(4 个下载)
│   └── settings.html   # API 设置 + 连接测试
└── static/             # 留空,所有 CSS/JS 走 CDN
```

## 下一步计划

- [ ] 任务状态写入 SQLite,服务重启不丢
- [ ] 自动质检阶段接入真实 AI 复审
- [ ] 能力模板继承(同能力第二次任务直接载入上次的检查项)
- [ ] 任务费用预估 + 实时统计
