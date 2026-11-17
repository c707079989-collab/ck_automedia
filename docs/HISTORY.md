# 项目提交历史（合并进 monorepo 前的两个仓库日志）

> 说明：这两个仓库原来各自带 .git，其中 social-auto-upload 的浅克隆丢失了一个父提交对象（7023053e），
> 导致 git push 被远端拒绝。合并成 monorepo 时以工作区快照重建，历史提交信息保存在这里备查。
> 原始 .git 已归档在 _archive/git-backup/（不随仓库发布）。

## social-auto-upload
```
87c296b  2026-11-17  YourName  feat(creator): 创作工作台改成三步向导 + 我的方案浮动抽屉
ec2b195  2026-11-17  YourName  fix(creator): 口播台词进提示词 + 任务状态自愈 + 出片端口可配置
59d5185  2026-11-17  YourName  创作者平台
ff2d21b  2026-11-17  YourName  chore: 移除只剩 README 的 sau_backend/ 空壳目录
769563c  2026-11-17  YourName  fix(hot-capture): 并发分析的状态与结果串台
4e87ec8  2026-11-17  YourName  feat(hot-capture): 抓取复用账号管理登录态，抖音真抓取跑通
ac1ea3b  2026-11-17  YourName  feat(hot-capture): 可配置领域抓取 + 逐条 WorkBuddy agent 分析
3891cb0  2026-11-17  YourName  feat: 主程序集成爆款采集与 agent 分析
cc72677  2026-11-17  WorkBuddy Local  feat: 修复小红书扫码登录并接入B站平台
3847fff  2026-10-23  home-dev-pookz  docs: clarify skill marketplace publishing status
```

## media-crawler
```
f77b310  2026-11-17  YourName  chore: 恢复 docs 里被运行期引用的两个文件
bcb0eb1  2026-11-17  YourName  chore: 移除上游文档与测试（docs/ 22M、tests/、test/），运行链路不依赖
322bddc  2026-11-17  YourName  feat(douyin): 支持 --storage-state 导入外部登录态
d137e8d  2026-11-17  YourName  feat: 本地爆款抓取适配 - CDP有头模式 + 完整cookie注入
4d629b5  2026-11-14  程序员阿江(Relakkes)  update README.md
```
