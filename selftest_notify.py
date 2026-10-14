# -*- coding: utf-8 -*-
"""离线自检: 推送(notify)与签到报告文本。不联网。

用法:  python selftest_notify.py
"""
import json
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
ALL_ENV = ["NOTIFY_ON", "NOTIFY_TITLE", "NTFY_TITLE", "NTFY_URL", "NTFY_TOPIC",
           "NTFY_SERVER", "NTFY_TOKEN", "NTFY_PRIORITY", "NTFY_TAGS", "NTFY_MODE",
           "TG_BOT_TOKEN", "TG_CHAT_ID", "TG_PARSE_MODE"]


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
    check("NTFY_URL 原样保留", cfg.get("url"), "https://ntfy.sh/t1")
    check("拆出服务器", cfg.get("base"), "https://ntfy.sh")
    check("拆出主题", cfg.get("topic"), "t1")
    check("默认 json 模式", cfg.get("mode"), "json")
    check("默认标题", cfg.get("title"), "FGO 签到")
    check("只有 ntfy 时渠道数", len(notify.channels()), 1)

with Env(NTFY_URL="https://fgo.example.com/ntfy/t9"):      # 自建 + 路径前缀
    cfg = notify.ntfy_config() or {}
    check("自建: base 保留路径前缀", cfg.get("base"), "https://fgo.example.com/ntfy")
    check("自建: topic", cfg.get("topic"), "t9")

with Env(NTFY_TOPIC="t2", NTFY_SERVER="https://my.ntfy.example/"):
    cfg = notify.ntfy_config() or {}
    check("TOPIC+SERVER: base 去尾斜杠", cfg.get("base"), "https://my.ntfy.example")
    check("TOPIC+SERVER: topic", cfg.get("topic"), "t2")
    check("TOPIC+SERVER: url", cfg.get("url"), "https://my.ntfy.example/t2")

with Env(NTFY_TOPIC="t3"):
    check("TOPIC 时用默认服务器", notify.ntfy_config().get("base"), "https://ntfy.sh")

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

# ---------- ntfy 请求构造（json 模式: 中文标题不能进 HTTP 头） ----------
NTFY_CFG = {"url": "https://ntfy.sh/t1", "base": "https://ntfy.sh", "topic": "t1",
            "mode": "json", "token": "tk", "title": "FGO 签到", "priority": "3",
            "tags": "video_game,a"}
with Env():
    url, headers, body = notify.ntfy_request(NTFY_CFG, "hi 世界", ok=True)
    check("json 发到服务器根", url, "https://ntfy.sh")
    check("json Content-Type", headers.get("Content-Type"), "application/json")
    check("json 带 Bearer", headers.get("Authorization"), "Bearer tk")
    check("头部全是 latin-1 可编码(回归: 原中文标题报错)", 
          all(v.encode("latin-1") for v in headers.values()), True)
    payload = json.loads(body.decode("utf-8"))
    check("json 主题", payload["topic"], "t1")
    check("json 中文标题完整", payload["title"], "FGO 签到")
    check("json 中文正文完整", payload["message"], "hi 世界")
    check("json 优先级", payload["priority"], 3)
    check("json 标签数组", payload["tags"], ["video_game", "a"])
    check("失败 -> 优先级 4", json.loads(notify.ntfy_request(NTFY_CFG, "x", ok=False)[2])
          ["priority"], 4)
    check("告警 -> 优先级至少 4",
          json.loads(notify.ntfy_request(NTFY_CFG, "x", ok=True, warn=True)[2])["priority"], 4)
    check("告警时低优先级也提到 4",
          json.loads(notify.ntfy_request(dict(NTFY_CFG, priority="1"), "x", ok=True,
                                        warn=True)[2])["priority"], 4)
    check("非法优先级回落 3", json.loads(notify.ntfy_request(dict(NTFY_CFG, priority="abc"),
                                                            "x")[2])["priority"], 3)
    check("优先级截断到 5", json.loads(notify.ntfy_request(dict(NTFY_CFG, priority="9"),
                                                          "x")[2])["priority"], 5)

    # 通知正文首行(成功/告警/失败三态)
    check("首行: 成功", notify.header_line(True, False), "✅ FGO 签到成功")
    check("首行: 告警", notify.header_line(True, True), "⚠️ FGO 签到成功(有告警)")
    check("首行: 失败", notify.header_line(False, False), "❌ FGO 签到失败")

    # raw 模式(HTTP 头发布)必须能容忍中文标题: 只保留 ASCII 部分而不是崩
    raw_cfg = dict(NTFY_CFG, mode="raw")
    url_r, headers_r, body_r = notify.ntfy_request(raw_cfg, "hi", ok=True)
    check("raw 发到主题 URL", url_r, "https://ntfy.sh/t1")
    check("raw 中文标题只保留 ASCII 部分", headers_r.get("Title"), "FGO")
    check("raw 头部 latin-1 可编码", all(v.encode("latin-1") for v in headers_r.values()), True)
    check("raw 正文纯文本", body_r, "hi".encode("utf-8"))
    check("raw 英文标题保留", notify.ntfy_request(dict(raw_cfg, title="FGO checkin"),
                                                  "hi")[1].get("Title"), "FGO checkin")
    check("raw 无 token 时不带 Authorization",
          "Authorization" in notify.ntfy_request(dict(raw_cfg, token=""), "hi")[1], False)

