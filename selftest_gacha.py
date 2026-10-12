# -*- coding: utf-8 -*-
"""离线自检: 用合成(假)响应校验抽卡结果解析与"免费次数"判定逻辑。不联网。

用法:  python selftest_gacha.py
"""
import sys

import fgo_cn

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

FAILED = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" (期望 {want!r})"))
    if not ok:
        FAILED.append(name)


ZEROTIME = 1791734400      # 今日 0 点重置时刻
NOW = ZEROTIME + 3600      # 今天 01:00
YESTERDAY_FREE = ZEROTIME - 7200

MASTER_STORY = {"id": 1234, "name": "剧情召唤", "type": 1, "freeDrawFlag": 1, "maxDrawNum": 0}
MASTER_FUKU = {"id": 59000003, "name": "第2部通关应援福袋召唤", "type": 7, "freeDrawFlag": 0,
               "maxDrawNum": 1}

# --- 卡池定位 ---
rows = fgo_cn.gacha_master_rows({"cache": {"updated": {"mstGacha": [MASTER_FUKU, MASTER_STORY]}}})
check("收集 mstGacha 行数", len(rows), 2)
check("挑出免费剧情池", fgo_cn.pick_free_story_pool(rows)["id"], 1234)

# --- 免费状态判定 ---
used_today = fgo_cn.free_draw_state(1234, MASTER_STORY, {"gachaId": 1234, "freeDrawAt": NOW},
                                    {"zerotime": ZEROTIME}, NOW)
check("今天已用过 -> 不可抽", used_today["ok"], False)
fresh = fgo_cn.free_draw_state(1234, MASTER_STORY,
                               {"gachaId": 1234, "freeDrawAt": YESTERDAY_FREE},
                               {"zerotime": ZEROTIME}, NOW)
check("昨天用过 -> 可抽", fresh["ok"], True)
never = fgo_cn.free_draw_state(1234, MASTER_STORY, None, {"zerotime": ZEROTIME}, NOW)
check("从没抽过 -> 可抽", never["ok"], True)
no_flag = fgo_cn.free_draw_state(1234, {"id": 1234, "freeDrawFlag": 0, "name": "剧情召唤"},
                                 None, {"zerotime": ZEROTIME}, NOW)
check("freeDrawFlag=0 -> 不可抽", no_flag["ok"], False)
unknown_pool = fgo_cn.free_draw_state(1234, None, None, {"zerotime": ZEROTIME}, NOW)
check("主数据缺失 + 未 assume -> 不可抽", unknown_pool["ok"], False)
assumed = fgo_cn.free_draw_state(1234, None, None, {"zerotime": ZEROTIME}, NOW, assume_free=True)
check("主数据缺失 + assume_free -> 可抽", assumed["ok"], True)
expired = fgo_cn.free_draw_state(1234, MASTER_STORY, None, {"zerotime": ZEROTIME}, NOW,
                                 free_until=ZEROTIME - 86400)
check("活动窗口已过 -> 不可抽", expired["ok"], False)

# --- 抽卡结果解析: 走了免费次数 ---
FREE_PAYLOAD = {
    "response": [{"resCode": "00", "nid": "gachadraw", "success": {
        "gachaResultList": [{"svtId": 100100, "rarity": 4, "isNew": True, "limitCount": 0}],
        "gachaId": 1234, "num": 1}}],
    "cache": {"updated": {
        "userGacha": [{"gachaId": "1234", "num": "1", "tenNum": "0", "freeDrawAt": str(NOW)}],
        "userItem": [{"itemId": "4001", "num": "7"}],
    }, "replaced": {}, "serverTime": NOW},
}
res_free = fgo_cn.gacha_draw_result(FREE_PAYLOAD, gacha_id=1234,
                                    game_row_before={"zerotime": ZEROTIME, "freeStone": 73},
                                    ticket_before=7, server_time=NOW)
check("解析: 成功", res_free["ok"], True)
check("解析: 判定免费", res_free["verdict"], "free")
check("解析: 抽出 1 张卡", len(res_free["cards"]), 1)
check("解析: 卡的 svtId", res_free["cards"][0].get("svtId"), 100100)

# --- 抽卡结果解析: 消耗了呼符(不是免费) ---
PAID_PAYLOAD = {
    "response": [{"resCode": "00", "nid": "gachadraw", "success": {"gachaId": 1234}}],
    "cache": {"updated": {
        "userGacha": [{"gachaId": "1234", "num": "2", "freeDrawAt": str(YESTERDAY_FREE)}],
        "userItem": [{"itemId": "4001", "num": "6"}],
    }, "replaced": {}, "serverTime": NOW},
}
res_paid = fgo_cn.gacha_draw_result(PAID_PAYLOAD, gacha_id=1234,
                                    game_row_before={"zerotime": ZEROTIME, "freeStone": 73},
                                    ticket_before=7, server_time=NOW)
check("解析: 判定消耗资源", res_paid["verdict"], "paid")
check("解析: 呼符 -1", res_paid["resource"].get("item4001"), -1)

# --- 抽卡结果解析: 失败 ---
FAIL_PAYLOAD = {"response": [{"resCode": "99", "nid": "0", "success": {},
                              "fail": {"detail": "扭蛋ID有误", "action": "goto_title"}}],
                "cache": {"updated": {}, "replaced": {}, "serverTime": NOW}}
res_fail = fgo_cn.gacha_draw_result(FAIL_PAYLOAD, gacha_id=1234)
check("解析: 失败 ok=False", res_fail["ok"], False)
check("解析: 失败详情", res_fail["fail"], "扭蛋ID有误")

# --- daily_free_draw: 免费次数已用 -> 不发请求 ---
class FakeSession:
    def __init__(self):
        self.calls = 0

    def action(self, *a, **kw):
        self.calls += 1
        raise AssertionError("不应该发出请求")


fake = FakeSession()
top = {"cache": {"serverTime": NOW, "replaced": {
    "userGame": [{"zerotime": ZEROTIME, "freeStone": 73}],
    "userGacha": [{"gachaId": "1234", "num": "1", "freeDrawAt": str(NOW)}],
}, "updated": {"mstGacha": [MASTER_STORY]}}}
out = fgo_cn.daily_free_draw(fake, top, gacha_id=1234, log_cb=lambda *_: None)
check("已用过 -> 跳过", out["skipped"], True)
check("已用过 -> 没发请求", fake.calls, 0)

# --- daily_free_draw: 可用但 do_draw=False -> 也不发请求 ---
fake2 = FakeSession()
top2 = {"cache": {"serverTime": NOW, "replaced": {
    "userGame": [{"zerotime": ZEROTIME, "freeStone": 73}],
    "userGacha": [{"gachaId": "1234", "num": "1", "freeDrawAt": str(YESTERDAY_FREE)}],
}, "updated": {"mstGacha": [MASTER_STORY]}}}
out2 = fgo_cn.daily_free_draw(fake2, top2, gacha_id=1234, do_draw=False,
                              log_cb=lambda *_: None)
check("可用 + 只检查 -> 发请求数 0", fake2.calls, 0)
check("可用 + 只检查 -> 未抽", out2["drawn"], False)

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("全部通过 ✅ (纯离线, 未联网)")
