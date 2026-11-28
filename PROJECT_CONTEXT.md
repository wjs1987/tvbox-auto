# 项目上下文（当前版）

> 2026-11-28 重写。旧版描述里的超级源/EdgeOne/国内探测均已下线，历史细节见 `archive/`。

## 一句话

用 GitHub Actions 每天从多个公开聚合配置里爬源、测活、测速，产出一份纯静态的 `latest.json`，
供家里天猫魔屏（`com.fongmi.android.tv`，OK影视）直接拉取使用。全免费、纯云端。

## 组成

| 组件 | 位置 | 作用 |
|---|---|---|
| 源地址池 | `sources_pool.json` | 12 个聚合配置站，流水线从这里爬 |
| 种子配置 | `config/tv.json` | 参数沿用（ads 黑名单等）+ 兜底源 + 直播频道原料 |
| 主流水线 | `check_sources.py` | 爬取 → 去重 → 测活/测速/试播 → 排序精选 → 写 `latest.json`、`report.md` |
| 直播整理 | `build_live.py` | 抓频道 → 归一化分组 → 多线路合并 → 写 `live.txt` |
| 定时任务 | `.github/workflows/update.yml` | 每天 03:00 北京时间 + push 触发；跑完提交并 purge jsDelivr |
| 家庭实测 | `device_test.py` | 家宽端到端测（同出口才有意义），产出 `device_report.md` |
| 自托管 jar | `libs/*.png` | spider/网盘爬虫包，jsDelivr `.png` 直链分发 |
| 归档区 | `archive/` | 超级源、EdgeOne 部署物料、旧探测脚本、网盘文档，流程不读 |

## 客户端地址

```
https://gh-proxy.com/https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json
```

备用：`https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json`、raw 直连。

## 产物规格（2026-11-28 实测）

- `latest.json`：约 21KB、48 站（40 精选 + 8 固定自建）、quickSearch 6 个、直播 4 条（1 外链 + 3 m3u）
- `live.txt`：约 104KB、1188 行、9 个分组
- 冷启动：最好 10~12 秒；偶发抖动见 HANDOFF 的已知问题

## 不要做

- 不要把直播频道内联回 `latest.json`（会从 20KB 涨到 400KB，冷启动显著变慢）
- 不要改站点 `key` 生成规则（会让客户端收藏/进度全部失效）
- 不要把 jar 直连 GitHub raw（2.7MB 会被重置，必须走 jsDelivr `.png`）
- 不要重新引入 EdgeOne/超级源（已明确放弃，相关代码已删除）
