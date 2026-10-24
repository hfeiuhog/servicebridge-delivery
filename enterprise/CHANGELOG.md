# ServiceBridge 扩展版本记录

版本号表示本扩展，不修改 Langflow 上游版本。

## 0.2.0 · 2026-10-24

- 新增原始描述、候选事实/引用、缺项问题及人工确认路径。
- 新增 Intake / Confirm 两组件，保留原三组件类名；候选及事件持久化。
- 确认绑定版本、所有者、租户及连接策略；修订失效、条件更新、预演冻结与日志创建同事务。
- 官方 DeepSeek Flash：单次调用、结构约束、有界输出、超时及人工兜底。
- 本机演示支持模拟审批、写回、回执重放与 POST 不确定结果。
- 公开 50 条模板、12 条补充样本的模型/规则对照及失败；36 项回归测试，CI PostgreSQL 16 和五组件加载。

### 旧流程迁移

1. 暂停写回，备份配置及独立数据库，核对旧 sending / uncertain。
2. 部署后由管理员执行 `enterprise/provision_journal.py`，新增 intake / intake_event 两张表；不会在模型调用中建表。本版为显式初始化，未接入上游 Alembic。
3. 加入 Intake / Confirm。Confirm 接收当前 draft、人工核对的 candidate 与 urgency，将 confirmation_id Data 传给 Prepare 的 ticket 输入。
4. 未确认 Ticket 默认拒绝。运营方可临时设置 `SERVICEBRIDGE_ALLOW_LEGACY_UNCONFIRMED=true` 兼容旧结构化入口，记录负责人和关闭日期；此例外不提供新增确认保证。
5. 旧 prepared 逐单核对并重新审批，不把旧签名视为已完成内容确认。已有交付表列和组件类名未重命名。
6. UAT 后恢复凭据；回滚先停写回及凭据，保留表、事件和回执，不自动删除外部 Incident。

未宣称客户上线、工时节省、语义自动验真、生产 SSO/RBAC 或跨系统 exactly-once。
