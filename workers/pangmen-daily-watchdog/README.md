# 旁门日报外部定时看门器

这个 Cloudflare Worker 只负责可靠地触发 GitHub Actions；Horizon 继续在
GitHub Actions 中采集、生成日报并推送飞书。它在每天北京时间 09:15 首次触发，
并在 09:35、10:00 检查当天运行状态：首次任务未启动或失败时，最多补跑一次。

## 部署前准备

1. 在 GitHub 创建一个 **fine-grained personal access token**，只授权仓库
   `yachen8915-sys/Horizon` 的 **Actions: Read and write** 权限。
2. 在本目录执行 `npx wrangler login`，完成 Cloudflare 账户登录。
3. 执行 `npx wrangler secret put GITHUB_DISPATCH_TOKEN`，粘贴上一步生成的
   GitHub token。该值只保存为 Cloudflare Worker Secret，不写入仓库。
4. 执行 `npx wrangler deploy`。确认部署输出包含 Worker URL 和三条 cron。

部署使用交互式登录，不需要把 Cloudflare API Token 存到仓库。只有改成由 GitHub
自动部署 Worker 时，才额外需要 `CLOUDFLARE_API_TOKEN` 作为 GitHub Secret。

## 上线验收

1. 在 Cloudflare Workers 的日志中确认一次 scheduled 触发成功。
2. 在 GitHub Actions 确认出现由 `workflow_dispatch` 发起的完整日报任务。
3. 确认该任务日志有 `Webhook sent successfully (status=200)`。
4. 以上三项都确认后，删除 `.github/workflows/daily-summary.yml` 中的 GitHub
   `schedule` 触发器，只保留 `workflow_dispatch` 作为手动入口，避免重复推送。

## 运行边界

看门器不能保证第三方服务绝不故障，但能避免 GitHub 原生 cron 漏触发时静默失联；
同时，Horizon 在飞书 webhook 未被接受时会让 Action 失败，使 09:35/10:00 的检查
能够识别并补跑。
