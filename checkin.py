# -*- coding: utf-8 -*-
"""FGO B服每日签到: 执行 toplogin 并输出账号/奖励摘要。

用法:
    python checkin.py           # 读取 auth.json (本地) 或环境变量 FGO_AUTH_JSON (CI)

token 剩余有效期不足 30 天时自动刷新;
凭据有变化时会写回 auth.json (本地) 或生成 updated_auth.json (CI, 供更新 secret)。
"""
import json
import os
import sys
import time

import fgo_cn
import notify

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

AUTH_FILE = "auth.json"
UPDATED_FILE = "updated_auth.json"
REFRESH_THRESHOLD = 30 * 86400  # 剩余不足 30 天则刷新

_START = time.time()


def log(msg: str) -> None:
    """带时间戳的单行日志。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def log_cb(msg: str) -> None:
    """传给 fgo_cn 的日志回调。"""
    log(msg)


def load_auth() -> tuple[dict, bool, str]:
    """返回 (auth, from_env, 来源描述)。

    FGO_AUTH_JSON 优先(CI), 否则读 FGO_AUTH_FILE 指定的文件, 再否则读 ./auth.json。
    """
    env = os.environ.get("FGO_AUTH_JSON", "").strip().lstrip("﻿")
    if env:
        return json.loads(env), True, "env:FGO_AUTH_JSON"
    path = os.environ.get("FGO_AUTH_FILE", "").strip() or AUTH_FILE
    with open(path, encoding="utf-8") as f:
        return json.load(f), False, path


def save_auth(auth: dict, from_env: bool) -> None:
    if from_env:
        with open(UPDATED_FILE, "w", encoding="utf-8") as f:
            json.dump(auth, f, ensure_ascii=False, indent=2)
    else:
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(auth, f, ensure_ascii=False, indent=2)


def item_num(payload: dict, item_id: int) -> int | None:
    """取道具当前数量: 优先 updated(本次最新增量), 再退 replaced(整表快照)。

    实测差别: `replaced.userItem` 是"登录前"的快照, 当天新发的奖励(例如呼符)
    出现在 `updated.userItem` 里 —— 只看 replaced 会读到旧值(线上就出现过 0 vs 7)。
    """
    cache = payload.get("cache") or {}
    for seg in ("updated", "replaced"):
        for row in (cache.get(seg) or {}).get("userItem") or []:
            if isinstance(row, dict) and int(row.get("itemId") or 0) == item_id:
                return int(row.get("num") or 0)
    return None


def summarize(payload: dict) -> str:
    """从 toplogin 响应中提取账号信息与签到奖励。"""
    lines = []
    cache = payload.get("cache") or {}
    replaced = cache.get("replaced") or {}
    updated = cache.get("updated") or {}

    user_game = (replaced.get("userGame") or [{}])[0]
    if user_game:
        ticket = item_num(payload, 4001)
        lines.append(f"御主: {user_game.get('name', '?')}  Lv.{user_game.get('lv', '?')}")
        lines.append(f"圣晶石: {user_game.get('stone', '?')}   "
                     f"呼符: {ticket if ticket is not None else '?'}")

    user_login = (updated.get("userLogin") or [{}])[0]
    if user_login:
        lines.append(f"连续登录: {user_login.get('seqLoginCount', '?')}天 / "
                     f"累计: {user_login.get('totalLoginCount', '?')}天")

    for resp in payload.get("response") or []:
        success = resp.get("success") or {}
        bonus = success.get("seqLoginBonus")
        if bonus:
            b0 = bonus[0]
            lines.append(f"签到奖励: {b0.get('message', '')}")
            for it in b0.get("items") or []:
                lines.append(f"  - {it.get('name')} x{it.get('num')}")
        campaign = success.get("campaignbonus")
        if campaign:
            c0 = campaign[0]
            lines.append(f"活动奖励: {c0.get('name', '')}")
            for it in c0.get("items") or []:
                lines.append(f"  - {it.get('name')} x{it.get('num')}")

    server_time = cache.get("serverTime")
    if server_time:
        lines.append(f"服务器时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(server_time)))}")
    return "\n".join(lines) if lines else "(未解析到账号信息, 但 toplogin 已成功)"


def _flag(name: str, default: bool = False) -> bool:
    """读一个"开关"环境变量: 空值取 default, 0/false/no/off/关 视为 False。"""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw not in ("0", "false", "no", "off", "关")


def build_report(payload: dict, apple: dict | None, elapsed: float) -> str:
    """把签到结果拼成一段文本(与控制台输出一致, 也是推送给 ntfy/TG 的正文)。"""
    lines = ["===== 签到成功 =====", summarize(payload)]
    if apple is not None:
        if apple.get("ap_before") is not None:
            tail = f" (AP {apple['ap_before']}" + (
                f"→{apple['ap_after']})" if apple.get("ap_after") is not None else ")")
        else:
            tail = ""
        if apple["converted"]:
            lines.append(f"苹果合成: {apple['converted']} 个 ({apple['name']}){tail}")
        elif apple["errors"] and "未达合成阈值" in apple["errors"][0]:
            eta = apple.get("eta_seconds")
            eta_txt = ""
            if eta:
                h, m = divmod(round(eta / 60), 60)
                eta_txt = f", 约 {h}小时{m}分钟后达到阈值" if h else f", 约{m}分钟后达到阈值"
            lines.append(f"苹果合成: 跳过, {apple['errors'][0]}{eta_txt}")
        else:
            lines.append(f"苹果合成: 未完成 ({'; '.join(apple['errors']) or '未知原因'}){tail}")
    lines.append(f"[{time.strftime('%H:%M:%S')}] 完成, 耗时 {elapsed:.1f}s")
    return "\n".join(lines)


def finish(msg: str, ok: bool = True, payload: dict | None = None,
           apple: dict | None = None) -> int:
    """统一收尾: 打印报告 + 推送。ok=False 时把 msg 当作失败原因推出去。

    报告正文末行已经是"完成, 耗时 …", 所以这里不再单独打一遍(线上出现过重复行)。
    """
    elapsed = time.time() - _START
    if ok:
        report = build_report(payload or {}, apple, elapsed)
    else:
        report = f"{msg}\n[{time.strftime('%H:%M:%S')}] 耗时 {elapsed:.1f}s"
    print()
    print(report if ok else f"===== 签到失败 =====\n{report}")
    try:
        notify.send_report(report, ok=ok, log=log)
    except Exception as exc:                      # 推送问题绝不影响退出码
        log(f"推送异常(已忽略): {type(exc).__name__}: {exc}")
    return 0 if ok else 1


def main() -> int:
    try:
        auth, from_env, auth_src = load_auth()
    except FileNotFoundError:
        return finish(f"找不到 {AUTH_FILE} 且未设置 FGO_AUTH_JSON, 请先运行  python login_qr.py  扫码登录",
                      ok=False)

    try:
        apple_num = max(0, int(os.environ.get("FGO_APPLE_NUM") or 0))
    except ValueError:
        apple_num = 0
    raw_min_ap = (os.environ.get("FGO_APPLE_AP_MIN") or "120").strip().lower()
    if raw_min_ap in ("max", "full", "满"):
        apple_min_ap = -1  # -1 = AP 满了才合成
        ap_min_txt = "max(AP满了才合成)"
    else:
        try:
            apple_min_ap = max(0, int(raw_min_ap))
        except ValueError:
            apple_min_ap = 120
        ap_min_txt = str(apple_min_ap)
    log(f"开始签到: 御主={auth.get('nickname', '?')} 平台={auth.get('platform', 'android_bili')} "
        f"凭据={auth_src}")
    log(f"苹果合成={'关' if apple_num <= 0 else f'{apple_num}个/阈值{ap_min_txt}'}")

    changed = False
    remaining = int(auth.get("expires_at") or 0) - time.time()

    if remaining <= 60:
        return finish("B站 access_token 已过期, 请重新运行  python login_qr.py  扫码", ok=False)

    if remaining < REFRESH_THRESHOLD and auth.get("refresh_token"):
        log(f"token 剩余 {remaining / 86400:.0f} 天, 执行自动刷新…")
        try:
            new = fgo_cn.refresh_access_token(auth["access_token"], auth["refresh_token"])
        except fgo_cn.FgoError as exc:
            log(f"自动刷新失败(将继续用现有 token 尝试签到): {exc}")
        else:
            auth.update(new)
            changed = True
            log("刷新成功, 新有效期 180 天")
    else:
        log(f"token 剩余有效期 {remaining / 86400:.0f} 天")

    payload: dict = {}
    apple = None
    try:
        session, payload = fgo_cn.open_session(
            access_token=auth["access_token"],
            mid=int(auth["mid"]),
            username=auth.get("username") or "master",
            nickname=auth["nickname"],
            device_id=auth["device_id"],
            platform_id=auth.get("platform", "android_bili"),
            log_cb=log_cb,
        )
        if apple_num > 0:
            apple = fgo_cn.convert_apples(session, payload, apple_num, apple_min_ap, log_cb)
    except fgo_cn.FgoError as exc:
        return finish(f"签到失败: {exc}", ok=False)
    except Exception as exc:
        return finish(f"网络或解析错误: {type(exc).__name__}: {exc}", ok=False)
    finally:
        if changed:
            save_auth(auth, from_env)

    return finish("", ok=True, payload=payload, apple=apple)


if __name__ == "__main__":
    sys.exit(main())
