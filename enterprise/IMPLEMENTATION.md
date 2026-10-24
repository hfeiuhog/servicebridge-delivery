# 企业售后交付手册

适用版本：ServiceBridge 0.2.0。先用合成数据验证完整流程；真实客户接入与收益测量尚未完成。

## 内容整理与人工确认

Langflow 平台按上游说明从本分支构建。生产路径配置 PostgreSQL 并显式建表后，连接四个业务步骤：

1. **EnterpriseTicketIntake**：source_text 为授权原文；case_ref / customer_ref 来自受控业务上下文，profile_name 来自运营配置。可接 LanguageModel，或由运行环境提供官方 DeepSeek 密钥。
2. **EnterpriseConfirmTicket**：draft 接 Intake 的 Data；final_content 包含 `candidate`（summary、detail、facts、evidence）和人工选择的 `urgency`。确认结果是服务端保存的引用，不是客户端布尔值。
3. **EnterprisePrepareChange**：ticket 接 Confirm 的 Data；使用同一 profile。服务端重新读取确认的 Ticket，忽略客户端另传内容，并在同一事务冻结候选和创建交付记录。
4. **EnterpriseServiceNowWriteback**：proposal 接 Prepare 输出，review_grant 来自独立审批服务；profile 同前。审批不是模型或请求人自行生成。

共注册五个组件；保留的 EnterpriseTicketContract 负责结构化字段映射，其直接接 Prepare 的旧流程默认不再有效。兼容开关和数据库升级见 [CHANGELOG.md](CHANGELOG.md)。

必需事实为设备/服务、故障现象、业务影响；已尝试操作和替代办法可为空。补充信息要先写入原文，再重新生成或修订，保证引用对应当前原文。修订、重新确认都会更新版本；旧 confirmation_id 失效。预演后冻结，内容变化需新草稿；同一工单的内容冲突不得静默覆盖。

源原文、模型候选及最终内容均不可信。引用位置检查不等于语义验真，专员必须核对标题、描述、事实及引用；模型漏项或失败可人工填写。缺项未补、urgency 未选择不能确认。模型单次调用不触发任何外部业务写入。

## 模型接入及演示边界

默认官方 DeepSeek Flash；通过 DEEPSEEK_API_KEY 注入，模型字段只能选择适配器允许的 Flash / Pro，演示及评测使用 Flash。平台异步调用外层最多等待 20 秒；HTTP 同步适配器超时 25 秒。请求最多 1536 输出 token，不自动重试；失败保留人工可编辑草稿。没有模型时，真实平台组件转人工草稿，本地 demo 的 rules 则显式使用窄规则，二者不可混称 LLM。

本地独立 UI 见 README 快速开始，SQLite 只用于演示。它模拟登录、独立审批人和 ServiceNow，用于验证交互与业务内核；不提供真实客户权限控制。生产必须使用平台登录上下文、运营配置及真正外部审批侧，不能直接公网开放 demo。

## 效果与收益验收

参考 [评测报告](evaluation/README.md)，先区分契约通过、内容语义和业务收益。合成标签不是独立人工金标。试点时抽取获授权真实工单，由业务专家盲审参考；同一批任务随机或交叉分配人工填单、规则填单与 AI 辅助填单，统一起止时间，记录事实错误、漏项、多问、修改字段数、完成时间及失败恢复时间。数据收集表见 evaluation/human-review-template.csv。

当前没有真实人工对照，不能写“节省 X%”。上线门槛需业务签字：错误风险、可接受等待、人工兜底、客户系统权限和故障责任人均达标，再考虑扩面。

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
