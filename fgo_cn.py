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
    s = requests.Session()
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


def fetch_game_top() -> dict[str, Any]:
    last_exc: Exception | None = None
    for url in CHALDEA_GAMETOP_URLS:
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            cn = r.json().get("CN")
            if isinstance(cn, dict):
                try:
                    rr = requests.get(FGO_CONFIG_URL, timeout=TIMEOUT)
                    m = re.search(r"FateGO_(\d+\.\d+\.\d+)_", rr.text) or \
                        re.search(r'"latest_version"\s*:\s*"(\d+\.\d+\.\d+)"', rr.text)
                    if m:
                        cn["appVer"] = m.group(1)
                except Exception:
                    pass
                return cn
        except Exception as exc:
            last_exc = exc
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

    sess = requests.Session()
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


# ================= 召唤 (卡池 / 抽卡) =================
#
# 实测结论 (国服 2.129.0, 2026-10, 用只读/无副作用请求标定):
#   动作:  POST ac.php?_key=gachadraw
#   参数:  id=<卡池ID>  num=<抽数 1|10>   (+ 公共会话字段 usk 等)
#          - 参数名就是 id / num (与 shoppurchase 同风格);
#            写成 gachaId 会被服务端判为 "参数错误", 缺 id 同样是 "参数错误"。
#          - 卡池不可用/不存在的 id 一律返回 resCode=99 + "扭蛋ID有误"
#            (fail.action=goto_title, nid=0)。所以"报错详情"本身不含可用卡池信息。
#   卡池主数据 mstGacha 由服务端在 toplogin/home 的 cache.updated 里按增量下发,
#   字段: id/name/type/freeDrawFlag/maxDrawNum/drawNum1/shopId1/ticketItemId/
#         gachaGroupId/openedAt/closedAt/adjustId/pickupId/beforeGachaId ...
#   用户侧记录 userGacha (toplogin 的 cache.replaced 里全量下发):
#         gachaId/num/tenNum/freeDrawAt/status
#         → freeDrawAt = 该卡池上一次"免费抽"的时间, 配 userGame.zerotime(今日 0 点重置时刻)
#           即可判断"今天的免费单抽是否还没用"。

GACHA_DRAW_KEY = "gachadraw"
GACHA_STORY_HINTS = ("剧情", "故事", "ストーリー", "story")
ITEM_TICKET = 4001  # 呼符


