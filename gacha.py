# -*- coding: utf-8 -*-
"""FGO B服「剧情池每日免费单抽」小工具。

用法:
    python gacha.py                     # 只检查: 列出可见卡池 + 今日免费单抽状态(不抽卡)
    python gacha.py --list              # 打印会话里能看到的 mstGacha / userGacha 明细
    python gacha.py --id 1234 --draw    # 指定卡池 ID, 抽 1 次(仅在免费状态可用时)
    python gacha.py --dump out.json     # 把原始响应存下来分析

环境变量(优先级低于命令行):
    FGO_GACHA_ID            剧情池卡池 ID(国服 2026-10 为 500139; 获取办法见 --list 一节)
    FGO_GACHA_NUM           抽数, 默认 1
    FGO_GACHA_DRAW          置 1 才会真正抽卡 (默认只检查)
    FGO_GACHA_ASSUME_FREE   置 1 表示"主数据看不到 freeDrawFlag 也当它免费"
    FGO_GACHA_FREE_UNTIL    免费活动截止日期 YYYY-MM-DD (默认 2026-12-31, 国服十周年活动)
    FGO_GACHA_FREE_WINDOW   置 0 关闭活动窗口校验(不推荐)

设计原则: 默认只读。任何一次真正的抽卡都必须同时满足
  1) 卡池 freeDrawFlag != 0 或显式 --assume-free
  2) userGacha.freeDrawAt 早于今日重置时刻(userGame.zerotime)
  3) 当前时间在免费活动窗口内
抽完之后会核对服务端返回的增量: 若呼符/圣晶石减少, 会明确提示"这次不是免费抽"。
"""
import argparse
import json
import os
import sys
import time

import fgo_cn

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

AUTH_FILE = "auth.json"
CONFIG_FILE = "gacha.json"
DEFAULT_FREE_UNTIL = "2026-12-31"  # 国服十周年「剧情池每日免费单抽」活动截止(可覆盖)
_START = time.time()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_date(value: str | None) -> int | None:
    """把 YYYY-MM-DD / 纯数字(epoch) 解析成时间戳; 空值返回 None。"""
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    if v.isdigit():
        return int(v)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(v, fmt)))
        except ValueError:
            continue
    raise SystemExit(f"无法解析日期 {value!r}, 请用 YYYY-MM-DD")


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except FileNotFoundError:
        return {}


def load_auth(path: str | None = None) -> tuple[dict, str]:
    """返回 (凭据, 来源描述)。

    优先级: --auth PATH > 环境变量 FGO_AUTH_FILE > 环境变量 FGO_AUTH_JSON > ./auth.json。
    探测/测试时务必用 --auth 指向副号凭据, 别把主号打进去。
    """
    env_path = os.environ.get("FGO_AUTH_FILE", "").strip()
    if path or env_path:
        target = path or env_path
        with open(target, encoding="utf-8") as f:
            return json.load(f), target
    env = os.environ.get("FGO_AUTH_JSON", "").strip().lstrip("\ufeff")
    if env:
        return json.loads(env), "env:FGO_AUTH_JSON"
    with open(AUTH_FILE, encoding="utf-8") as f:
        return json.load(f), AUTH_FILE


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "关")


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))


def print_pools(payload: dict) -> None:
    rows = fgo_cn.gacha_master_rows(payload)
    if not rows:
        print("(这次服务端没有下发任何 mstGacha 主数据 —— 常驻池没变化时就是这样)")
    else:
        print(f"可见卡池主数据 ({len(rows)} 个):")
        for r in rows:
            print(f"  id={r.get('id'):<10} freeDrawFlag={r.get('freeDrawFlag')} "
                  f"type={r.get('type')} maxDrawNum={r.get('maxDrawNum')} "
                  f"closedAt={fmt_ts(int(r['closedAt']) if str(r.get('closedAt') or '').isdigit() else 0)}"
                  f"  {r.get('name')}")
    user_rows = fgo_cn.user_gacha_rows(payload)
    game = fgo_cn.user_game_row(payload)
    print(f"\nuserGame: 圣晶石={game.get('stone')} 免费石={game.get('freeStone')} "
          f"有偿石={game.get('chargeStone')} 今日重置={fmt_ts(int(game.get('zerotime') or 0))}")
    ticket = fgo_cn.user_item_num(payload, fgo_cn.ITEM_TICKET)
    print(f"呼符(item {fgo_cn.ITEM_TICKET}): {ticket if ticket is not None else '未知'}")
    recent = sorted(user_rows.values(), key=lambda r: int(r.get("freeDrawAt") or 0), reverse=True)[:10]
    print(f"\n最近有过免费抽记录的卡池 (userGacha 共 {len(user_rows)} 条, 显示 10 条):")
    for r in recent:
        print(f"  gachaId={r.get('gachaId'):<10} freeDrawAt={fmt_ts(int(r.get('freeDrawAt') or 0))}"
              f"  num={r.get('num')} tenNum={r.get('tenNum')} status={r.get('status')}")


