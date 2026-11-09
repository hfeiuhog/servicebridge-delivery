# 公开协作与上游同步

本仓库公开。任何人可以读取、下载、Fork，并通过 PR 提议改动。公开读取不授予直接写入权限。

## 三个入口的含义

| 入口 | 作用 |
| --- | --- |
| Code / Fork | 获取本仓库代码或建立自己的副本 |
| 本仓库 Issues / Pull requests | 给本扩展反馈问题或提交改进，目标为本仓库 `enterprise` 分支 |
| Contribute / Open pull request | 向原项目 `langflow-ai/langflow` 提交审核请求，需遵守原项目贡献规则 |
| Sync fork | 将原项目更新合入当前分支；不会自动把扩展贡献给原项目 |

## 安全同步

保留 `enterprise` 分支及现有扩展提交。`ahead` 表示这里有原项目没有的提交；维护 fork 时属于正常状态。
`behind` 表示有上游更新尚未合并。出现冲突时，在单独的维护分支解决并验证，然后正常合并。
不要点击 **Discard commits / 丢弃提交**；该操作会移除自己的扩展。

```bash
git switch enterprise
git fetch upstream
git switch -c maintenance/sync-upstream
git merge upstream/main
# Resolve conflicts, then run the extension checks.
# Open a PR to hfeiuhog/servicebridge-delivery:enterprise.
```

上面的流程在已配置 `upstream` 且工作区无未提交改动时使用。`upstream` 应指向 `https://github.com/langflow-ai/langflow.git`。
发生冲突时逐项处理，保留两边有效的内容。不要用强制重置替代合并。

## 贡献证据

保留 `UPSTREAM.json` 中的原始 `base_commit`，用于说明扩展起点。
`last_synced_commit` 表示最近纳入的上游版本，不能据此把上游代码算作自己的新增工作。
查看 [扩展文件的提交记录](https://github.com/hfeiuhog/servicebridge-delivery/commits/enterprise/enterprise) 和 Enterprise CI 的具体运行结果。
向原项目提交的 PR，只有在合并后才可表述为“被上游采纳”。

本次同步基准：`60999cc9a7350e44dde3ef641baae864e10861f0`（2026-11-09）。新的上游提交出现后，GitHub 的 behind 数量可能再次增加。
