# 归档区（不再维护）

本目录存放已经下线、仅作备查的代码与配置。**任何流程都不会再读取或执行这里的文件。**

## super-source/ ——「全网汇总」超级源（2026-11-28 正式弃用）

**为什么下线**：这套功能依赖腾讯 EdgeOne Pages 边缘函数（`super-source/functions/api.js`）对外提供
maccms 接口和网盘 Cookie 管理页（`?mode=panck`）。EdgeOne 项目 `makers-lxb2wlvf61qz`（域名
`tvbox-juxu-spegz3xv.edgeone.cool`）被误删后全站 404；重建项目 `makers-o6ymftyp74bq`
（`tvbox-juxu-m9b9mcni.edgeone.cool`）的所有部署都失败（Code=26），无法恢复。

用户明确选择「彻底放弃」：网盘播放/汇总页依赖自家网盘 Cookie，维护成本高、报错频繁。
因此：

- `latest.json` 不再注入任何 `super` 条目（本站点数：49 个普通采集站，与超级源无关）
- 流水线不再生成 `super_catalog.json`，不再签发 `eo_token`
- `.github/workflows/update.yml` 已移除 `EDGEONE_TOKEN` 注入与目录复制步骤
- 原 `refresh_token.yml`（每小时续签 token）已停用，文件移入本目录

**影响面**：只有「全网汇总 / 网盘播放 / panck Cookie 管理」这组功能不可用；
普通站点浏览、搜索、直播、详情多线路播放完全不受影响。

**如果将来想恢复**，三条路径（按成本排序）：
1. 腾讯云函数 SCF / 阿里云函数计算免费额度：把 `super-source/functions/api.js` 的接口迁过去，
   `catalog.json`（6535 条片单）一并托管，再在配置里加一个站点指向新域名。
2. 重新在 EdgeOne 控制台生成 **Pages API Token**（注意不是腾讯云 API 凭证），用
   `npx edgeone pages deploy . -t <token>` 部署；或先在网页控制台手动上传一次激活项目。
3. Cloudflare Workers（免费，但国内访问不稳，不推荐）。

> 注意：腾讯云 API 的 `DeletePagesProject` 接口极危险，曾导致本项目域名被删，不要再用来探测。

## refresh_token.yml.off

原每小时刷新超级源 token 的工作流，已停用（文件后缀 `.off`，GitHub 不会执行）。
