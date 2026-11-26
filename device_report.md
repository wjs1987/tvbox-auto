# 魔屏端到端实测报告

- 时间：2026-11-27 00:39:49
- 网络：家庭宽带（与魔屏同一出口），脚本 `device_test.py`

## 一、配置下载（latest.json，带时间戳穿透缓存）

| 链路 | 最快 | 中位 | 大小 | 成功/1 |
|---|---|---|---|---|
| jsdelivr | 3169ms | 3169ms | 193112B | 1/1 |
| raw | 10674ms | 10674ms | 193112B | 1/1 |
| gh-proxy | 4859ms | 4859ms | 193112B | 1/1 |

配置体积：188KB，站点 12 个，直播源 4 个

## 二、点播站点（家庭网络实测，复用 check_sources.py）

| 源 | 类型 | 列表 | 可播 | 延迟 | 说明 |
|---|---|---|---|---|---|
| 全网汇总 | 1 | OK | True | 428ms | JSON |
| 淘片 采集 | 1 | OK | True | 4181ms | JSON |
| 影视 建安 | 1 | OK | True | 913ms | JSON |
| 红牛资源(切) | 1 | OK | True | 2289ms | JSON |
| 影视 非凡[直连] | 1 | OK | True | 468ms | JSON |
| 非凡 资源 | 1 | × | None | -ms | 超时/连接失败 |
| JRKAN直播 | 3 | OK | None | 299ms | 规则文件可达 |
| 虎牙直播 | 3 | OK | None | 471ms | 规则文件可达 |
| 忍者 影视 | 3 | OK | None | -ms | 内置 CSP |
| 云盘搜索 | 3 | OK | None | -ms | 内置 CSP |
| SeedHub磁力 | 3 | OK | None | -ms | 内置 CSP |
| New6V磁力 | 3 | OK | None | -ms | 内置 CSP |

## 三、超级源与爬虫包

- `ac=list&pg=1` → OK 199ms list=40
- `ac=list&pg=1&t=c_movie` → OK 195ms list=40
- `ac=detail&ids=m22x04` → OK 747ms list=1
- spider jar：OK 214ms 1152135B 头=504b0304

## 四、直播频道抽样

- 抽样 60 个频道，列表可拉通 52 个（86%）

| 频道 | 结果 | 耗时 |
|---|---|---|
| 湖南卫视 | × | 54ms |
| 甘肃卫视 | OK | 72ms |
| 旅游卫视 | OK | 76ms |
| 东南卫视 | OK | 85ms |
| 少儿卫视 | OK | 86ms |
| 福建东南卫视 | OK | 89ms |
| 吉林卫视 | OK | 90ms |
| 南方卫视 | OK | 94ms |
| 广东南方卫视 | OK | 94ms |
| 上海卫视 | OK | 97ms |
| 兵团卫视 | OK | 107ms |
| 延边卫视 | OK | 119ms |
| 河北卫视 | OK | 132ms |
| 安多卫视 | OK | 185ms |
| 广西卫视 | OK | 187ms |
| 内蒙古卫视 | OK | 191ms |
| 海南卫视 | OK | 191ms |
| CCTV-13 新闻 | OK | 664ms |
| CCTV央视文化精品 | OK | 742ms |
| CCTV-7 国防军事 | OK | 746ms |

- 说明：只测列表可达，**不代表画面能解码播出**（HLS 需魔屏实播验证）
