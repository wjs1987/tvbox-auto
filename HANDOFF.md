# 交接说明（当前状态 2026-11-28 · 已定稿）

这个仓库只有一件事：**每天自动产出一份能用的 OK影视 配置**。不需要本地电脑参与。

## 现在在跑什么

1. `build_live.py` → 抓 GitHub 上的 m3u 直播列表、归一化、分组、合并线路 → 写 `live.txt`
2. `check_sources.py --auto --top 50` → 爬 `sources_pool.json` 里的 GitHub 配置清单（多仓递归一层）
   → 提取源 → 并发测活/测速/试播 → 剔除 `HOME_DEAD_APIS` → 排序取 50 → 写 `latest.json` + `report.md`
3. Actions 自动提交；提交时用 `git pull --rebase -X ours` 重试 5 次防并发冲突
4. 收尾 purge jsDelivr 的 `latest.json` / `live.txt` 缓存

定时：`.github/workflows/update.yml`，`cron: 0 19 * * *`（UTC）= 每天 03:00 北京时间；
也可以手动 `workflow_dispatch`，或直接 push 触发。

## 配置里是什么

- `sites`：50 个流水线精选的 CMS 采集站 + 2 个固定磁力站（SeedHub、New6V）
- `quickSearch`：只在最快 6 个纯 CMS 源上开（1GB 内存盒子并发搜索扛不住更多）
- `lives`：`自建直播` 指向 `live.txt`（外链），另加虎牙/斗鱼/YY 三个固定轮播 m3u
- `spider`：`libs/spider.png`（jsDelivr），客户端按字节和 md5 校验

## 已彻底删除，不要试图复活

| 功能 | 状态 |
|---|---|
| 全网汇总 / 超级源（EdgeOne 边缘函数、网盘播放、panck Cookie） | 代码与目录已删除，源站也已被删 |
| EdgeOne token 续签工作流 | 已删除 |
| 国内节点探测（EdgeOne 中转） | 已删除，只靠 `HOME_DEAD_APIS` 兜底 |
| 网盘站（云盘搜索 / 我的云盘扫码登录 / 玩偶哥哥4K） | 已删除，网盘爬虫包 `libs/fty_spider.png` 一并删除 |
| 非 GitHub 源地址（gitee、ztha.top、nxog、qiaoji8、盒子迷、zbds 等） | 已删除，源池只留 GitHub |
| `device_test.py`（家宽端到端实测脚本） | 已删除（结论已定：瓶颈在 App 首屏） |

## 已知问题

- **冷启动 12~13 秒**：网络只占 1.5 秒，其余是 OK影视 首屏并发拉取与渲染。
  已排除配置体积、镜像速度、jar 预下载；实测更换默认站点最多省 0.5 秒，属 App 侧，无解。
- **海外机房测速不准**：`report.md` 里的延迟只作筛选参考，真实体验以魔屏为准。
- 直播频道在海外机房探测常显示"存活 0"，属正常（国内 IPTV 海外连不上），以设备实测为准。

## 增删源怎么做

- 加清单仓库：编辑 `sources_pool.json`，加一行 GitHub raw 地址
- 屏蔽某个具体源：把它的完整 API 加进 `check_sources.py` 的 `HOME_DEAD_APIS`
- 调数量：改 workflow 里的 `--top 50`（越大首页越重、加载越慢）
