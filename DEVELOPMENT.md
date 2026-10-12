# 开发日志

> 一个御主的自我修养：人可以睡，签不能断。

## 起源：一次断签惨案

每个 FGO 玩家都经历过这样的夜晚——刷完无限池倒头就睡，凌晨猛然惊醒：**今天没登录**。

几百天的连续登录记录，断了就是断了，从第 1 天重新来过。这种感觉比十连全是礼装还难受。

日服玩家早就有救星：[hexstr/FGODailyBonus](https://github.com/hexstr/FGODailyBonus)，GitHub Actions 定时签到，稳定运行多年。而国服玩家搜遍 GitHub，只有抓包工具、刷本脚本，唯独没有一个"开箱即用的云端签到方案"。

原因很快找到了：日服脚本的服务器写死 `game.fate-go.jp`，凭据是游戏内的 authKey/secretKey，而国服走的是**完全不同的B站账号体系**——这不是改个地址就能适配的，得重写。

| | 日服 | 国服（B服） |
|---|---|---|
| 登录方式 | 游戏内 authKey/secretKey 直登 | B站 OAuth access_token 授权 |
| 游戏服务器 | game.fate-go.jp | le1-bili-fate.bilibiligame.net |
| 客户端 | com.aniplex.fategrandorder | com.bilibili.fatego |
| 会话校验 | verCode + RSA 签名 | B站 token 校验 + md5(KEY+sgusk) |

既然没有轮子，那就自己造一个。

## 摸清国服的登录链路

国服协议没有官方文档，好在社区前辈已经把路趟出来了。[chaldea-center/chaldea](https://github.com/chaldea-center/chaldea) 的协议研究和 [AkatuRegumi/fgo-cn-toplogin-extractor](https://github.com/AkatuRegumi/fgo-cn-toplogin-extractor) 的 Python 实现，把完整的登录链路摆在了明处：

```
B站App扫码 → TV端 OAuth → access_token（passport.bilibili.com）
  → member.php                拿 gamedata（当前 dataVer）
  → logintomembercenter.php   用B站 token 换 rguid / rgusk
  → login.php                 建立游戏会话，得 sguid / sgusk
  → ac.php?_key=toplogin      usk = md5(FUNNY_KEY + sgusk)
                              ↑ 每日签到就藏在这最后一步
```

调研中最大的惊喜：Chaldea 维护着一个公共版本元数据接口（`data-cn.chaldea.center/gametop.json`），appVer、dataVer、unityVer 随游戏版本实时更新。日服脚本需要用户手动维护 `cfg.json` 的痛点，在国服直接被这个接口解决了——版本更新？不存在的，接口会出手。

另一个值得庆幸的发现：`toplogin` 的响应里什么都有——连续登录天数、等级、圣晶石、呼符、当日签到奖励明细。签到和报告一步到位，不用额外请求。

## 几个关键的设计决策

**扫码登录，而不是抓包或密码。**
国服登录绕不开B站授权。抓包要装证书配代理，门槛高；要密码更是想都别想。B站 TV 端的扫码 OAuth（`passport-tv-login`）是最优解：用户手机App扫一下、点确认，access_token 到手，全程不碰密码。安全性拉满，体验也最好。

**凭据与代码严格分离。**
`auth.json` 本地存、Secrets 云端存，`.gitignore` 从第一个 commit 就排除。凭据永远不进 git 历史，这是底线。

**先本地跑通，再上云。**
登录协议这种东西，本地都没签到成功就急着部署 Actions，等于拿着没强化满的队就冲高难本。本地验证 → 确认奖励到账 → 再谈自动化。

**token 自动续期（本项目的核心设计）。**
如果每次 token 过期都要重新扫码，那这脚本最多算"半自动"。实测B站 TV token 有效期 180 天，于是设计了这样的闭环：

```
每次运行时：token 剩余 < 30 天？
  → 是：自动刷新（有效期重置为 180 天）
  → CI 环境：用 PAT 把新凭据回写仓库 Secret
```

只要任务每天正常跑，token 就永远新鲜——**一次扫码，长期免维护**。

## 踩坑实录

### 坑一：B站 token 刷新端点（三连才中）

刷新端点没有任何文档，只能摸着石头过河：

```
POST /x/passport-tv-login/token/refresh   → 404，端点不存在
POST /api/oauth2/refreshToken              → -101 账号未登录
POST /api/oauth2/refreshToken              → code:0 ✓
  （关键：参数里同时带 access_token + refresh_token，
    用 TV 端 appkey/appsec 签名）
```

⚠️ 这里有个凶险的细节：**刷新成功的瞬间旧 token 立即作废**。拿到新 token 必须马上落盘/回写，慢一步凭据就断了——比手滑把芙芙喂错人还没法撤回。

### 坑二：PowerShell 的 UTF-8 BOM（本项目唯一的线上事故）

部署后第一次 Actions 运行直接报错：

```
json.decoder.JSONDecodeError: Unexpected UTF-8 BOM
```

排查发现：PowerShell 5.1 的 `[Text.Encoding]::UTF8` **自带 BOM**，凭据经 `Get-Content | gh secret set` 管道写入 secret 时，BOM 被一并带了进去，JSON 解析当场去世。

修复双管齐下：解析端 `lstrip('\ufeff')` 做防御性兼容；写入端改用 `New-Object System.Text.UTF8Encoding($false)`（无 BOM）。

**教训：在 Windows 上用 PowerShell 给程序喂数据，永远、永远用无 BOM 的 UTF-8。**

### 坑三：gh CLI 的 scope 陷阱

`gh auth login --with-token` 强制要求 PAT 带 `read:org` scope，而部署 PAT 只需 `repo` + `workflow`。解法：跳过 auth login，改用 `GH_TOKEN` 环境变量驱动 gh；git push 用一次性 `http.extraHeader` 凭据，token 不落盘到 `.git/config`。

## 为什么拆成两个仓库

公开仓库的 **Actions 日志是公开的**——每天签到的等级、石头数都会跟着日志一起裸奔。所以：

- **私有仓库**：跑自己的签到，日志私有，岁月静好
- **公开模板仓库**：纯代码 + 文档，workflow 默认禁用，fork 之后按 README 配置自己的 Secrets 即可

部署实例与开源模板分离，各干各的，互不干扰。

## 抓包炼金：从网络错误到青铜果实

签到稳定之后，需求开始膨胀：每次运行能不能顺手把多余的 AP 换成苹果？（树苗 + 40AP = 1 青铜果实，AP 满着不换，比抽卡歪了还亏。）

问题是：这个合成接口的 `_key` **没有任何公开文档**——登录链路有前辈趟过路，商店接口只能自己抓。于是搭了一套 mitmproxy 中间人环境：MuMu 模拟器 + adb root + 系统证书 + 代理转发，让游戏的所有 HTTPS 流量都先经过我这里。

游戏直接甩来一句"网络错误"。证书它不认。

### 坑四：证书格式（PEM，不是 DER）

网上教程清一色教你把证书转成 DER、命名 `<hash>.0`。推上去，游戏照样报错。拉下 Android 15 自带的系统证书一看——**人家是 PEM 格式**。文档抄的是上个时代的。

### 坑五：哈希算法（MD5，不是 RIPEMD160）

文件名的 `<hash>` 怎么算？OpenSSL 的 `subject_hash_old`（RIPEMD160）算出来 `3f201b09`，游戏不认。拿设备上一张**已知证书**反推：它的文件名是 `01419da9`，MD5 小端一算——精确命中。修正后 `c8750f0d.0`，一次到位。

> 调试玄学问题的正确姿势：别猜公式，找一个标准答案反着推。

### 坑六：命名空间套娃（最凶的一关）

证书装进 `/apex/.../cacerts`，`ls` 看得见，游戏还是不认。直到祭出 `/proc/<pid>/root` 才看清真相——**每个进程有自己的挂载命名空间**：

```
init ns      ✅ 有证书（adb 挂载在这）
zygote32 ns  ❌ 没有（我第一次装错了地方）
zygote64 ns  ❌ 没有 ← 游戏是 64 位，从这里 fork
应用 ns      ❌ fork 时复制父 zygote 的视图
```

`nsenter -t <zygote64> -m` 进去重装一遍，游戏重启后立刻握手成功——日志从"certificate unknown"变成一路 200 OK，比十连出金还舒坦。

### 抓到了什么

游戏内点开 AP 旁的加号，一次合成，请求响应全部到手：

```
请求: ac.php?_key=shoppurchase   id=13000000  num=1
响应: resCode=00  purchaseName="青铜果实"  PurchaseNum=1
      + 新会话种子 usk（下一次请求 = md5(FUNNY_KEY + 种子)）
```

关键发现：**usk 每次响应都会轮换**。登录链的 `md5(FUNNY_KEY + sgusk)` 公式在会话内继续生效，只是种子换成了响应里下发的新值——链式推进，断链即失效。这正是会话防重放的设计，也解释了为什么抓包必须拿到**响应体**。

### 落地

`FGO_APPLE_NUM` 环境变量/仓库 Variable 控制每次运行的合成数量：签到成功后逐个发送 `shoppurchase`，逐次轮换 usk，AP 或树苗不够时服务器返回错误码，优雅停止、绝不影响签到本身。本地实测 `1/1 个 (青铜果实)`，收官。

抓包环境（证书、代理、mitmproxy）随后已拆除——工具用完就收，留着中间人证书过年吗？

### 坑七：AP 满了才换，可脚本怎么知道 AP？

合成要花 AP，但响应里**没有**当前 AP 字段，只给了两个看似无关的数字：`actMax`（上限）和 `actRecoverAt`（回满时刻）。用游戏内显示的 `14/141` 一对照，公式当场标定：

```
AP = actMax - ceil((actRecoverAt - serverTime) / 300)
```

恢复周期 5 分钟 1 点，代入服务端数据 `141 - ceil(37989/300) = 14`——和游戏里显示的一模一样。`actRecoverAt` 已过期则直接是满值。

于是有了 `FGO_APPLE_AP_MIN`（默认 120）：AP 不到阈值就跳过本次合成，只打印一行"未达合成阈值"并附预计还需多久；设为 `max` 则阈值取 `actMax` 本身——**AP 满了才换**，升级涨上限后自动跟上。配合 workflow 里**每 3 小时**的静态调度——每轮合成花 40 AP、3 小时只回复 36 AP，会自动收敛到"合成几轮、歇一轮"的节奏，稳态约 7 个苹果/天（288 AP/天回复上限封顶）；玩家把 AP 花掉了，最迟 3 小时后下次运行自动回归正常节奏。比动态改写 cron 干净：没有提交噪音，调度自纠错。

### 坑八：GitHub 的 cron 会"看心情"跑

跑了一阵发现：GitHub 的 `schedule` 是**尽力而为**，会延迟、甚至整点漏跑（实测出现过 6 小时空档，连续登录没断是因为前几天的运行覆盖到了，但换苹果的及时性受影响）。

于是加了一个**外部定时器**兜底（cron-job.org 免费版）：每 3 小时（分钟设在 `:45`，错开 GitHub 自带的 `:17`）调用 GitHub API 触发一次 `workflow_dispatch`：

```
POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/checkin.yml/dispatches
Authorization: Bearer <PAT>
Accept: application/vnd.github+json
Body: {"ref":"main"}
```

要点：
- 脚本是**幂等**的（已签到会提示"今日已签到"，换苹果按 AP 判断），两套调度同时打也不会重复签到，谁先到谁生效。
- PAT 建议用 **fine-grained**，仅授权该仓库 + `Actions: Read and write`，别用全权限 classic token。
- 在定时服务里开启**失败邮件通知**，token 失效/接口变动能第一时间发现。

## 抓包炼金 II：剧情池每日免费单抽

国服十周年把常驻剧情池改成了**每天一次免费单抽**（到 12-31 为止）。签到顺手把这一抽用掉，
是"白嫖 165 抽"里最稳的一档 —— 需求自然就来了：把 `gachadraw` 这个接口挖出来。

### 没有文档，也没有报错信息 —— 那就自己造"探针"

国服所有动作都走 `ac.php?_key=<动作名>`，卡池相关接口没有任何公开资料。于是先摸清一件事：
**服务端的报错本身能不能当探针用？** 试出来的语义表（全部实测）：

| 请求 | 服务端响应 | 含义 |
|---|---|---|
| `_key=某不存在的动作` | `错误的请求，请重新登录` | 这个动作名不存在 |
| `_key=gachadraw` 不带 `id` | `参数错误` | 动作存在，但缺参数 |
| `_key=gachadraw&gachaId=…` | `参数错误` | 参数名不对（`gachaId` 被拒，说明要的是 `id`） |
| `_key=gachadraw&id=…&num=…` | `扭蛋ID有误` | 参数齐了，但该卡池当前不可抽 |
| `_key=shoppurchase&id=…&num=1` | `商品参数错误` / `00` | 对照：已知动作的参数校验风格一致 |

结论：**动作名是 `gachadraw`，参数是 `id` + `num`**（和 `shoppurchase` 同一风格，全小写、无分隔符）。
剩下的唯一问题是"当前可抽的卡池 id 是多少"，而这一点服务端一点信息都不给 ——
我把 id 从 1 扫到 9999999 的各个量级（含 `mstGacha` 里下发过的卡池、banner 里的 `SummonScene/<id>`、
账号 `userGacha` 里所有 99 个历史卡池）逐个试探，**全部回同一句 `扭蛋ID有误`**。
探针法到此为止：再往下就只能靠客户端手里那份主数据。

> 顺带踩到的坑：拿"已知动作 + 已知商品 id"当对照请求（本意是让它报错）时，**服务端照常执行了** ——
> 那次探测真的买掉了一个青铜果实（40AP + 1 树苗）。教训：拿真实接口做"对照实验"时，
> 参数必须全部换成不可能成功的值，否则它不会报错，它会照做。

### 转折：id 不用抓包也能拿到（用 userGacha 反推）

折腾了半天代理和证书，最后拿到 id 的方式却出乎意料地简单 —— **在游戏里手动点一次免费单抽，
然后读签到响应**：

```
userGacha 里 freeDrawAt = 你点抽的那一分钟 的那一行, 它的 gachaId 就是剧情池 id
  → gachaId=500139  freeDrawAt=2026-10-12 22:02:09  num=7
  → 同时 圣晶石 73 / 呼符 7 一点没变  ← 证明这次真的是免费抽
```

因为"每日免费单抽"必须在这个池子上留下免费抽的痕迹，而 `userGacha` 每次 `toplogin` 都全量下发 ——
等于服务端自己把答案送上门。代价：一次游戏内点击，零工具、零 root、零证书。

> 顺便记两条负面结论，免得后人重复踩：
> - `gachadraw` 用 `num=0` 当"卡池是否存在"的探针**无效**：无效 id 与真实 id 都回 `扭蛋ID有误`。
> - 参数名 `id` 才是被服务端接受的（写成标量 `gachaId` 直接 `参数错误`）；之前一路 `扭蛋ID有误`
>   的真正原因是**从没试过正确的 id**，而不是形状不对。

### 抓到了什么（以及没抓到什么）

`_key=gachadraw` 这条动作、以及"参数校验通过 / 卡池不可用"这两种报错的区分，都已在真机上标定：

```
POST ac.php?_key=gachadraw        id=<卡池ID>  num=<抽数>
  → 参数名不对(例如 gachaId 标量) → resCode=99 "参数错误"
  → 参数对但卡池当前不可抽/不存在 → resCode=99 "扭蛋ID有误" (fail.action=goto_title)
```

但**真正能被抽的卡池 id + 客户端实际参数形态**没能靠盲试逼出来，试过的都不行：

| 尝试 | 结果 |
|---|---|
| `mstGacha` 增量里下发的卡池 id（如福袋 59000003，已关闭） | 扭蛋ID有误 |
| `mstBanner` 里 `SummonScene/<id>`（含当时**生效中**的 6 个 banner id） | 扭蛋ID有误 |
| 账号 `userGacha` 全部 99 个历史卡池 id | 扭蛋ID有误 |
| 小 id 区间 1–5000、各量级 10^3…10^7 | 扭蛋ID有误 |
| `dataVer`/`version` 回拨（想骗服务端补发全量 `mst*`） | 986 可接受但增量不变；更旧直接 `resCode=88` |
| 参数形状：`gachaId[]`、`gachaId[]=[id,0]`、`type`/`gachaType`/`payType`/`shopId1`/`ticketItemId` 等组合 | 扭蛋ID有误 |

结论很朴素：**常驻池主数据不在签到响应里，服务端又不肯在报错里泄露任何可用 id**。
后来靠"游戏内点一次 + 读 `userGacha`"零成本拿到了 id（见上一节），
**为此写的整套抓包/验证工具用完就删了**（本仓库不是 git 仓库，删了就没有）：
mitmproxy addon、证书安装器、HAR 解析器、网络自检脚本等。

删掉之前把坑记在这儿，免得以后重踩：

| 坑 | 真相 |
|---|---|
| 模拟器设了代理就"没网"，浏览器却正常 | 证书只装进了**用户 store**；API 24+ 的 app 只信系统 CA |
| `su` 找不到 | MuMu 的 root 没开（开了之后 `u:r:ksu:s0`，是 KernelSU） |
| 证书装进 `/system/etc/security/cacerts` 没用 | Android 14+ 真正生效的是 `/apex/com.android.conscrypt/cacerts`，只读 loop 挂载，`mount -o rw,remount` 直接 `Invalid argument` |
| 装了证书还是握手失败 | **每个进程有独立挂载命名空间**；已经在跑的 app 必须重启才能继承，`zygote`/`zygote64` 要各自覆盖挂载 |
| `:pushservice` 一直报证书错误 | 它在补丁之前就启动了，继承了**旧的空挂载**；把游戏整个 force-stop 再启动才干净 |
| Windows 上 `mitmdump` 提示"无法识别" | pip 的 `Scripts` 目录不在 PATH，跟"没装"无关；另外别用管道跑它（`\| Select-Object` 会把它带走） |
| 游戏登录弹"前方拥堵" | 全量解密把客户端登录/SDK 链路拖坏了；**只拦游戏 API 域名**（`le*-fate`），其余明文直通即可 |
| PowerShell 的 `>` 存 PNG | 二进制被转码，图片直接坏掉；走管道读字节再写文件 |

> 探测成本记录：所有 id/形状探测**零消耗**（全部被服务端拒绝，呼符 7、圣晶石 73 未变）。
> 唯一那笔真实消耗是更早那条"对照请求"误触发的 1 个青铜果实（40AP + 1 树苗）。

请求参数形态仍然是**可配置**的（`gacha.json` 的 `id_param` / `extra`，或
`FGO_GACHA_ID_PARAM` / `FGO_GACHA_EXTRA`）—— 万一将来客户端换了字段名，不用改代码。

### 免费次数的判据：userGacha.freeDrawAt × userGame.zerotime

挖掘过程中确认了两张表的结构，它们就是"今天这一抽该不该抽"的全部依据：

```
mstGacha   (卡池主数据, 只在有变化时下发)
  id / name / type / freeDrawFlag / maxDrawNum / drawNum1 / shopId1 / ticketItemId /
  gachaGroupId / openedAt / closedAt / adjustId / pickupId / beforeGachaId ...

userGacha  (账号抽卡记录, toplogin 每次全量下发, 每个抽过的池一条)
  gachaId / num / tenNum / freeDrawAt / status
      ↑ freeDrawAt = 该卡池上一次"免费抽"的时间

userGame.zerotime = 今日 0 点重置时刻
```

于是有了判据：**`freeDrawAt < zerotime` ⟺ 今天的免费次数还没用**。
再加上卡池自身的 `freeDrawFlag`（免费池标记）和活动窗口，三条件同时成立才发抽卡请求。
抽完之后再从响应增量里核对一遍：`userItem(4001 呼符)` / `freeStone` 有没有减少，
减少了就说明这次不是免费抽，日志里直接告警。

### 落地

- 协议层 `fgo_cn.py`：`gacha_draw()`（`_key=gachadraw` + `id`/`num`）、
  `gacha_master_rows()` / `user_gacha_rows()`、`free_draw_state()`、`gacha_draw_result()`（结果解析 + 免费判定）。
- 工具 `gacha.py`：默认**只检查**，`--draw` 才真抽；`--id` / `FGO_GACHA_ID` / `gacha.json` 三处配置来源；
  `--list` 会把"今天哪个卡池用过免费抽"直接列出来 —— id 就是这么找到的。
- 离线自检 `selftest_gacha.py`：用合成响应把"定位卡池 / 免费判定 / 结果解析 / 绝不多发请求"全部跑一遍，
  不联网也能验证。
- 抓包工具链（mitmproxy addon / 证书安装器 / HAR 解析器 / 网络自检）确认结论后**已删除**，
  留下的只有上面那张坑表。

安全上的取向很明确：**宁可少抽一次，也不多花一颗石头**。所以默认只读、三条件强制、
事后核对资源、活动过期自动停手（默认 `2026-12-31`，届时本功能自己失效而不是继续花钱）。

## 许可证

协议实现参考了两个 AGPL-3.0 项目（Chaldea、fgo-cn-toplogin-extractor）的公开研究成果。尊重前辈，按规矩来：本项目以 **AGPL-3.0** 发布。

## 写在最后

每天北京时间 01:05，当你抱着芙芙进入梦乡，这个小脚本会在云端准时替你上线——领圣晶石、领呼符、续上连续登录天数，然后安静下线。

圣晶石会有的，呼符也会有的。断签？不会再有了。

祝各位御主十连满宝，无限池把把掉落礼装。

## 参考与致谢

- [hexstr/FGODailyBonus](https://github.com/hexstr/FGODailyBonus) —— 日服签到脚本，本项目的灵感来源
- [chaldea-center/chaldea](https://github.com/chaldea-center/chaldea) —— 国服协议研究 & 公共版本元数据接口
- [AkatuRegumi/fgo-cn-toplogin-extractor](https://github.com/AkatuRegumi/fgo-cn-toplogin-extractor) —— 国服 TopLogin 链路实现参考
