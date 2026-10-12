# FGO-Bilibili-DailyBonus

**Fate/Grand Order 国服（B服）每日自动签到脚本** —— GitHub Actions 云端定时运行，B站 token 自动续期，一次扫码长期免维护。

![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![Platform](https://img.shields.io/badge/platform-%E5%AE%89%E5%8D%93B%E6%9C%8D%20%7C%20iOSB%E6%9C%8D-orange)

## ✨ 特性

- 🗓️ **每日自动签到**：通过 GitHub Actions 定时执行 `toplogin`，无需自己的服务器，无需电脑开机
- 🍎 **自动苹果合成**（可选）：每次运行顺带把 AP 换成青铜果实（树苗 + 40AP），设置仓库 Variable `FGO_APPLE_NUM` 即可开启
- 🎴 **剧情池每日免费单抽**（可选）：签到后顺带把当天那次免费单抽用掉（`_key=gachadraw`），只在能证明是免费次数时才出手，动过资源会告警
- 🔄 **token 自动续期**：B站 access_token 有效期 180 天，剩余不足 30 天时自动刷新并回写仓库 Secrets，**一次扫码，长期免维护**
- 📊 **签到报告**：输出御主等级、圣晶石、呼符、连续/累计登录天数、当日签到奖励明细
- 📱 **双平台**：支持安卓 B服、iOS B服
- 💻 **也可纯本地运行**：不依赖 GitHub，配合 Windows 任务计划即可本地定时签到（国内 IP，风控风险更低）

## 🔧 原理

FGO 国服使用B站账号体系登录，签到链路为：

```
B站App扫码 → 获取 OAuth access_token
    → member (gamedata) → logintomembercenter (B站授权校验)
    → login (建立游戏会话) → toplogin ← 这一步即每日签到
                            → (可选) shoppurchase ← AP→青铜果实 合成
                            → (可选) gachadraw   ← 剧情池每日免费单抽
```

> 本项目灵感来自日服签到脚本 [hexstr/FGODailyBonus](https://github.com/hexstr/FGODailyBonus)。
> 由于日服与国服的登录协议、加密、服务器完全不同，本项目是针对国服的独立实现。
> 有兴趣了解从零调研到部署上线的全过程？请看 [📖 开发日志](DEVELOPMENT.md)。

## 🚀 快速开始

### 1. Fork 本仓库

点击右上角 **Fork**。

### 2. 本地扫码，获取登录凭据

需要 **Python 3.10+**：

```bash
pip install -r requirements.txt
python login_qr.py run "你的FGO御主名"
```

用手机 **Bilibili App** 扫描终端里的二维码并确认登录（⚠️ 确认App当前登录的是**绑定FGO账号的那个B站号**）。成功后凭据保存到本地 `auth.json`。

> `auth.json` 等同账号密码，请勿分享、勿提交到任何仓库（已在 .gitignore 中排除）。

### 3. 在 Fork 的仓库中设置 Secrets

进入你 Fork 的仓库 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret | 值 | 说明 |
|---|---|---|
| `FGO_AUTH_JSON` | `auth.json` 文件的**全部内容** | B站登录凭据 |
| `FGO_UPDATE_PAT` | [创建链接](https://github.com/settings/tokens) | classic PAT，勾选 `repo` 权限，用于 token 续期后自动回写 Secrets |

> **（可选）开启自动苹果合成**：同页面切到 **Variables** 标签 → **New repository variable**，添加 `FGO_APPLE_NUM` = 每次运行合成的数量（如 `1`）。不设置或设为 `0` = 关闭。可再添加 `FGO_APPLE_AP_MIN`：**AP 达到该阈值才开始合成**，默认 `120`，设为 **`max`** 则 **AP 满了才换**（自动等于上限，升级后自适应）；低于阈值本次跳过、不影响签到。

> **（可选）开启剧情池每日免费单抽**：再加三个 Variable —— `FGO_GACHA_ID` = 剧情池卡池 ID（国服 2026-10 为 `500139`，获取办法见 [🎴 那一节](#-剧情池每日免费单抽可选)）、`FGO_GACHA_DRAW` = `1`（**不设它只做检查、不抽卡**）、`FGO_GACHA_FREE_UNTIL` = 活动截止日（默认 `2026-12-31`，过期自动停手）。想先看看效果：本地跑 `python gacha.py --list`。

### 4. 启用 Actions 并验证

进入你 Fork 的仓库 → **Actions** → 启用 → 选择 `FGO B服每日签到` → **Run workflow** 手动触发一次，确认日志输出 `签到成功`。

### 5. 定时运行

默认每天 **北京时间 01:05**（UTC 17:05）自动签到（FGO 每日 0 点刷新后），另有**每 3 小时**一次的检查：若开启了苹果合成且 AP ≥ `FGO_APPLE_AP_MIN`（默认 120）就合成一次，AP 恢复不到阈值则秒过。如需修改，编辑 `.github/workflows/checkin.yml` 中的 `cron`。

> 注意：GitHub 定时任务在高峰期可能延迟数十分钟，属正常现象。

## 💻 纯本地运行（可选，不用 GitHub）

完成上面第 2 步后：

```bash
python checkin.py
```

配合 **Windows 任务计划程序**每天定时执行即可。本地运行使用国内 IP，相比 GitHub Actions 的海外 IP 更不容易触发风控。

本地想顺便合成苹果：`FGO_APPLE_NUM=1 python checkin.py`（Windows PowerShell 写法：`$env:FGO_APPLE_NUM="1"; python checkin.py`）。

## 🎴 剧情池每日免费单抽（可选）

> 国服十周年活动：剧情召唤（常驻池）每天一次免费单抽，截止 2026-12-31。本功能把这一次顺手用掉。

### 1) 先拿到卡池 ID（最省事的办法：不用抓包）

**在游戏里手动点掉今天那次免费单抽**，然后跑一条只读命令：

```powershell
python gacha.py --list
```

`userGacha` 里**今天有 `freeDrawAt` 记录的那一行，它的 `gachaId` 就是剧情池 ID**（因为免费单抽会把这个字段更新成你点抽的时刻）：

```
gachaId=500139   freeDrawAt=2026-10-12 22:02:09   num=7 tenNum=0 status=0   ← 就是它
```

> 国服 2026-10 的剧情池 id 是 **500139**（已实测：在该池抽免费单抽后，圣晶石与呼符数量都没变）。
> 这个办法比抓包省事得多——不用 root、不用证书、不碰代理，一次游戏内点击就够。

拿到后本地验证一次（**只检查、不抽卡**）：

```bash
python gacha.py --list                # 看卡池主数据 / 今日免费状态 / 呼符与圣晶石
python gacha.py                       # 只检查: 今天这一次能不能免费抽
python gacha.py --draw                # 确认无误后再真正抽 (或把 gacha.json 里 draw 改成 true)
```

把 id 写进配置（直接手写一份 `gacha.json` 即可）：

```json
{ "gacha_id": 500139, "id_param": "id", "assume_free": true, "draw": false, "free_until": "2026-12-31" }
```

### 2) 打开自动抽

- 本地：把 `gacha.json` 里的 `"draw"` 改成 `true`，之后 `python checkin.py` 每次签到顺带用掉免费单抽；或
  `$env:FGO_GACHA_ID="500139"; $env:FGO_GACHA_DRAW="1"; python checkin.py`
- 云端：仓库 Variables 里设 `FGO_GACHA_ID=500139` + `FGO_GACHA_DRAW=1`（+ 可选 `FGO_GACHA_FREE_UNTIL`）

### 3) 安全设计（为什么它不会乱花你的石头）

真正抽卡前必须同时满足：**活动窗口内**（默认到 2026-12-31）∧ **卡池 `freeDrawFlag != 0`**
∧ **`userGacha.freeDrawAt` 早于今日重置时刻 `userGame.zerotime`**（即今天还没用过免费次数）。
任一条件不满足就只打印跳过原因、不发抽卡请求。抽完之后还会核对服务端增量：**如果呼符/圣晶石
变少了，会明确打出「本次不是免费抽」的告警**（输出里的 `resource` 字段）。

- `FGO_GACHA_ASSUME_FREE=1`：主数据里看不到 `freeDrawFlag` 时也认为它是免费池 —— 仅在已实测确认该 id 就是活动免费池时使用。
- `FGO_GACHA_FREE_WINDOW=0`：关闭活动窗口校验（不推荐）。
- 只想检查不动手：`FGO_GACHA_DRAW` 不设即可。

### 4) 抓包结论（工具已删除，结论保留）

排查期间写过一整套抓包/验证工具（mitmproxy addon、证书安装器、HAR 解析器等），**确认结论后已全部删除**。
有用的东西留在这里：

- 请求是 `ac.php?_key=gachadraw`，参数名是 **`id`** + `num`；写成标量 `gachaId` 服务端直接回 `参数错误`。
- 对任何"当前不可抽"的 id 一律只回 `扭蛋ID有误`，**零信息量** —— 别指望用报错反推 id；`num=0` 也不能当存在性探针（无效 id 与真实 id 回同一句话）。
- 战斗类动作确实存在（实测 `battlesetup` / `battleresult`），但逐回合指令、战斗种子、主数据都要自己啃，所以推图没走 API 这条路。
- Android 14+ 的系统 CA 在 `/apex/com.android.conscrypt/cacerts`（只读 loop 挂载），"设置→安装证书"装的是**用户 store**，API 24+ 的 app 不信；要给 app 用必须 root + 在 zygote 命名空间里覆盖挂载（重启即失效）。
- 以后真要再抓：`pip install mitmproxy` 自己写个 addon 记 `ac.php`，**并且只拦游戏 API 域名**（`le*-fate` / `line*-s*-bili-fate`），其余一律明文直通 —— 全量解密很容易把客户端登录/SDK 链路拖坏，游戏会弹"前方拥堵"。
- **刷图或抓包期间别跑 `checkin.py` / `gacha.py`**：脚本登录会把游戏会话顶掉，游戏直接掉线。

## 🔄 token 自动续期说明

- B站 TV 端 token 有效期 **180 天**
- 每次运行时检查：剩余不足 30 天则自动刷新，有效期重置为 180 天
- CI 环境下新 token 通过 `FGO_UPDATE_PAT` 自动回写 Secrets
- 理论上只要任务每天正常运行，**永远不需要再次扫码**

## ❓ 常见问题

**Q: 签到失败，提示风控/授权失败？**
A: GitHub Actions 服务器在海外，B站可能对异地登录风控。建议改用纯本地运行方式。

**Q: token 过期/失效了？**
A: 重新执行 `python login_qr.py run "御主名"` 扫码，把新的 `auth.json` 内容更新到 Secret `FGO_AUTH_JSON`。

**Q: iOS B服怎么用？**
A: 扫码时加个 `ios` 参数即可：`python login_qr.py run "御主名" ios`，其余流程完全相同。

**Q: 签到日志里没有奖励明细？**
A: 说明当天已经登录过游戏（奖励每天只能领一次），属于正常现象，第二天再看。

**Q: 自动苹果合成消耗什么？会失败吗？**
A: 每个果实消耗 1 个树苗 + 40 AP。仅当 AP ≥ `FGO_APPLE_AP_MIN`（默认 120，设 `max` 则满了才换）时才动手，低于阈值本次直接跳过并提示原因、附预计还需多久达到阈值；即便动手了，AP 或树苗不足时服务器返回错误，脚本会提示"未完成"但**不影响签到结果**。数量由 `FGO_APPLE_NUM` 控制，删除该 Variable 即关闭。

**Q: 会封号吗？**
A: 无法承诺。第三方模拟客户端登录存在触发风控/违反用户协议的可能，请仅用于自己的账号并自行评估风险。

**Q: 免费单抽会花掉我的圣晶石/呼符吗？**
A: 默认不会：不设 `FGO_GACHA_DRAW=1` 时只检查不抽；开了之后也要同时满足「活动窗口内 + 卡池标记为免费 + 今日免费次数没用过」才会发抽卡请求，抽完还会核对资源是否真的没变。真要出问题也是"少抽一次"而不是"多花石头"。

**Q: 为什么非要抓包拿卡池 ID，不能自动识别吗？**
A: 常驻剧情池没变化时服务端不会重复下发它的 `mstGacha` 主数据，签到响应里通常看不到它；而对不可用的 id 服务端只回 `扭蛋ID有误`，等于没有信息量。所以第一次必须抓一次真实单抽拿到 id；之后本功能会把它写死用，同时仍然按"今日免费次数是否用过"来判断该不该抽。

## ⚠️ 免责声明

- 本项目为**非官方**第三方开源工具，与 bilibili、Fate/Grand Order、Aniplex、Lasengle 等无任何隶属、授权或合作关系
- 使用本项目可能违反相关平台服务条款，存在账号被限制/封禁的风险，**使用后果请自负**
- 本项目不承诺"绝不触发风控"或"绝不封号"
- 请勿将本项目用于任何商业用途，仅供学习交流

## 🙏 致谢

- [hexstr/FGODailyBonus](https://github.com/hexstr/FGODailyBonus) —— 日服签到脚本，本项目的灵感来源
- [chaldea-center/chaldea](https://github.com/chaldea-center/chaldea) —— FGO 国服登录协议研究参考
- [AkatuRegumi/fgo-cn-toplogin-extractor](https://github.com/AkatuRegumi/fgo-cn-toplogin-extractor) —— 国服 TopLogin 链路实现参考

## 📄 License

[GNU Affero General Public License v3.0](LICENSE)

本项目参考了上述 AGPL-3.0 许可项目的协议研究成果，依其许可证要求以 AGPL-3.0 发布。
