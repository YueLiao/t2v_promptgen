# Web UI ｜ t2v_promptgen v0.6

可点击通走的原型,所有 LLM 调用 mock 化。

## 启动

```bash
pip install fastapi uvicorn jinja2 python-multipart pydantic
cd /Users/yue/Documents/t2v_benchmark
uvicorn t2v_promptgen.web.app:app --reload --port 8000
```

浏览器:`http://localhost:8000`

## 页面

| 路由 | 模板 | 阶段 |
|---|---|---|
| `GET /` | `index.html` | 首页(新建 + Run 列表) |
| `GET /runs/{id}` (phase=P1) | `dimensions.html` | 维度迭代页 |
| `GET /runs/{id}` (phase=P4) | `review.html` | 审核页(覆盖热图 + Prompt 表) |
| `GET /runs/{id}` (phase=P5) | `export.html` | 导出页(4 个下载) |

## API

| 路由 | 方法 | 用途 |
|---|---|---|
| `/runs` | POST | 创建 run |
| `/runs/{id}/p1/regenerate` | POST | P1 重新生成维度 |
| `/runs/{id}/p1/confirm` | POST | P1 确认进入 P2 |
| `/runs/{id}/p4/drop/{pid}` | POST | 删除一条 prompt |
| `/runs/{id}/p4/regenerate` | POST | P4 重新生成 |
| `/runs/{id}/p4/confirm` | POST | P4 确认进入 P5 |
| `/runs/{id}/download/prompts.jsonl` | GET | 下载主数据 |
| `/runs/{id}/download/handbook.md` | GET | 下载评测员说明书 |
| `/runs/{id}/download/handbook.json` | GET | 平台 ingest JSON |
| `/runs/{id}/download/coverage.json` | GET | 覆盖报告 |
| `/runs/{id}/delete` | POST | 删除 run |
| `/api/runs/{id}/state` | GET | 完整 Run JSON |

## 当前 mock 范围

| 步骤 | 状态 |
|---|---|
| 能力 slug 抽取 | mock(关键词匹配) |
| 维度 / Axes 生成 | mock(预设 8 个 SL2 + 4 axes 模板) |
| Prompt 生成 | mock(模板填空 60 条,数量动态) |
| 难度评分 | **真实**(qa.difficulty 启发式) |
| 质检 | 跳过 |
| 评测员说明书生成 | **真实**(从 mock 数据渲染) |
| Memory 持久化 | TODO(in-memory only) |
| LLM 调用 | TODO |

## 下一步

把 `web/mock_data.py` 替换为真实 `phases/*` 实现即可。Web UI 不需要改。

## 关键文件

```
web/
├── app.py              # FastAPI + 路由
├── mock_data.py        # 临时数据(替换为真实 phases)
├── templates/
│   ├── base.html       # 公共 shell + 阶段进度条
│   ├── index.html      # 首页
│   ├── intake.html     # P0(自动跳过)
│   ├── dimensions.html # P1
│   ├── generating.html # P2/P3 占位
│   ├── review.html     # P4(覆盖热图 + Prompts 表)
│   └── export.html     # P5
└── static/             # (空,所有 CSS/JS 走 CDN)
```
