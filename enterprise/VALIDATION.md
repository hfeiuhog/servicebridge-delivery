# 0.2.0 验证范围

日期：2026-10-24。以发布提交的 Enterprise extension CI 为准，不把旧提交的绿灯当成本次验证。

| 层次 | 实际执行 | 范围 |
|---|---|---|
| 本地测试 | 36 passed；独立临时 SQLite、模拟 HTTP | 契约、身份、策略、版本失效、并发确认、原子预演、审批、回执重放、uncertain 阻断 |
| GitHub CI | PostgreSQL 16、相同测试、5 个组件通过真实平台目录加载及实例化 | 具体结果看发布 SHA 对应运行 |
| 正式模型评测 | 30 开发 + 20 留出 + 12 补充，共 62 次真实调用 | 合成数据代理指标，见 evaluation/README.md |
| 初期 smoke | intake-v1，3 次开发调用 | 发现未知影响被填为字符串；保留失败，不混入正式成绩 |
| 模型完整链路 | 额外 1 次真实调用 → 保存确认 → 模拟审批 → 模拟写回 → 相同回执重放，模拟 POST 仅 1 次 | 身份服务及 ServiceNow 为替身 |
| 本地浏览器 | Edge；1440px 桌面及 390px 手机；正常流程与缺项阻断 | 独立演示页面，不是完整 Langflow UI 端到端 |
| 格式检查 | 扩展目录 Ruff format，E4/E7/E9/F/I/UP/B | 不代表上游全仓 lint / 测试通过 |

未完成：真实 ServiceNow 租户接入、客户字段 UAT、企业 SSO/RBAC、负载/灾备演练、独立人工语义评审、真实工时和修订量对照、客户上线与采用。

## 复验

TEST_DATABASE_URL 只能指向可丢弃的专用测试库，测试会 drop/create 扩展表。未设置时默认临时 SQLite。

```powershell
uv pip install pytest pytest-asyncio 'psycopg[binary]' ruff
$env:PYTHONPATH = 'src/lfx/src'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
uv run --no-project pytest -p pytest_asyncio.plugin -c enterprise/pytest.ini enterprise/tests -q
uv run --no-project ruff format --check src/lfx/src/lfx/components/enterprise_delivery enterprise
uv run --no-project ruff check --select E4,E7,E9,F,I,UP,B src/lfx/src/lfx/components/enterprise_delivery enterprise
```

完整目录加载需 `uv pip install -e src/lfx`，随后 `uv run --no-project python enterprise/component_smoke.py`；CI 已配置。最小演示依赖不能代替完整平台安装。

## 已知失败

3/50 条模板模型结果转人工草稿。通用错误码不记录可能含原文或密钥的供应商异常，当前不能从落盘记录区分供应商失败与引用校验失败。

补充集 CHALLENGE-02 / 04 / 07 / 11 把已说明的业务影响漏填为 null，导致多问一次；04 / 07 的详情还错误声称未提供业务影响。CHALLENGE-10 的“未做排查”是否算处理动作，也需要业务人员确认标签口径。引用存在只证明位置，不能证明含义、归属或标题正确。这些语义问题依赖人工复核。
