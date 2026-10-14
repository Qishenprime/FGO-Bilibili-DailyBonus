# -*- coding: utf-8 -*-
"""FGO 国服(B服) 登录/签到协议实现。

链路: gamedata(member) -> logintomembercenter -> login -> toplogin
协议参考(公开研究):
  - Chaldea  https://github.com/chaldea-center/chaldea (AGPL-3.0)
  - fgo-cn-toplogin-extractor  https://github.com/AkatuRegumi/fgo-cn-toplogin-extractor (AGPL-3.0)
仅供个人账号使用。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from typing import Any
from urllib.parse import unquote

import requests

# ---- B站 TV 端 OAuth(扫码登录用) ----
TV_APPKEY = "4409e2ce8ffd12b8"
TV_APPSEC = "59b43e04ad6965f34319062b478f83dd"
TV_AUTH_URL = "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code"
TV_POLL_URL = "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll"
BILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# ---- FGO 国服元数据 ----
CHALDEA_GAMETOP_URLS = (
    "https://data-cn.chaldea.center/gametop.json",
    "https://data.chaldea.center/gametop.json",
)
FGO_CONFIG_URL = "https://static.biligame.com/config/fgo.config.js"

DEVELOPMENT_AUTH_CODE = "aK8mTxBJCwZyxBjNJSKA5xCWL7zKtgZEQNiZmffXUbyQd5aLun"
FUNNY_KEY = "B5UI78B3486A7B48IB9AUF8E8P97CPI9"
DEFAULT_UNITY = "2022.3.62f2"
TIMEOUT = (12, 35)
APPLE_SHOP_ID = 13000000  # 主界面 AP 加号: 树苗+40AP -> 青铜果实 (shoppurchase)
APPLE_COST = 40           # 一次合成消耗 AP (另需树苗)
AP_RECOVER_SECONDS = 300  # AP 每 300 秒回复 1 点 (已实测确认)
GAMETOP_CACHE = "gametop_cache.json"   # 版本元数据本地缓存(元数据站点抖动时兜底)
GAMETOP_CACHE_MAX_AGE = 7 * 86400      # 缓存最多用 7 天(版本太旧登录会被服务端拒)


def domestic_session() -> requests.Session:
    """国内域名(B站 / chaldea 元数据)专用会话: **忽略系统代理**。

    本机开着 Clash 之类并启用"系统代理"时, requests 会把这些国内域名也塞进代理,
    实测会 SSL EOF / 超时, 整条签到直接失败(CI 上没有代理, 所以只在本地复现)。
    """
    s = requests.Session()
    s.trust_env = False
    return s


PLATFORMS: dict[str, dict[str, Any]] = {
    "android_bili": {
        "label": "安卓 B服",
        "host": "https://le1-bili-fate.bilibiligame.net",
        "rkchannel": 24,
        "cPlat": 3,
        "uPlat": 3,
        "is_android": True,
        "os": "Android OS 7.1.2 / API-25 (N2G48C/4565141)",
        "ptype": "vivo V1938CT",
    },
    "ios_bili": {
        "label": "iOS B服",
        "host": "https://le1-ios-fate.bilibiligame.net",
        "rkchannel": 996,
        "cPlat": 2,
        "uPlat": 2,
        "is_android": False,
        "os": "iPadOS 15.2",
        "ptype": "iPad7,3",
    },
}


class FgoError(RuntimeError):
    pass


# ================= B站扫码授权 =================

def _sign_tv(params: dict[str, Any]) -> dict[str, str]:
    p = {k: str(v) for k, v in params.items()}
    query = "&".join(f"{k}={p[k]}" for k in sorted(p))
    p["sign"] = hashlib.md5((query + TV_APPSEC).encode("utf-8")).hexdigest()
    return p


def _passport_session() -> requests.Session:
    s = domestic_session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    })
    return s


def create_qr() -> tuple[str, str]:
    """申请扫码登录二维码, 返回 (auth_code, qr_url)。"""
    params = _sign_tv({"appkey": TV_APPKEY, "local_id": 0, "ts": int(time.time())})
    r = _passport_session().post(TV_AUTH_URL, data=params, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise FgoError(f"二维码申请失败: code={payload.get('code')} message={payload.get('message')}")
    data = payload["data"]
    auth_code, qr_url = str(data.get("auth_code") or ""), str(data.get("url") or "")
    if not auth_code or not qr_url:
        raise FgoError("二维码响应缺少 auth_code/url")
    return auth_code, qr_url


def poll_qr(auth_code: str, status_cb=None, timeout_seconds: int = 180) -> dict[str, Any]:
    """轮询扫码结果, 成功返回 {mid, username, access_token, refresh_token, expires_at}。"""
    sess = _passport_session()
    deadline = time.time() + timeout_seconds
    last_code = None
    while time.time() < deadline:
        time.sleep(2)
        params = _sign_tv({
            "appkey": TV_APPKEY, "auth_code": auth_code,
            "local_id": 0, "ts": int(time.time()),
        })
        r = sess.post(TV_POLL_URL, data=params, timeout=TIMEOUT)
        r.raise_for_status()
        result = r.json()
        code = result.get("code")
        if code != last_code:
            if status_cb:
                if code in (86039, 86101):
                    status_cb("等待扫码…")
                elif code == 86090:
                    status_cb("已扫码, 等待手机端确认…")
                elif code == 86038:
                    raise FgoError("二维码已过期, 请重试")
                elif code != 0:
                    status_cb(f"等待授权: code={code}")
            last_code = code
        if code != 0:
            continue

        payload = result.get("data")
        if not isinstance(payload, dict):
            raise FgoError("扫码成功响应缺少 data")
        token_info = payload.get("token_info") if isinstance(payload.get("token_info"), dict) else payload
        token = str(token_info.get("access_token") or "")
        refresh = str(token_info.get("refresh_token") or "")
        mid = int(token_info.get("mid") or 0)
        expires_in = int(token_info.get("expires_in") or 0)
        if not token or mid <= 0:
            raise FgoError("扫码成功但缺少 access_token/mid")

        username = ""
        cookies = {}
        ci = payload.get("cookie_info")
        if isinstance(ci, dict) and isinstance(ci.get("cookies"), list):
            cookies = {str(c["name"]): str(c["value"]) for c in ci["cookies"]
                       if isinstance(c, dict) and c.get("name")}
        if cookies:
            try:
                nav = requests.get(BILI_NAV_URL, cookies=cookies,
                                   headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
                nd = nav.json()
                if nd.get("code") == 0:
                    username = str(nd["data"].get("uname") or "")
            except Exception:
                pass
        return {
            "mid": mid,
            "username": username,
            "access_token": token,
            "refresh_token": refresh,
            "expires_at": int(time.time()) + expires_in if expires_in > 0 else 0,
        }
    raise FgoError("等待扫码超时")


# ================= FGO 协议 =================

def refresh_access_token(access_token: str, refresh_token: str) -> dict[str, Any]:
    """刷新 B站 access_token。成功返回 {access_token, refresh_token, expires_at}。

    注意: 刷新成功后旧 token 立即失效, 调用方必须立刻保存新 token。
    """
    params = _sign_tv({
        "appkey": TV_APPKEY,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "ts": int(time.time()),
    })
    r = _passport_session().post(
        "https://passport.bilibili.com/api/oauth2/refreshToken",
        data=params, timeout=TIMEOUT,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise FgoError(f"token 刷新失败: code={payload.get('code')} message={payload.get('message')}")
    data = payload["data"]
    return {
        "access_token": str(data["access_token"]),
        "refresh_token": str(data.get("refresh_token") or refresh_token),
        "expires_at": int(time.time()) + int(data.get("expires_in") or 0),
    }


def parse_fgo_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    s = str(data).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    try:
        obj = json.loads(base64.b64decode(unquote(s).strip()).decode("utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception as exc:
        raise FgoError(f"无法解码 FGO 响应: {exc}") from exc
    raise FgoError("FGO 响应不是 JSON 对象")


def response_detail(payload: dict[str, Any], nid: str) -> dict[str, Any]:
    for item in payload.get("response") or []:
        if not isinstance(item, dict):
            continue
        if item.get("nid", "") == nid:
            code = str(item.get("resCode", ""))
            if code not in ("", "00"):
                raise FgoError(f"FGO {nid or 'login'} 失败: resCode={code} detail={item.get('fail')}")
            success = item.get("success")
            return success if isinstance(success, dict) else item
    errors = [f"{x.get('nid')}:{x.get('resCode')}" for x in payload.get("response") or []
              if isinstance(x, dict) and str(x.get("resCode", "")) not in ("", "00")]
    raise FgoError(f"FGO 响应中找不到 {nid!r}, error_codes={errors}")


def _response_usk(payload: dict[str, Any]) -> str | None:
    """从 ac.php 响应提取下一轮会话种子 (response[].usk 或 userGame.usk)。"""
    for item in payload.get("response") or []:
        if isinstance(item, dict) and item.get("usk"):
            return str(item["usk"])
    updated = (payload.get("cache") or {}).get("updated") or {}
    for game in updated.get("userGame") or []:
        if isinstance(game, dict) and game.get("usk"):
            return str(game["usk"])
    return None


def _next_usk(seed: str) -> str:
    """会话种子 -> 下一次请求的 usk (与 sgusk->usk 同一公式)。"""
    return hashlib.md5((FUNNY_KEY + seed).encode("utf-8")).hexdigest()


def _purchase_result(payload: dict[str, Any]) -> dict[str, Any]:
    """解析 shoppurchase 响应 (错误响应 nid 可能不是 purchase, 实测为 "0")。"""
    items = [x for x in payload.get("response") or [] if isinstance(x, dict)]
    for item in items:
        if item.get("nid") == "purchase":
            code = str(item.get("resCode", ""))
            success = item.get("success") or {}
            return {
                "ok": code in ("", "00"),
                "code": code,
                "name": str(success.get("purchaseName") or ""),
                "num": int(success.get("PurchaseNum") or 0),
                "fail": item.get("fail") or {},
            }
    for item in items:
        code = str(item.get("resCode", ""))
        if code not in ("", "00"):
            fail = item.get("fail") or {}
            detail = " ".join(str(fail.get("detail") or fail.get("message") or "").split())
            return {"ok": False, "code": code, "name": "", "num": 0,
                    "fail": detail or fail or "未知错误"}
    return {"ok": False, "code": "missing", "name": "", "num": 0,
            "fail": "响应中找不到结果"}


def ap_state(payload: dict[str, Any]) -> dict[str, int]:
    """从 toplogin/shoppurchase 响应计算 AP 状态。

    公式(已用游戏内显示标定, 2026-09-26, actMax=141 时显示 14, 公式给出 14):
        AP = actMax - ceil((actRecoverAt - serverTime) / 300)
    actRecoverAt 为 AP 回满时刻; 已回满(actRecoverAt<=now)时 AP=actMax。
    返回 {ap, act_max, act_recover_at, server_time}。
    """
    cache = payload.get("cache") or {}
    server_time = int(cache.get("serverTime") or time.time())
    replaced = cache.get("replaced") or {}
    game = (replaced.get("userGame") or [{}])[0] if replaced.get("userGame") else {}
    act_max = int(game.get("actMax") or 0)
    recover_at = int(game.get("actRecoverAt") or 0)
    if act_max <= 0:
        raise FgoError("响应中缺少 actMax, 无法计算 AP")
    if recover_at <= server_time:
        ap = act_max
    else:
        delta = recover_at - server_time
        ap = act_max - -(-delta // AP_RECOVER_SECONDS)  # ceil
        ap = max(0, ap)
    return {"ap": ap, "act_max": act_max, "act_recover_at": recover_at,
            "server_time": server_time}


def _read_gametop_cache() -> dict[str, Any] | None:
    """读本地缓存的版本元数据(元数据站点挂掉时兜底); 太旧就不用。"""
    try:
        with open(GAMETOP_CACHE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    age = time.time() - float(data.get("cached_at") or 0)
    if age > GAMETOP_CACHE_MAX_AGE:
        return None
    cn = data.get("CN")
    return cn if isinstance(cn, dict) else None


def _write_gametop_cache(cn: dict[str, Any]) -> None:
    try:
        with open(GAMETOP_CACHE, "w", encoding="utf-8") as f:
            json.dump({"cached_at": time.time(), "CN": cn}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_game_top() -> dict[str, Any]:
    sess = domestic_session()          # 国内元数据站点, 不走系统代理
    last_exc: Exception | None = None
    for url in CHALDEA_GAMETOP_URLS:
        try:
            r = sess.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            cn = r.json().get("CN")
            if isinstance(cn, dict):
                try:
                    rr = sess.get(FGO_CONFIG_URL, timeout=TIMEOUT)
                    m = re.search(r"FateGO_(\d+\.\d+\.\d+)_", rr.text) or \
                        re.search(r'"latest_version"\s*:\s*"(\d+\.\d+\.\d+)"', rr.text)
                    if m:
                        cn["appVer"] = m.group(1)
                except Exception:
                    pass
                _write_gametop_cache(cn)
                return cn
        except Exception as exc:
            last_exc = exc
    cached = _read_gametop_cache()
    if cached:
        print(f"提示: 版本元数据站点不可用({last_exc}), 改用本地缓存 {GAMETOP_CACHE}")
        return cached
    raise FgoError(f"无法获取国服版本元数据: {last_exc}")


def _game_headers(unity_ver: str, platform: dict[str, Any]) -> dict[str, str]:
    if platform["is_android"]:
        ua = f"UnityPlayer/{unity_ver} (UnityWebRequest/1.0, libcurl/8.10.1-DEV)"
    else:
        ua = "fatego/20 CFNetwork/1327.0.4 Darwin/21.2.0"
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Connection": "keep-alive",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "X-Unity-Version": unity_ver,
        "User-Agent": ua,
    }


class GameSession:
    """一次 FGO 游戏会话: 封装 ac.php 的公共参数 + usk 链式轮换。

    每条响应都会下发新的会话种子(usk/sgusk), 下一条请求的 usk = md5(FUNNY_KEY + 种子);
    断链即失效, 所以所有 ac.php 动作都必须走同一个 session 顺序发。
    """

    def __init__(self, *, host: str, platform_id: str, sess: requests.Session,
                 headers: dict[str, str], app_ver: str, data_ver: int,
                 device_id: str, nickname: str, sguid: str, sgtype: Any,
                 sgtag: str, usk: str, app_start: float,
                 date_ver: int | None = None) -> None:
        self.host = host
        self.platform_id = platform_id
        self.sess = sess
        self.headers = headers
        self.app_ver = app_ver
        self.data_ver = data_ver
        # dateVer 与 dataVer 不同: dataVer 是主数据版本号(如 987),
        # dateVer 是主数据的"日期版本"时间戳(如 1791748800), 服务端按它下发 mst* 增量。
        self.date_ver = int(date_ver) if date_ver else int(data_ver)
        self.device_id = device_id
        self.nickname = nickname
        self.sguid = sguid
        self.sgtype = sgtype
        self.sgtag = sgtag
        self.usk = usk
        self.app_start = app_start
        self.platform = PLATFORMS[platform_id]

    def common_form(self, key: str, with_login_fields: bool = False) -> OrderedDict:
        """ac.php 的公共表单字段 (各动作在其后追加自己的参数)。"""
        form: OrderedDict[str, Any] = OrderedDict()
        if with_login_fields:
            form["nickname"] = self.nickname
            form["sgtype"] = self.sgtype
            form["sgtag"] = self.sgtag
        p = self.platform
        form.update(OrderedDict([
            ("ac", "action"), ("key", key), ("deviceid", self.device_id),
            ("os", p["os"]), ("ptype", p["ptype"]), ("usk", self.usk), ("umk", ""),
            ("rgsid", 1001), ("rkchannel", p["rkchannel"]), ("cPlat", p["cPlat"]),
            ("uPlat", p["uPlat"]), ("userId", self.sguid), ("appVer", self.app_ver),
            ("dateVer", self.date_ver), ("lastAccessTime", int(time.time())),
            ("developmentAuthCode", DEVELOPMENT_AUTH_CODE),
            ("idempotencyKey", str(uuid.uuid4())), ("userAgent", 1),
            ("dataVer", self.data_ver),
        ]))
        return form

    def rotate_usk(self, payload: dict[str, Any]) -> str | None:
        """用响应里的会话种子推进 usk; 返回种子(无则 None)。"""
        seed = _response_usk(payload)
        if seed:
            self.usk = _next_usk(seed)
        return seed

    def action(self, key: str, extra: dict[str, Any] | None = None,
               with_login_fields: bool = False, url_key: str | None = None) -> dict[str, Any]:
        """执行一个 ac.php 动作, 返回解码后的 payload (并自动轮换 usk)。"""
        form = self.common_form(key, with_login_fields)
        if extra:
            form.update(extra)
        client_local = time.monotonic() - self.app_start
        url = (f"{self.host}/rongame_beta/rgfate/60_1001/ac.php"
               f"?_userId={self.sguid}&_key={url_key or key}&_clientLocalTime={client_local:.5f}")
        r = self.sess.post(url, data=form, headers=self.headers, timeout=TIMEOUT)
        r.raise_for_status()
        payload = parse_fgo_payload(r.text)
        self.rotate_usk(payload)
        return payload


def open_session(access_token: str, mid: int, username: str, nickname: str,
                 device_id: str, platform_id: str = "android_bili",
                 log_cb=print,
                 raw_sink: dict[str, Any] | None = None,
                 data_ver_override: int | None = None) -> tuple[GameSession, dict[str, Any]]:
    """执行完整登录链(gamedata -> 授权 -> login -> toplogin), 返回 (会话, toplogin 响应)。

    raw_sink 不为 None 时, 会把各步骤的原始响应塞进去(调试/协议分析用), 键为
    member / logintomembercenter / login / toplogin。

    data_ver_override 用于协议调试: 用一个更旧的 dataVer/dateVer 调 login.php,
    服务端会把该版本之后变更过的 mst* 主数据全部下发(即客户端"数据更新"那一步),
    日常签到不需要设置它。
    """
    platform = PLATFORMS[platform_id]
    cn = fetch_game_top()
    app_ver = str(cn.get("appVer") or "").strip()
    data_ver = int(cn.get("dataVer") or 0)
    unity_ver = str(cn.get("unityVer") or DEFAULT_UNITY)
    if not app_ver or data_ver <= 0:
        raise FgoError(f"版本元数据异常: {cn}")

    host = platform["host"]
    rkchannel, cplat, uplat = platform["rkchannel"], platform["cPlat"], platform["uPlat"]
    os_name, ptype = platform["os"], platform["ptype"]
    log_cb(f"连接 {platform['label']}, 当前版本 FGO {app_ver}")

    sess = domestic_session()          # 游戏服务器都在国内, 不走系统代理
    headers = _game_headers(unity_ver, platform)
    app_start = time.monotonic() - 30.0

    def post(url, form):
        r = sess.post(url, data=form, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r

    def record(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if raw_sink is not None:
            raw_sink[name] = payload
        return payload

    # 1/4 gamedata
    log_cb("1/4 获取 gamedata…")
    member = OrderedDict([
        ("deviceid", ""), ("t", "22360"), ("v", "1.0.1"), ("s", "1"),
        ("mac", "00000000000000E0"), ("os", ""), ("ptype", ""), ("imei", "aaaaa"),
        ("username", "lv9999"), ("type", "login"), ("password", "111111"),
        ("rksdkid", "1"), ("rkchannel", rkchannel), ("cPlat", cplat), ("uPlat", uplat),
        ("appVer", app_ver), ("dateVer", data_ver), ("lastAccessTime", int(time.time())),
        ("developmentAuthCode", DEVELOPMENT_AUTH_CODE), ("idempotencyKey", str(uuid.uuid4())),
        ("version", data_ver), ("dataVer", data_ver),
    ])
    s = response_detail(record("member", parse_fgo_payload(post(f"{host}//rongame_beta/rgfate/60_member/member.php", member).text)), "gamedata")
    data_ver = int(s.get("version", data_ver))
    date_ver = data_ver

    # 2/4 B站授权校验
    log_cb("2/4 验证 Bilibili 授权…")
    lm = OrderedDict([
        ("deviceid", ""), ("t", "22360"), ("v", "1.0.1"), ("s", "1"),
        ("mac", "00000000000000E0"), ("os", ""), ("ptype", ""), ("imei", "aaaaa"),
        ("rksdkid", "1"), ("username", username),
    ])
    if platform["is_android"]:
        lm["bundleid"] = "com.bilibili.fatego"
    lm.update(OrderedDict([
        ("type", "token"), ("rkuid", mid), ("access_token", access_token),
        ("rkchannel", rkchannel), ("cPlat", cplat), ("uPlat", uplat),
        ("appVer", app_ver), ("dateVer", data_ver), ("lastAccessTime", int(time.time())),
        ("developmentAuthCode", DEVELOPMENT_AUTH_CODE), ("idempotencyKey", str(uuid.uuid4())),
        ("version", data_ver), ("dataVer", data_ver),
    ]))
    s = response_detail(record("logintomembercenter", parse_fgo_payload(post(f"{host}/rongame_beta/rgfate/60_member/logintomembercenter.php", lm).text)), "login_to_membercenter")
    rguid, rgusk = str(s["rguid"]), str(s["rgusk"])
    data_ver = int(s.get("dataVer", data_ver))
    date_ver = int(s.get("dateVer", data_ver))
    time.sleep(2)

    # 3/4 建立游戏会话
    log_cb("3/4 建立 FGO 会话…")
    if data_ver_override is not None:
        req_data_ver = req_date_ver = max(1, int(data_ver_override))
        log_cb(f"协议调试: login.php 使用旧 dataVer={req_data_ver} (拉取全量 mst* 主数据)")
    else:
        req_data_ver, req_date_ver = data_ver, date_ver
    gl = OrderedDict([
        ("deviceid", device_id), ("os", os_name), ("ptype", ptype),
        ("rgsid", 1001), ("rguid", rguid), ("rgusk", rgusk), ("idfa", ""),
        ("v", "1.0.1"), ("mac", "0"), ("imei", ""), ("type", "login"),
        ("nickname", nickname), ("rkchannel", rkchannel), ("cPlat", cplat), ("uPlat", uplat),
        ("assetbundleFolder", ""), ("appVer", app_ver), ("dateVer", req_date_ver),
        ("lastAccessTime", int(time.time())), ("developmentAuthCode", DEVELOPMENT_AUTH_CODE),
        ("idempotencyKey", str(uuid.uuid4())), ("userAgent", 1), ("t", 20399),
        ("s", 1), ("rksdkid", 1), ("dataVer", req_data_ver),
    ])
    s = response_detail(record("login", parse_fgo_payload(post(f"{host}/rongame_beta/rgfate/60_1001/login.php", gl).text)), "")
    sguid = str(s["sguid"])
    sgtype = s.get("sgtype", 2)
    sgtag = str(s.get("sgtag", ""))
    resolved_nickname = str(s.get("nickname") or nickname)
    usk = hashlib.md5((FUNNY_KEY + str(s["sgusk"])).encode("utf-8")).hexdigest()
    time.sleep(5)

    # 4/4 toplogin (即每日签到)
    log_cb("4/4 TopLogin 签到…")
    session = GameSession(
        host=host, platform_id=platform_id, sess=sess, headers=headers,
        app_ver=app_ver, data_ver=data_ver, device_id=device_id,
        nickname=resolved_nickname, sguid=sguid, sgtype=sgtype, sgtag=sgtag,
        usk=usk, app_start=app_start, date_ver=date_ver,
    )
    payload = record("toplogin", session.action("toplogin", with_login_fields=True))
    response_detail(payload, "login")
    log_cb("TopLogin 完成")
    return session, payload


def convert_apples(session: GameSession, toplogin_payload: dict[str, Any],
                   apple_num: int, apple_min_ap: int, log_cb=print) -> dict[str, Any]:
    """AP -> 青铜果实 合成 (树苗+40AP, usk 每次响应轮换)。返回结果摘要。"""
    apple: dict[str, Any] = {"requested": apple_num, "converted": 0, "name": "", "errors": [],
                             "ap_before": None, "ap_after": None}
    try:
        st = ap_state(toplogin_payload)
        apple["ap_before"] = st["ap"]
        log_cb(f"当前 AP: {st['ap']}/{st['act_max']}")
    except FgoError as exc:
        log_cb(f"AP 检测失败: {exc}")
        st = None
    # apple_min_ap < 0 表示"AP 满了才合成" (阈值 = actMax, 等级提升后自适应)
    threshold = (st["act_max"] if apple_min_ap < 0 else apple_min_ap) if st else 0
    if threshold > 0 and (st is None or st["ap"] < threshold):
        reason = (f"AP={st['ap']}/{st['act_max']} < {threshold}, 未达合成阈值"
                  if st else "AP 检测失败, 无法判断阈值")
        apple["errors"].append(reason)
        if st and st["ap"] < st["act_max"]:
            apple["eta_seconds"] = (threshold - st["ap"]) * AP_RECOVER_SECONDS
        log_cb(f"苹果合成跳过: {reason}")
        return apple
    log_cb(f"开始合成苹果 (每 40AP 换 1 个, 阈值 {threshold}, 上限 {apple_num} 个)…")
    if not _response_usk(toplogin_payload):
        log_cb("提示: toplogin 响应未含 usk 种子, 尝试沿用当前 usk")
    est_ap = st["ap"] if st is not None else None
    for i in range(apple_num):
        if est_ap is not None and i > 0:
            if threshold > 0 and est_ap < threshold:
                apple["errors"].append(f"AP={est_ap} < {threshold}, 停止继续合成")
                break
            if est_ap < APPLE_COST:
                apple["errors"].append(f"AP={est_ap} < {APPLE_COST}, AP 不足")
                break
        buy_payload = session.action("shoppurchase", {"id": APPLE_SHOP_ID, "num": 1})
        result = _purchase_result(buy_payload)
        if not result["ok"]:
            log_cb("合成响应诊断: " + json.dumps(
                buy_payload.get("response"), ensure_ascii=False)[:500])
            apple["errors"].append(f"resCode={result['code']} {result['fail']}")
            break
        apple["converted"] += 1
        apple["name"] = result["name"] or apple["name"]
        if est_ap is not None:
            est_ap = max(0, est_ap - APPLE_COST)
            apple["ap_after"] = est_ap
        time.sleep(1.5)
    if apple["converted"]:
        log_cb(f"苹果合成完成: 共 {apple['converted']} 个")
    else:
        log_cb(f"苹果合成未完成: {'; '.join(apple['errors']) or '未知'}")
    return apple


def toplogin(access_token: str, mid: int, username: str, nickname: str,
             device_id: str, platform_id: str = "android_bili",
             apple_num: int = 0, apple_min_ap: int = 0, log_cb=print) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """完整登录链 + 可选苹果合成, 返回 (toplogin 响应 payload, 合成结果或 None)。"""
    session, payload = open_session(
        access_token=access_token, mid=mid, username=username, nickname=nickname,
        device_id=device_id, platform_id=platform_id, log_cb=log_cb,
    )
    apple = None
    if apple_num > 0:
        apple = convert_apples(session, payload, apple_num, apple_min_ap, log_cb)
    return payload, apple
