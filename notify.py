# -*- coding: utf-8 -*-
"""签到结果推送: ntfy / Telegram（可选，未配置就静默跳过，绝不影响签到本身）。

用法:
    python notify.py --test        # 用当前环境变量发一条测试消息
    python notify.py --dry-run     # 只打印"将会发送的 URL 与正文", 不联网
    python notify.py --text "..."  # 发送指定文本（调试用）

环境变量（放仓库 Secrets 里，见 README）:

  通用
    NOTIFY_ON       always（默认：配了就发） | fail（只在失败时发） | off（关闭）
    NOTIFY_TITLE    标题，默认 “FGO 签到”

  ntfy（两种写法二选一）
    NTFY_URL        https://ntfy.sh/你的主题     自建服务器就换成自己的域名
    NTFY_TOPIC      只填主题名，配合 NTFY_SERVER（默认 https://ntfy.sh）
    NTFY_TOKEN      主题开了写保护时填（Bearer）        [建议 Secret]
    NTFY_PRIORITY   1-5，默认 3；失败时自动用 4
    NTFY_TAGS       逗号分隔，如 video_game,white_check_mark
    NTFY_MODE       json（默认，推荐）| raw
                    json: 用 ntfy 的 JSON 发布, 标题/正文走 UTF-8 正文, 中文标题没问题;
                    raw : 用 HTTP 头发布, 而 HTTP 头只能是 latin-1, 中文标题会被丢掉
                          （想用 raw 就把 NOTIFY_TITLE 改成纯英文）

  Telegram（两项都要）
    TG_BOT_TOKEN    @BotFather 给的 token               [建议 Secret]
    TG_CHAT_ID      目标 chat id（私聊填自己的 id；群/频道先把 bot 拉进去）
    TG_PARSE_MODE   可选 HTML / MarkdownV2；默认纯文本（日志里有 `<` `>`，不建议开）

  可选
    HTTPS_PROXY     本地直连不通时设置，requests 会自动使用
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TIMEOUT = (8, 25)
TG_TEXT_LIMIT = 3900          # Telegram 上限 4096，留点余量
OK_TAG = "✅"
FAIL_TAG = "❌"
WARN_TAG = "⚠️"


def header_line(ok: bool = True, warn: bool = False) -> str:
    """通知正文第一行: 成功 / 告警 / 失败。"""
    if not ok:
        return f"{FAIL_TAG} FGO 签到失败"
    if warn:
        return f"{WARN_TAG} FGO 签到成功(有告警)"
    return f"{OK_TAG} FGO 签到成功"


def _env(*names: str, default: str = "") -> str:
    """按顺序取第一个非空环境变量。"""
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def notify_mode() -> str:
    mode = _env("NOTIFY_ON", default="always").lower()
    return mode if mode in ("always", "fail", "off") else "always"


# ---------------- 配置解析 ----------------

def _split_base_topic(url: str) -> tuple[str, str]:
    """把 https://ntfy.sh/主题 拆成 (服务器, 主题); 支持自建服务器带路径前缀。"""
    clean = url.rstrip("/")
    base, _, topic = clean.rpartition("/")
    return (base or "https://ntfy.sh"), topic


def ntfy_config() -> dict | None:
    url = _env("NTFY_URL")
    topic = _env("NTFY_TOPIC")
    server = _env("NTFY_SERVER", default="https://ntfy.sh").rstrip("/")
    if not url and not topic:
        return None
    if topic:
        base = server
    else:
        base, topic = _split_base_topic(url)
        if not topic:
            return None
    return {
        "url": url or f"{server}/{topic}",       # raw 模式用得到
        "base": base,                            # json 模式发到服务器根, 主题放正文
        "topic": topic,
        "mode": _env("NTFY_MODE", default="json").lower(),
        "token": _env("NTFY_TOKEN"),
        "title": _env("NOTIFY_TITLE", default=_env("NTFY_TITLE", default="FGO 签到")),
        "priority": _env("NTFY_PRIORITY", default="3"),
        "tags": _env("NTFY_TAGS"),
    }


def telegram_config() -> dict | None:
    token, chat_id = _env("TG_BOT_TOKEN"), _env("TG_CHAT_ID")
    if not token or not chat_id:
        return None
    return {"token": token, "chat_id": chat_id, "parse_mode": _env("TG_PARSE_MODE")}


def channels() -> list[tuple[str, dict]]:
    out = []
    ntfy = ntfy_config()
    if ntfy:
        out.append(("ntfy", ntfy))
    tg = telegram_config()
    if tg:
        out.append(("telegram", tg))
    return out


def should_send(ok: bool) -> bool:
    """是否应该发这次通知。"""
    if notify_mode() == "off" or not channels():
        return False
    if notify_mode() == "fail" and ok:
        return False
    return True


# ---------------- 请求构造（与网络分离，便于离线自检） ----------------

def _priority(cfg: dict, ok: bool, warn: bool = False) -> int:
    """失败固定 4；告警至少 4；成功用配置值（非法值回落 3，超出 1-5 截断）。"""
    if not ok:
        return 4
    try:
        value = max(1, min(5, int(cfg.get("priority") or 3)))
    except (TypeError, ValueError):
        value = 3
    return max(value, 4) if warn else value


def _tags(cfg: dict) -> list[str]:
    return [t.strip() for t in str(cfg.get("tags") or "").split(",") if t.strip()]


