# -*- coding: utf-8 -*-
"""离线自检: 推送(notify)与签到报告文本。不联网。

用法:  python selftest_notify.py
"""
import os
import sys

import checkin
import notify

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

FAILED = []
ALL_ENV = ["NOTIFY_ON", "NOTIFY_TITLE", "NTFY_URL", "NTFY_TOPIC", "NTFY_SERVER",
           "NTFY_TOKEN", "NTFY_PRIORITY", "NTFY_TAGS", "TG_BOT_TOKEN", "TG_CHAT_ID",
           "TG_PARSE_MODE"]


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" (期望 {want!r})"))
    if not ok:
        FAILED.append(name)


class Env:
    """临时设置环境变量, 退出时恢复。"""

    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in ALL_ENV}
        for k in ALL_ENV:
            os.environ.pop(k, None)
        for k, v in self.kv.items():
            os.environ[k] = str(v)
        return self

    def __exit__(self, *exc):
        for k in ALL_ENV:
            os.environ.pop(k, None)
        for k, v in self.saved.items():
            if v is not None:
                os.environ[k] = v


# ---------- 配置解析 ----------
with Env():                                     # 什么都没配
    check("未配置 -> 无通道", notify.channels(), [])
    check("未配置 -> 不发", notify.should_send(True), False)
    check("未配置 -> send_report 返回空", notify.send_report("x", log=lambda *_: None), [])

with Env(NTFY_URL="https://ntfy.sh/t1"):
    cfg = notify.ntfy_config() or {}
    check("NTFY_URL 直接用", cfg.get("url"), "https://ntfy.sh/t1")
    check("默认标题", cfg.get("title"), "FGO 签到")
    check("默认优先级", cfg.get("priority"), "3")
    check("只有 ntfy 时渠道数", len(notify.channels()), 1)

with Env(NTFY_TOPIC="t2", NTFY_SERVER="https://my.ntfy.example/"):
    check("TOPIC+SERVER 拼接(去尾斜杠)", (notify.ntfy_config() or {}).get("url"),
          "https://my.ntfy.example/t2")

with Env(NTFY_TOPIC="t3"):
    check("TOPIC 时默认服务器", (notify.ntfy_config() or {}).get("url"), "https://ntfy.sh/t3")

with Env(TG_BOT_TOKEN="123:abc"):
    check("TG 只有 token -> 不算配置", notify.telegram_config(), None)
with Env(TG_BOT_TOKEN="123:abc", TG_CHAT_ID="42"):
    check("TG 配齐", (notify.telegram_config() or {}).get("chat_id"), "42")

# ---------- 开关 ----------
with Env(NTFY_URL="https://ntfy.sh/t1", NOTIFY_ON="off"):
    check("NOTIFY_ON=off -> 不发", notify.should_send(False), False)
with Env(NTFY_URL="https://ntfy.sh/t1", NOTIFY_ON="fail"):
    check("NOTIFY_ON=fail + 成功 -> 不发", notify.should_send(True), False)
    check("NOTIFY_ON=fail + 失败 -> 发", notify.should_send(False), True)
with Env(NTFY_URL="https://ntfy.sh/t1"):
    check("默认 always -> 成功也发", notify.should_send(True), True)
with Env(NTFY_URL="https://ntfy.sh/t1", NOTIFY_ON="乱填"):
    check("非法值回落到 always", notify.notify_mode(), "always")

# ---------- 请求构造 ----------
with Env():
    cfg = {"url": "https://ntfy.sh/t1", "title": "T", "priority": "3",
           "tags": "video_game", "token": "tk"}
    url, headers, body = notify.ntfy_request(cfg, "hello", ok=True)
    check("ntfy url", url, "https://ntfy.sh/t1")
    check("ntfy 成功用配置优先级", headers["Priority"], "3")
    check("ntfy 失败用优先级 4", notify.ntfy_request(cfg, "x", ok=False)[1]["Priority"], "4")
    check("ntfy 带 Tags", headers.get("Tags"), "video_game")
    check("ntfy 带 Bearer", headers.get("Authorization"), "Bearer tk")
    check("ntfy 正文用 UTF-8 字节", body, "hello".encode("utf-8"))
    _, h2, _ = notify.ntfy_request({"url": url, "title": "T"}, "x")
    check("ntfy 无 token 时不带 Authorization", "Authorization" in h2, False)

with Env():
    cfg = {"token": "123:abc", "chat_id": "42", "parse_mode": ""}
    url, data = notify.telegram_request(cfg, "hello")
    check("TG url 带 token", url, "https://api.telegram.org/bot123:abc/sendMessage")
    check("TG chat_id", data["chat_id"], "42")
    check("TG 默认不带 parse_mode", "parse_mode" in data, False)
    long_text = "x" * (notify.TG_TEXT_LIMIT + 500)
    check("TG 超长截断", len(notify.telegram_request(cfg, long_text)[1]["text"]),
          notify.TG_TEXT_LIMIT)

# ---------- 报告正文 ----------
PAYLOAD = {
    "cache": {
        "serverTime": 1791813409,
        "replaced": {
            "userGame": [{"name": "Aokoちゃん", "lv": 159, "stone": 73}],
            "userItem": [{"itemId": 4001, "num": 0}],
        },
        "updated": {"userLogin": [{"seqLoginCount": 4, "totalLoginCount": 1988}]},
    },
    "response": [],
}
APPLE = {"requested": 1, "converted": 0, "name": "", "ap_before": 23, "ap_after": None,
         "errors": ["AP=23/144 < 40, 未达合成阈值"], "eta_seconds": 5100}
GACHA = {"skipped": True, "notes": [],
         "state": {"reason": "今日免费单抽已使用 (freeDrawAt 晚于今日重置时刻)"}}
report = checkin.build_report(PAYLOAD, APPLE, GACHA, 11.4)
print("\n---- 报告预览 ----")
print(report)
print("------------------\n")
check("报告含签到标题", report.startswith("===== 签到成功 ====="), True)
check("报告含御主", "御主: Aokoちゃん  Lv.159" in report, True)
check("报告含圣晶石/呼符", "圣晶石: 73   呼符: 0" in report, True)
check("报告含连续/累计登录", "连续登录: 4天 / 累计: 1988天" in report, True)
check("报告含苹果跳过原因", "苹果合成: 跳过, AP=23/144 < 40, 未达合成阈值" in report, True)
check("报告含 ETA", "约 1小时25分钟后达到阈值" in report, True)
check("报告含免费单抽状态", "免费单抽: 跳过 (今日免费单抽已使用" in report, True)
check("报告末行是耗时", report.splitlines()[-1].endswith("完成, 耗时 11.4s"), True)

check("失败报告带原因", checkin.build_report({}, None, None, 3.2).splitlines()[0],
      "===== 签到成功 =====")

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("全部通过 ✅ (纯离线, 未联网)")