def print_result(res: dict) -> None:
    print("\n===== 抽卡结果 =====")
    if not res.get("drawn"):
        print("未执行抽卡")
        return
    result = res.get("result") or {}
    if not result.get("ok"):
        print(f"失败: resCode={result.get('code')} {result.get('fail')}")
        return
    verdict = {"free": "免费次数", "paid": "消耗了资源", "unknown": "无法判断(看 resource)"}
    print(f"卡池: id={res.get('pool_id')} {res.get('pool_name') or ''}")
    print(f"本次判定: {verdict.get(result.get('verdict'), result.get('verdict'))}")
    cards = result.get("cards") or []
    if cards:
        print(f"获得 {len(cards)} 张卡:")
        for c in cards:
            bits = " ".join(f"{k}={v}" for k, v in c.items())
            print(f"  - {bits}")
    else:
        print("获得卡面数据未在响应里找到(服务端字段可能不同), 原始 success keys: "
              f"{result.get('success_keys')}")
    if result.get("items"):
        print("道具变化(userItem 增量):", json.dumps(result["items"], ensure_ascii=False))
    if result.get("resource"):
        print("资源变化:", json.dumps(result["resource"], ensure_ascii=False))
    if result.get("game_after"):
        print("抽卡后:", json.dumps(result["game_after"], ensure_ascii=False))
    if result.get("free_draw_at"):
        print("该卡池 freeDrawAt 更新为:", fmt_ts(result["free_draw_at"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="FGO B服剧情池每日免费单抽")
    parser.add_argument("--auth", metavar="PATH",
                        help="凭据文件(默认 auth.json; 探测副号请显式指定, 免得动到主号)")
    parser.add_argument("--id", type=int, help="卡池 ID (gachadraw 的 id 参数)")
    parser.add_argument("--num", type=int, help="抽数, 默认 1")
    parser.add_argument("--draw", action="store_true", help="真正执行抽卡(默认只检查)")
    parser.add_argument("--dry-run", action="store_true", help="显式只检查(与默认一致)")
    parser.add_argument("--assume-free", action="store_true", help="主数据看不到免费标记也认为免费")
    parser.add_argument("--free-until", help="免费活动截止 YYYY-MM-DD (默认 2026-12-31)")
    parser.add_argument("--no-window", action="store_true", help="不做活动窗口校验")
    parser.add_argument("--list", action="store_true", help="只打印卡池/免费状态明细")
    parser.add_argument("--dump", metavar="PATH", help="把原始响应写入文件(JSON)")
    parser.add_argument("--id-param", help="卡池 ID 的参数名(默认 id; 以抓包为准, 如 gachaId[] )")
    parser.add_argument("--extra", help='额外请求参数 JSON, 如 \'{"type":1}\'')
    args = parser.parse_args()

    cfg = load_config()
    gacha_id = args.id if args.id is not None else (
        int(os.environ["FGO_GACHA_ID"]) if os.environ.get("FGO_GACHA_ID", "").strip().isdigit()
        else (int(cfg["gacha_id"]) if str(cfg.get("gacha_id") or "").isdigit() else None))
    num = args.num or int(os.environ.get("FGO_GACHA_NUM") or cfg.get("num") or 1)
    assume_free = args.assume_free or env_flag("FGO_GACHA_ASSUME_FREE", bool(cfg.get("assume_free")))
    do_draw = (args.draw or env_flag("FGO_GACHA_DRAW", bool(cfg.get("draw")))) and not args.dry_run
    free_until_raw = args.free_until or os.environ.get("FGO_GACHA_FREE_UNTIL") or \
        cfg.get("free_until") or DEFAULT_FREE_UNTIL
    if args.no_window or os.environ.get("FGO_GACHA_FREE_WINDOW", "").strip() == "0":
        free_until = None
    else:
        free_until = parse_date(free_until_raw)

    id_param = (args.id_param or os.environ.get("FGO_GACHA_ID_PARAM", "").strip()
                or str(cfg.get("id_param") or "id"))
    extra = cfg.get("extra") if isinstance(cfg.get("extra"), dict) else {}
    raw_extra = (args.extra or os.environ.get("FGO_GACHA_EXTRA", "")).strip()
    if raw_extra:
        try:
            extra = json.loads(raw_extra)
        except json.JSONDecodeError:
            raise SystemExit(f"--extra 不是合法 JSON: {raw_extra}")
    if not isinstance(extra, dict):
        extra = {}

    auth, auth_src = load_auth(args.auth)
    log(f"登录: 御主={auth.get('nickname', '?')} 平台={auth.get('platform', 'android_bili')} "
        f"凭据={auth_src}")
    session, payload = fgo_cn.open_session(
        access_token=auth["access_token"], mid=int(auth["mid"]),
        username=auth.get("username") or "master", nickname=auth["nickname"],
        device_id=auth["device_id"], platform_id=auth.get("platform", "android_bili"),
        log_cb=log,
    )

    if args.list:
        print()
        print_pools(payload)
        log(f"完成, 耗时 {time.time() - _START:.1f}s")
        return 0

    log(f"模式: {'实抽' if do_draw else '只检查'}  抽数={num}  "
        f"活动截止={free_until_raw if free_until else '不校验'}  "
        f"参数名={id_param}" + (f" 额外参数={extra}" if extra else ""))
    res = fgo_cn.daily_free_draw(
        session, payload, gacha_id=gacha_id, num=num, assume_free=assume_free,
        free_until=free_until, do_draw=do_draw, log_cb=log, id_param=id_param, extra=extra,
    )
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump({"toplogin": payload, "gacha": res.get("result")}, f,
                      ensure_ascii=False, indent=2)
        log(f"原始响应已写入 {args.dump}")
    print_result(res)
    if not do_draw:
        print("\n(当前是只检查模式; 确认无误后加 --draw 或设 FGO_GACHA_DRAW=1 才会真正抽)")
    log(f"完成, 耗时 {time.time() - _START:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