def gacha_master_rows(*payloads: dict[str, Any]) -> list[dict[str, Any]]:
    """从若干响应里收集出现过的 mstGacha 行(去重, 按 id 排序)。

    mstGacha 只在服务端"有变化"时下发, 所以这里拿到的通常是最近新开/改动过的卡池。
    """
    rows: dict[int, dict[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        containers = [payload.get("cache") or {}]
        containers += [payload.get("success") or {}]
        for cont in containers:
            segments = [cont] + [cont.get(seg) or {} for seg in ("updated", "replaced", "cache")]
            for seg in segments:
                table = seg.get("mstGacha") if isinstance(seg, dict) else None
                if isinstance(table, list):
                    for row in table:
                        if isinstance(row, dict) and row.get("id") is not None:
                            rows[int(row["id"])] = row
    return [rows[k] for k in sorted(rows)]


def user_gacha_rows(*payloads: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """收集 userGacha 行, 返回 {gachaId: row}。"""
    out: dict[int, dict[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        cache = payload.get("cache") or {}
        for seg in ("replaced", "updated"):
            for row in (cache.get(seg) or {}).get("userGacha") or []:
                if isinstance(row, dict) and row.get("gachaId") is not None:
                    out[int(row["gachaId"])] = row
    return out


def user_game_row(*payloads: dict[str, Any]) -> dict[str, Any]:
    """从响应里取出 userGame 行(拿 zerotime / 圣晶石等)。"""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        cache = payload.get("cache") or {}
        for seg in ("replaced", "updated"):
            rows = (cache.get(seg) or {}).get("userGame") or []
            if rows and isinstance(rows[0], dict):
                return rows[0]
    return {}


def user_item_num(payload: dict[str, Any], item_id: int) -> int | None:
    """从响应 cache 里读某个道具的当前数量(找得到才返回)。"""
    cache = payload.get("cache") or {}
    for seg in ("replaced", "updated"):
        for row in (cache.get(seg) or {}).get("userItem") or []:
            if isinstance(row, dict) and int(row.get("itemId") or 0) == item_id:
                return int(row.get("num") or 0)
    return None


def pick_free_story_pool(rows: list[dict[str, Any]],
                         log_cb=print) -> dict[str, Any] | None:
    """从可见的 mstGacha 行里挑出"每日免费剧情池"。

    判据: freeDrawFlag != 0 (免费抽卡池) + 名称命中剧情池关键词 / id 较小(常驻池特征)。
    找不到返回 None —— 属于正常情况: 常驻剧情池没变化时服务端不会重复下发它的主数据。
    """
    free_rows = [r for r in rows if int(r.get("freeDrawFlag") or 0) != 0]
    if not free_rows:
        return None
    named = [r for r in free_rows
             if any(h in str(r.get("name") or "") for h in GACHA_STORY_HINTS)]
    if named:
        return named[0]
    free_rows.sort(key=lambda r: int(r.get("id") or 0))
    log_cb(f"提示: 未命中剧情池名称关键词, 按 id 取第一个免费卡池 id={free_rows[0].get('id')}")
    return free_rows[0]


def free_draw_state(gacha_id: int, pool_row: dict[str, Any] | None,
                    user_row: dict[str, Any] | None, game_row: dict[str, Any],
                    server_time: int, free_until: int | None = None,
                    assume_free: bool = False) -> dict[str, Any]:
    """判断"今天的免费单抽"是否还没用。返回 {ok, reason, used_at, reset_at}。"""
    reset_at = int(game_row.get("zerotime") or 0)
    used_at = int((user_row or {}).get("freeDrawAt") or 0)
    info: dict[str, Any] = {"ok": False, "reason": "", "used_at": used_at, "reset_at": reset_at}

    if free_until and server_time > free_until:
        info["reason"] = (f"免费活动窗口已过 (serverTime={server_time} > until={free_until})")
        return info
    if pool_row is not None and int(pool_row.get("freeDrawFlag") or 0) == 0:
        info["reason"] = f"卡池 {gacha_id} 的 freeDrawFlag=0, 不是免费卡池"
        return info
    if reset_at and used_at >= reset_at:
        info["reason"] = "今日免费单抽已使用 (freeDrawAt 晚于今日重置时刻)"
        return info
    if pool_row is None and not assume_free:
        info["reason"] = ("主数据里看不到该卡池的 freeDrawFlag, 无法证明是"
                          "免费卡池 (可加 --assume-free, 或先确认该 id 就是活动免费池)")
        return info
    info["ok"] = True
    info["reason"] = ("今日免费单抽可用" if not assume_free
                      else "按 --assume-free 假定免费次数可用")
    return info


def gacha_draw(session: GameSession, gacha_id: int, num: int = 1,
               id_param: str = "id", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """发一次抽卡请求 (_key=gachadraw), 返回原始 payload。

    参数形态以实测为准: 确认 `id` + `num` 会被服务端接受(参数名校验通过),
    而对任何"当前不可抽"的 id 一律回 扭蛋ID有误。若将来发现客户端用的是别的
    参数名/额外字段(例如 PHP 数组形态 gachaId[] 或带 type), 用 id_param/extra 覆盖即可,
    不必改代码(gacha.json 里可配 id_param / extra)。
    """
    form: dict[str, Any] = {id_param: int(gacha_id), "num": int(num)}
    if extra:
        form.update(extra)
    return session.action(GACHA_DRAW_KEY, form)


def _walk_cards(node: Any, out: list[dict[str, Any]], depth: int = 0) -> None:
    """在抽卡响应里挖出"抽到的卡"(带 svtId/ceId/rarity/isNew 之类字段的字典)。"""
    if depth > 6:
        return
    if isinstance(node, dict):
        keys = set(node)
        tags = [k for k in ("svtId", "ceId", "equipId", "svtType", "cardType")
                if k in keys]
        if tags and ({"rarity"} & keys or {"isNew"} & keys or len(keys) <= 12):
            card = {k: node.get(k) for k in
                    ("svtId", "ceId", "equipId", "cardType", "svtType", "rarity",
                     "limitCount", "isNew", "type", "imageId", "name", "id")
                    if k in keys}
            if len(card) > 1:
                out.append(card)
                return
        for v in node.values():
            _walk_cards(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk_cards(v, out, depth + 1)


def gacha_draw_result(payload: dict[str, Any], gacha_id: int | None = None,
                      game_row_before: dict[str, Any] | None = None,
                      ticket_before: int | None = None,
                      server_time: int = 0) -> dict[str, Any]:
    """解析 gachadraw 响应: 抽到的卡 / 资源消耗 / 是否走了免费次数。

    判定"是否免费"的依据(服务端返回的 cache 增量):
      * userGacha.freeDrawAt >= 今日重置时刻 -> 本次记的是免费抽
      * 或者 userItem(4001 呼符)/freeStone 数量下降 -> 本次消耗了资源
    """
    res: dict[str, Any] = {"ok": False, "code": "", "fail": "", "action": "",
                           "cards": [], "items": [], "resource": {}, "free_draw_at": 0,
                           "verdict": "unknown", "success_keys": []}
    for item in payload.get("response") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("resCode", ""))
        if code not in ("", "00"):
            fault = item.get("fail") or {}
            res["code"] = code
            res["fail"] = str(fault.get("detail") or fault.get("message") or fault or "未知错误")
            res["action"] = str(fault.get("action") or "")
            continue
        res["ok"] = True
        success = item.get("success")
        if isinstance(success, dict):
            res["success_keys"] = [k for k in success if k != "cache"]
            _walk_cards(success, res["cards"])

    cache = payload.get("cache") or {}
    updated = cache.get("updated") or {}
    for seg in ("updated", "replaced"):
        for row in (cache.get(seg) or {}).get("userGacha") or []:
            if not isinstance(row, dict):
                continue
            if gacha_id is None or int(row.get("gachaId") or -1) == int(gacha_id):
                res["free_draw_at"] = int(row.get("freeDrawAt") or 0)
                res["gacha_row"] = row
                break
    for row in updated.get("userItem") or []:
        if isinstance(row, dict):
            res["items"].append({"itemId": int(row.get("itemId") or 0),
                                 "num": int(row.get("num") or 0)})
    game_after = {}
    for row in updated.get("userGame") or []:
        if isinstance(row, dict):
            game_after = row
    before = game_row_before or {}
    for field in ("freeStone", "chargeStone", "stone"):
        if field in game_after and field in before:
            delta = int(game_after.get(field) or 0) - int(before.get(field) or 0)
            if delta:
                res["resource"][field] = delta
    if game_after:
        res["game_after"] = {k: game_after.get(k) for k in
                             ("freeStone", "chargeStone", "stone") if k in game_after}
    reset_at = int((game_row_before or {}).get("zerotime") or 0)
    ticket_after = user_item_num(payload, ITEM_TICKET)
    if ticket_before is not None and ticket_after is not None and ticket_after != ticket_before:
        res["resource"][f"item{ITEM_TICKET}"] = ticket_after - ticket_before
    spent = {k: v for k, v in res["resource"].items() if v < 0}
    if res["ok"]:
        if spent:
            res["verdict"] = "paid"
        elif reset_at and res["free_draw_at"] >= reset_at:
            res["verdict"] = "free"
        else:
            res["verdict"] = "unknown"
    return res


def daily_free_draw(session: GameSession, toplogin_payload: dict[str, Any], *,
                    gacha_id: int | None = None, num: int = 1,
                    assume_free: bool = False, free_until: int | None = None,
                    do_draw: bool = True, log_cb=print,
                    id_param: str = "id",
                    extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """每日免费剧情池单抽: 定位卡池 -> 校验免费状态 -> 抽 1 次 -> 解析结果。

    gacha_id 为 None 时, 尝试从响应里可见的 mstGacha 行自动识别免费剧情池。
    do_draw=False 时只做检查(不发送抽卡请求)。
    id_param/extra 用于覆盖请求参数形态(以抓包实测为准)。
    """
    out: dict[str, Any] = {"drawn": False, "skipped": False, "pool_id": gacha_id,
                           "pool_name": "", "state": None, "result": None, "notes": []}
    rows = gacha_master_rows(toplogin_payload)
    user_rows = user_gacha_rows(toplogin_payload)
    game_row = user_game_row(toplogin_payload)
    server_time = int(((toplogin_payload.get("cache") or {}).get("serverTime")) or time.time())

    pool_row = None
    if gacha_id is None:
        pool_row = pick_free_story_pool(rows, log_cb)
        if pool_row is None:
            out["skipped"] = True
            out["notes"].append(
                "无法自动定位剧情池: 服务端这次没有下发 mstGacha 主数据。"
                "请用 --id / FGO_GACHA_ID 指定卡池 ID (国服 2026-10 为 500139)")
            log_cb(out["notes"][-1])
            return out
        gacha_id = int(pool_row["id"])
        out["pool_id"] = gacha_id
    else:
        pool_row = next((r for r in rows if int(r.get("id") or -1) == int(gacha_id)), None)
    if pool_row:
        out["pool_name"] = str(pool_row.get("name") or "")

    user_row = user_rows.get(int(gacha_id))
    state = free_draw_state(gacha_id, pool_row, user_row, game_row, server_time,
                            free_until=free_until, assume_free=assume_free)
    out["state"] = state
    log_cb(f"卡池 id={gacha_id}"
           + (f" 「{out['pool_name']}」" if out["pool_name"] else "")
           + f" 免费单抽状态: {'可用' if state['ok'] else '不可用'} ({state['reason']})")
    if not state["ok"]:
        out["skipped"] = True
        return out
    if not do_draw:
        out["notes"].append("仅检查模式 (do_draw=False), 未发送抽卡请求")
        return out

    ticket_before = user_item_num(toplogin_payload, ITEM_TICKET)
    payload = gacha_draw(session, gacha_id, num, id_param=id_param, extra=extra)
    result = gacha_draw_result(payload, gacha_id=gacha_id, game_row_before=game_row,
                               ticket_before=ticket_before, server_time=server_time)
    out["drawn"] = bool(result["ok"])
    out["result"] = result
    if result["ok"]:
        log_cb(f"单抽完成: 得到 {len(result['cards'])} 张卡, 判定={result['verdict']}")
        if result["verdict"] == "paid":
            log_cb("!! 注意: 本次抽卡消耗了资源 (见 resource 字段), 说明免费次数并未生效")
    else:
        log_cb(f"单抽失败: resCode={result['code']} {result['fail']}")
    return out