# ---------- Telegram 请求构造 ----------
with Env():
    tg_cfg = {"token": "123:abc", "chat_id": "42", "parse_mode": ""}
    url, data = notify.telegram_request(tg_cfg, "hello")
    check("TG url 带 token", url, "https://api.telegram.org/bot123:abc/sendMessage")
    check("TG chat_id", data["chat_id"], "42")
    check("TG 默认不带 parse_mode", "parse_mode" in data, False)
    long_text = "x" * (notify.TG_TEXT_LIMIT + 500)
    check("TG 超长截断", len(notify.telegram_request(tg_cfg, long_text)[1]["text"]),
          notify.TG_TEXT_LIMIT)

# ---------- 报告正文 ----------
PAYLOAD = {
    "cache": {
        "serverTime": 1791813409,
        "replaced": {
            "userGame": [{"name": "测试御主", "lv": 150, "stone": 99}],
            "userItem": [{"itemId": 4001, "num": 0}],
        },
        "updated": {"userLogin": [{"seqLoginCount": 12, "totalLoginCount": 365}]},
    },
    "response": [],
}
APPLE = {"requested": 1, "converted": 0, "name": "", "ap_before": 23, "ap_after": None,
         "errors": ["AP=23/140 < 40, 未达合成阈值"], "eta_seconds": 5100}
report = checkin.build_report(PAYLOAD, APPLE, 11.4)
print("\n---- 报告预览 ----")
print(report)
print("------------------\n")
check("报告含签到标题", report.startswith("===== 签到成功 ====="), True)
check("报告含御主", "御主: 测试御主  Lv.150" in report, True)
check("报告含圣晶石/呼符", "圣晶石: 99   呼符: 0" in report, True)
check("报告含连续/累计登录", "连续登录: 12天 / 累计: 365天" in report, True)
check("报告含苹果跳过原因", "苹果合成: 跳过, AP=23/140 < 40, 未达合成阈值" in report, True)
check("报告含 ETA", "约 1小时25分钟后达到阈值" in report, True)
check("报告末行是耗时", report.splitlines()[-1].endswith("完成, 耗时 11.4s"), True)

# 呼符读数: updated 比 replaced 新, 必须优先取 updated(线上出现过 0 vs 7)
FRESH = {"cache": {"updated": {"userItem": [{"itemId": 4001, "num": 7}]},
                   "replaced": {"userGame": [{"name": "X", "lv": 1, "stone": 1}],
                                "userItem": [{"itemId": 4001, "num": 0}]}}}
check("呼符取 updated 的新值", checkin.item_num(FRESH, 4001), 7)
check("summarize 用新值", "呼符: 7" in checkin.summarize(FRESH), True)
OLD_ONLY = {"cache": {"updated": {}, "replaced": {"userItem": [{"itemId": 4001, "num": 3}]}}}
check("只有 replaced 时退回快照", checkin.item_num(OLD_ONLY, 4001), 3)
check("都没有时返回 None", checkin.item_num({}, 4001), None)

check("失败报告带原因", checkin.build_report({}, None, 3.2).splitlines()[0],
      "===== 签到成功 =====")

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("全部通过 ✅ (纯离线, 未联网)")
