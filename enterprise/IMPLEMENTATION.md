# 企业售后交付手册

## 需求发现与试点

确认业务负责人、工单来源、字段口径、可处理工单范围、人工审批人、ServiceNow 实例、账号最小权限、审计保留期和回滚负责人。
首期只创建 Incident，不自动关闭工单或修改既有记录；使用脱敏数据和测试租户。组织运营方、执行人、审批人三种职责，审批人不得与请求人相同。

## 运营配置

`SERVICEBRIDGE_DATABASE_URL` 使用独立 PostgreSQL 数据库和最小权限账号。先由数据库管理员执行 `enterprise/provision_journal.py` 建表，禁止在每次流程中动态创建表。
`SERVICEBRIDGE_PROFILES_FILE` 指向平台用户无法修改的 JSON 配置。每个 profile 包含 tenant、audience、base_url、token_env、allowed_user_ids 和 PEM 格式 review_public_key；不在流程中接收任意 URL/token。
ServiceNow origin 必须是 HTTPS `*.service-now.com`，不带路径、账号、非标准端口。API token 通过对应 token_env 注入。审批私钥仅存外部审批服务/HSM，不能配置在执行平台。
关闭匿名运行，限制任意自定义 Python 组件、管理员权限及环境变量访问，启用上游 SSRF DNS 校验；仅允许用户触达所授权 profile。

## UAT 矩阵

| 场景 | 预期 |
|---|---|
| 字段映射缺失或额外敏感源字段 | 缺失失败；额外字段丢弃 |
| 无权限执行用户/跨租户调用 | 外部请求前拒绝 |
| 请求人自批、摘要/目标被修改、审批过期 | 拒绝写回 |
| 同一工单相同摘要重复执行 | 返回既有状态/回执 |
| 同工单变更内容而未单独处理 | 冲突，不静默覆盖 |
| 两个 worker 同时 claim | 只有一个进入 sending |
| ServiceNow POST 超时或响应不确定 | uncertain，禁止自动重发 |
| 查询阶段失败，尚未 POST | 回到 prepared，可重新核对后执行 |

## 故障处理与回滚

uncertain 或遗留 sending 必须核查 correlation_id、服务端日志和实际 Incident；由授权运维在审计流程下修正状态。HTTP 请求取消可能留下 sending，也不得盲目重发。单纯查询未发现记录不证明前一次 POST 绝未成功。
发布先 dry-run，再少量测试租户写回，再业务签字扩面。回滚关闭写回组件和执行凭据，保留日志及回执；创建成功的 Incident 由业务系统授权人员处理，代码回滚不会删除外部工单。
CI 使用真实 PostgreSQL 和模拟 HTTP；客户测试实例、权限配置与真实字段差异仍需 UAT。本连接器只发送标准 Incident 字段，customer_ref 留于本地契约，不假设客户有自定义字段。
