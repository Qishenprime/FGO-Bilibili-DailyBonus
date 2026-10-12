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

> **（可选）开启剧情池每日免费单抽**：同页面 **Variables** 标签加 `FGO_GACHA_ID=500139` + `FGO_GACHA_DRAW=1` + `FGO_GACHA_ASSUME_FREE=1`。参数含义与完整清单见 [🎴 剧情池每日免费单抽](#-剧情池每日免费单抽可选)；不设 `FGO_GACHA_DRAW` 时只检查、不抽卡。

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

> 国服十周年活动：剧情召唤（常驻池）每天可免费单抽一次，截止 2026-12-31。开启后每次签到顺手把这一次用掉。

### 参数一览

| 参数 | 位置 | 建议值 | 说明 |
|---|---|---|---|
| `gacha_id` / `FGO_GACHA_ID` | 本地 `gacha.json` / 云端 Variable | `500139` | 剧情池卡池 ID（国服 2026-10） |
| `draw` / `FGO_GACHA_DRAW` | 同上 | `false` → `true` | **只有 `true` 才真正抽卡**；不设 = 只检查 |
| `num` / `FGO_GACHA_NUM` | 同上 | `1` | 抽数 |
| `assume_free` / `FGO_GACHA_ASSUME_FREE` | 同上 | `true` | 服务端不下发免费标记时也按免费池处理；**云端建议开启** |
| `free_until` / `FGO_GACHA_FREE_UNTIL` | 同上 | `2026-12-31` | 活动窗口，过期自动停手 |
| `id_param` | 同上 | `id` | 卡池 ID 的参数名，一般不用改 |
| `extra` / `FGO_GACHA_EXTRA` | 同上 | 空 | 额外请求参数 JSON，一般不用改 |

> `gacha_id` 需要更新时（换新剧情池），用 `python gacha.py --list` 取**今天有 `freeDrawAt` 记录的那一行 `gachaId`**。

### 本地开启

```powershell
Copy-Item gacha.example.json gacha.json   # 已按 500139 配好, draw=false
python gacha.py --list                    # 只读: 卡池 / 今日免费状态 / 呼符与圣晶石
python gacha.py                           # 只检查: 今天这一次能不能免费抽
python gacha.py --draw                    # 确认无误后再真抽(或把 gacha.json 的 draw 改成 true)
```

把 `draw` 改成 `true` 之后，`python checkin.py` 每次签到就会顺带用掉当天那次免费单抽。

### 云端开启

仓库 **Settings → Secrets and variables → Actions → Variables**：

| Variable | 值 |
|---|---|
| `FGO_GACHA_ID` | `500139` |
| `FGO_GACHA_DRAW` | `1` |
| `FGO_GACHA_ASSUME_FREE` | `1` |
| `FGO_GACHA_FREE_UNTIL` | `2026-12-31`（可省） |

建议**先只设 `FGO_GACHA_ID` + `FGO_GACHA_ASSUME_FREE`** 手动跑一次（此时只检查、不抽卡），
日志里看到 `免费单抽: 可用/跳过 (...)` 确认逻辑正常，再加上 `FGO_GACHA_DRAW=1`。

### 行为与安全

- 发抽卡请求前必须同时满足：**在活动窗口内** ∧ **该卡池是免费池**（`assume_free` 或服务端 `freeDrawFlag`）∧ **今天还没用过免费次数**（`userGacha.freeDrawAt` 早于今日重置时刻 `userGame.zerotime`）。任一不满足就只打印跳过原因、**不发请求**。
- 抽完会核对服务端增量：**若呼符/圣晶石变少，会打出「本次不是免费抽」的告警**（输出里的 `resource` 字段）。
- `FGO_GACHA_FREE_WINDOW=0`：关闭活动窗口校验（不推荐）；`FGO_GACHA_ID` 不设 = 该功能整体关闭。

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

**Q: 剧情池的卡池 ID 变了怎么办？**
A: 用 `python gacha.py --list` 看哪一行今天有 `freeDrawAt` 记录，那行的 `gachaId` 就是当前剧情池，更新 `FGO_GACHA_ID`（或 `gacha.json` 的 `gacha_id`）即可。

**Q: 跑脚本时游戏会掉线吗？**
A: 会。脚本登录会顶掉游戏会话，所以别在游戏在线时跑 `checkin.py` / `gacha.py`，反过来也一样。

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
