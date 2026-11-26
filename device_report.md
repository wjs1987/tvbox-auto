# 魔屏端到端实测报告

- 时间：2026-11-27 00:55:35
- 网络：家庭宽带（与魔屏同一出口），脚本 `device_test.py`

## 一、配置下载（latest.json，带时间戳穿透缓存）

| 链路 | 最快 | 中位 | 大小 | 成功/1 |
|---|---|---|---|---|
| jsdelivr | 2846ms | 2846ms | 193112B | 1/1 |
| raw | 2355ms | 2355ms | 192195B | 1/1 |
| gh-proxy | 2403ms | 2403ms | 192195B | 1/1 |

配置体积：188KB，站点 12 个，直播源 4 个

## 二、点播站点（家庭网络实测，复用 check_sources.py）

| 源 | 类型 | 列表 | 可播 | 延迟 | 说明 |
|---|---|---|---|---|---|
| 全网汇总 | 1 | OK | True | 200ms | JSON |
| 淘片 采集 | 1 | OK | True | 3434ms | JSON |
| 影视 建安 | 1 | OK | True | 1927ms | JSON |
| 红牛资源(切) | 1 | OK | True | 818ms | JSON |
| 影视 非凡[直连] | 1 | OK | True | 310ms | JSON |
| 非凡 资源 | 1 | × | None | -ms | 超时/连接失败 |
| JRKAN直播 | 3 | OK | None | 320ms | 规则文件可达 |
| 虎牙直播 | 3 | OK | None | 601ms | 规则文件可达 |
| 忍者 影视 | 3 | OK | None | -ms | 内置 CSP |
| 云盘搜索 | 3 | OK | None | -ms | 内置 CSP |
| SeedHub磁力 | 3 | OK | None | -ms | 内置 CSP |
| New6V磁力 | 3 | OK | None | -ms | 内置 CSP |

## 三、超级源与爬虫包

- `ac=list&pg=1` → OK 129ms list=40
- `ac=list&pg=1&t=c_movie` → OK 163ms list=40
- `ac=detail&ids=m22x04` → OK 984ms list=1
- spider jar：OK 234ms 1152135B 头=504b0304

## 四、直播频道抽样

- 抽样 60 个频道，列表可拉通 51 个（85%）

| 频道 | 结果 | 耗时 |
|---|---|---|
| 湖南卫视 | × | 47ms |
| 广东南方卫视 | OK | 62ms |
| 广西卫视 | OK | 65ms |
| 福建东南卫视 | OK | 68ms |
| 兵团卫视 | OK | 71ms |
| 延边卫视 | OK | 76ms |
| 旅游卫视 | OK | 77ms |
| 少儿卫视 | OK | 82ms |
| 河北卫视 | OK | 86ms |
| 甘肃卫视 | OK | 86ms |
| 吉林卫视 | OK | 89ms |
| 东南卫视 | OK | 105ms |
| 上海卫视 | OK | 107ms |
| 南方卫视 | OK | 110ms |
| 内蒙古卫视 | OK | 126ms |
| 安多卫视 | OK | 181ms |
| 海南卫视 | OK | 198ms |
| 人间卫视 | OK | 607ms |
| CCTV-13 新闻 | OK | 679ms |
| 河南卫视 | OK | 729ms |

- 说明：只测列表可达，**不代表画面能解码播出**（HLS 需魔屏实播验证）