def ntfy_request(cfg: dict, text: str, ok: bool = True,
                 warn: bool = False) -> tuple[str, dict, bytes]:
    """构造 ntfy 请求。默认走 JSON 发布（UTF-8 正文），中文标题不会踩 HTTP 头的 latin-1 限制。"""
    if (cfg.get("mode") or "json") == "raw":
        # HTTP 头只能是 latin-1: 标题里的非 ASCII 字符只能丢掉, 否则发送时报 UnicodeEncodeError
        title = str(cfg.get("title") or "").encode("latin-1", "ignore").decode("latin-1").strip()
        headers = {"Priority": str(_priority(cfg, ok, warn)), "Markdown": "no"}
        if title:
            headers["Title"] = title
        tags = _tags(cfg)
        if tags:
            headers["Tags"] = ",".join(tags)
        if cfg.get("token"):
            headers["Authorization"] = f"Bearer {cfg['token']}"
        return cfg["url"], headers, text.encode("utf-8")

    payload: dict = {
        "topic": cfg["topic"],
        "message": text,
        "title": str(cfg.get("title") or ""),
        "priority": _priority(cfg, ok, warn),
    }
    tags = _tags(cfg)
    if tags:
        payload["tags"] = tags
    headers = {"Content-Type": "application/json"}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return cfg["base"], headers, body


def telegram_request(cfg: dict, text: str, ok: bool = True) -> tuple[str, dict]:
    url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
    data: dict = {
        "chat_id": cfg["chat_id"],
        "text": text[:TG_TEXT_LIMIT],
        "disable_web_page_preview": True,
    }
    if cfg.get("parse_mode"):
        data["parse_mode"] = cfg["parse_mode"]
    return url, data


# ---------------- 发送 ----------------

def send_ntfy(cfg: dict, text: str, ok: bool = True, warn: bool = False) -> tuple[bool, str]:
    url, headers, body = ntfy_request(cfg, text, ok, warn)
    resp = requests.post(url, data=body, headers=headers, timeout=TIMEOUT)
    if resp.status_code >= 300:
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    return True, f"HTTP {resp.status_code}"


def send_telegram(cfg: dict, text: str, ok: bool = True, warn: bool = False) -> tuple[bool, str]:
    url, data = telegram_request(cfg, text, ok)
    resp = requests.post(url, data=data, timeout=TIMEOUT)
    detail = resp.text[:160]
    if resp.status_code >= 300:
        return False, f"HTTP {resp.status_code}: {detail}"
    try:
        payload = resp.json()
    except Exception:
        return True, f"HTTP {resp.status_code}"
    if not payload.get("ok", False):
        return False, f"API 返回失败: {detail}"
    return True, "ok"


def send_report(text: str, ok: bool = True, warn: bool = False,
                log=print) -> list[tuple[str, bool, str]]:
    """把文本推到所有已配置的通道。返回 [(通道, 是否成功, 详情)]；任何异常都不会抛出。"""
    if not should_send(ok):
        return []
    head = header_line(ok, warn)
    body = text if text.startswith(head) else f"{head}\n{text}"
    body = body.strip()
    results: list[tuple[str, bool, str]] = []
    for name, cfg in channels():
        try:
            success, detail = (send_ntfy(cfg, body, ok, warn) if name == "ntfy"
                               else send_telegram(cfg, body, ok, warn))
        except Exception as exc:                      # 推送失败绝不能影响签到
            success, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, success, detail))
        log(f"推送 {name}: {'成功' if success else '失败'} ({detail})")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="签到结果推送测试 (ntfy / Telegram)")
    parser.add_argument("--test", action="store_true", help="发送一条测试消息")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发送的内容, 不联网")
    parser.add_argument("--text", default="", help="发送指定文本")
    parser.add_argument("--fail", action="store_true", help="按失败通知发送(用于验证失败样式)")
    parser.add_argument("--warn", action="store_true", help="按告警通知发送(用于验证告警样式)")
    args = parser.parse_args()

    text = args.text or (f"这是一条测试消息。\n时间: "
                         f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    ok = not args.fail
    warn = args.warn and ok
    conf = channels()
    print(f"NOTIFY_ON = {notify_mode()}")
    print(f"已配置通道: {[name for name, _ in conf] or '无 —— 请先设置 NTFY_URL 或 TG_BOT_TOKEN/TG_CHAT_ID'}")
    if not conf:
        return 1

    if args.dry_run:
        body = f"{header_line(ok, warn)}\n{text}"
        for name, cfg in conf:
            if name == "ntfy":
                url, headers, payload = ntfy_request(cfg, body, ok, warn)
                safe = dict(headers)
                if "Authorization" in safe:
                    safe["Authorization"] = "Bearer <已隐藏>"
                print(f"\n[ntfy] POST {url}\n  头: {safe}\n  正文:\n{payload.decode('utf-8')}")
            else:
                url, data = telegram_request(cfg, body, ok)
                safe = dict(data)
                safe["chat_id"] = f"{safe['chat_id'][:3]}…(已隐藏)"
                print(f"\n[telegram] POST {url.replace(cfg['token'], '<token>')}\n  表单: {safe}")
        print("\n(--dry-run, 未联网、未发送)")
        return 0

    if not args.test and not args.text:
        parser.print_help()
        return 1
    results = send_report(text, ok=ok, warn=warn)
    failed = [r for r in results if not r[1]]
    print("\n全部成功 ✓" if results and not failed else f"\n失败 {len(failed)} 条")
    return 0 if results and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
