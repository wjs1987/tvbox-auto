# TVBox 自动维护仓库

**全自动流水线（纯云端，不依赖本地电脑）：**
每天凌晨 3 点（北京时间）+ 每次推送代码时，GitHub Actions 自动执行：

```
爬取 18 个聚合配置站（含每日更新的 tvbox-ysc-config 聚合仓库、各大仓直连）
→ 提取点播源 + 直播频道 + 直播源引用
→ 按 (type, api) 去重
→ 并发测试：存活检测 + 延迟测速 + 质量评分（片单数据量）
→ 只保留合格源，按速度排序 → 生成 latest.json
→ 自动提交回仓库
```

## 魔屏 OK影视 配置地址

```
首选（国内快）：https://cdn.jsdelivr.net/gh/WJS1987/tvbox-auto@main/latest.json
备用（官方源）：https://raw.githubusercontent.com/WJS1987/tvbox-auto/main/latest.json
```

## 首次搭建步骤（只需做一次）

1. **注册 GitHub**：https://github.com/signup
2. **新建仓库**：https://github.com/new — 名称 `tvbox-auto`，必须 **Public**
3. **安装 Git**：`winget install --id Git.Git -e`（已装跳过）
4. **推送本文件夹**：

```powershell
cd "C:\Users\86186\WorkBuddy\2026-11-26-07-32-26\tvbox-auto"
git init -b main
git add -A
git commit -m "init"
git branch -M main
git remote add origin https://github.com/WJS1987/tvbox-auto.git
git push -u origin main   # 弹出授权窗口时点授权
```

5. **验证**：推送会自动触发一轮流水线，打开仓库 Actions 标签看运行状态；
   成功后仓库根目录出现 `latest.json`（魔屏配置）和 `report.md`（检测报告）
6. **魔屏**：OK影视 → 设置 → 点播 → 粘贴上面的首选地址 → 确定

## 日常维护

- 什么都不用做：每天云端自动爬取+检测+更新
- 想增删聚合配置站：编辑 `sources_pool.json`（找到新聚合地址加一行即可）
- 你的旧配置 `config/tv.json` 仍会作为兜底来源参与检测，参数（ads 黑名单等）沿用
- 本地手动跑一次：`python check_sources.py --auto --limit 20`（限 20 个源快速试），
  全量跑：`python check_sources.py --auto`
- 注意：Public 仓库内容任何人可见
