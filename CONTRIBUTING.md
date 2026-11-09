# Contributing to ServiceBridge

欢迎使用、Fork、报告问题和提交改进。本仓库是 `langflow-ai/langflow` 的公开扩展 fork。
向本仓库提交 PR 时，请选择 `hfeiuhog/servicebridge-delivery` 的 `enterprise` 分支作为目标。

## Use this project

Clone or fork this public repository. Read the setup instructions in [README.md](README.md).
Keep the existing licenses, notices, and attribution. Some upstream directories have separate terms.

## Report an extension issue

Open an [issue](https://github.com/hfeiuhog/servicebridge-delivery/issues/new/choose) with a small, reproducible example.
Use synthetic data. Remove secrets, customer records, and private documents.
For private security reports, follow [SECURITY.md](SECURITY.md) when that file applies to the affected upstream code.
Do not post vulnerabilities or secrets in a public issue.

## Submit an extension change

1. Fork `hfeiuhog/servicebridge-delivery` and create a feature branch from `enterprise`.
2. Make one focused change. Keep upstream code and attribution intact.
3. Run the relevant checks from [Enterprise CI](.github/workflows/enterprise-ci.yml).
4. Open a pull request to `hfeiuhog/servicebridge-delivery`, with `enterprise` as the base branch.
5. Describe the problem, changed behavior, test results, and compatibility limits.

New contributors' Actions runs may need maintainer approval.
PRs do not grant direct write access. The maintainer reviews and merges changes.
Disclose material AI assistance and verify the submitted result.

## Contribute to the upstream project

The GitHub **Contribute** button proposes changes to `langflow-ai/langflow`.
That is separate from making this fork public or accepting changes here.
Follow the [upstream contribution guide](CONTRIBUTING.upstream.md) and use a clean branch for one upstream change.
Keep this fork's branding, business-specific workflows, and maintenance files out of an upstream PR.

A submitted PR is a review request. Only a merged PR is an accepted upstream contribution.
The current extension uses AI assistance; it does not imply upstream approval or production deployment.

## Sync this fork

Read [同步与协作说明](enterprise/COLLABORATION.md).
Keep extension work on `enterprise`. Merge upstream updates and resolve conflicts before publishing.
