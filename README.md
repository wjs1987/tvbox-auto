# TVBox 自动维护仓库

每天凌晨（北京时间 3:00 左右）云端自动：检测源存活 → 测速 → 去重 → 生成干净配置 → 自动提交。
魔屏 OK影视只需填一次下面的地址，以后每次启动自动拉最新配置。

## 魔屏 OK影视 填这个地址（推完仓库后把 <用户名>/<仓库名> 换成你的）

```
首选（国内快）：https://cdn.jsdelivr.net/gh/<用户名>/<仓库名>@main/latest.json
备用（官方源）：https://raw.githubusercontent.com/<用户名>/<仓库名>/main/latest.json
```

## 首次搭建步骤（只需做一次）

1. **注册 GitHub**：https://github.com/signup （只需要邮箱）
2. **新建仓库**：登录后打开 https://github.com/new
   - Repository name：`tvbox-auto`（或任意名字）
   - 选择 **Public**（必须公开，私有仓库魔屏拉不到配置）
   - 不要勾选 README 初始化
3. **安装 Git**（本机没装过的话）：PowerShell 执行 `winget install --id Git.Git -e`，装完关掉重开 PowerShell
4. **推送本文件夹**：PowerShell 依次执行（第 4 步会弹浏览器让你登录 GitHub，点授权即可）

```powershell
cd "C:\Users\86186\WorkBuddy\2026-11-26-07-32-26\tvbox-auto"
git init -b main
git add -A
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/tvbox-auto.git
git push -u origin main
```

5. **验证**：打开仓库页面 → Actions 标签 → 选 "Auto Update TV Config" → Run workflow 手动触发一次
   → 几分钟后仓库根目录出现 `latest.json` 和 `report.md` 就成功了
6. **魔屏**：OK影视 → 设置 → 点播 → 粘贴上面的首选地址 → 确定

## 之后你什么都不用做

- 每天 3 点左右云端自动检测更新，源失效自动剔除、新源（以后加 --crawl）自动进来
- 想手动加/删源：改 `config/tv.json` → 推送 → 手动 Run workflow
- 想看检测结果：打开仓库里的 `report.md`
- 注意：Public 仓库内容任何人可见，介意配置内容公开的话请自行斟酌
