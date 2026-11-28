# 交接说明（当前状态 2026-11-28）

这个仓库只有一件事：**每天自动产出一份能用的 OK影视 配置**。不需要本地电脑参与。

## 现在在跑什么

1. `build_live.py` → 抓直播频道、归一化、分组、合并线路 → 写 `live.txt`
2. `check_sources.py --auto --top 50` → 爬 12 个聚合站（`sources_pool.json`）→ 提取源 →
   并发测活/测速/试播 → 剔除 `HOME_DEAD_APIS` → 排序取 40 → 写 `latest.json` + `report.md`
3. Actions 自动提交；每次提交会用 `git pull --rebase -X ours` 重试 5 次防并发冲突
4. 收尾 purge jsDelivr 的 `latest.json` / `live.txt` 缓存

定时：`.github/workflows/update.yml`，`cron: 0 19 * * *`（UTC）= 每天 03:00 北京时间；
也可以手动 `workflow_dispatch`，或直接 push 触发。

## 配置里是什么

- `sites`：50 个由流水线精选 + 5 个固定自建站（云盘搜索、SeedHub磁力、New6V磁力、我的云盘扫码、玩偶哥哥4K）
- `quickSearch`：只在最快 6 个纯 CMS 源上开（1GB 内存盒子并发搜索扛不住更多）
- `lives`：`自建直播` 指向 `live.txt`（外链），另加虎牙/斗鱼/YY 三个 m3u
- `spider`：`libs/spider.png`（jsDelivr），客户端按字节和 md5 校验

## 已下线，不要试图复活

| 功能 | 状态 |
|---|---|
| 全网汇总 / 超级源（EdgeOne 边缘函数、网盘播放、panck Cookie 管理） | 已归档到 `archive/super-source/`，代码路径已移除 |
| EdgeOne token 每小时续签 | `archive/refresh_token.yml.off`，已停用 |
| 国内节点探测（EdgeOne 中转） | 已移除，改由 `HOME_DEAD_APIS` 黑名单兜底 |
| `edge_probe.py`（依赖 EdgeOne 的家宽探测脚本） | 已归档到 `archive/manual-tools/` |
| 网盘播放配置文档 | 已归档到 `archive/` |

## 已知问题

- **冷启动抖动**：最好 10~12 秒，但偶发 20~70 秒，卡在"品牌名+转圈"。
  已排除配置体积（18KB）、镜像速度（curl 0.3~0.6s）、jar 预下载；
  怀疑是 OK影视 首拉失败后的自身重试逻辑，非配置侧问题，暂未根治。
- **海外机房测速不准**：`report.md` 里的延迟只作筛选参考，真实体验以魔屏为准。
  需要精测时在家跑 `device_test.py`。
- 直播频道在海外机房探测常显示"存活 0"，属正常（国内 IPTV 海外连不上），以设备实测为准。

## 增删源怎么做

- 加聚合站：编辑 `sources_pool.json`，加一行 url
- 屏蔽某个具体源：把它的完整 API 加进 `check_sources.py` 的 `HOME_DEAD_APIS`
- 调数量：改 workflow 里的 `--top 50`（越大首页越重、加载越慢）
