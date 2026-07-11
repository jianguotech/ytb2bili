"""命令行入口：子命令 + 统一 --json 输出。

设计目标：对 AI/脚本友好。加 ``--json`` 时，stdout 只输出一个 JSON 对象
（login 例外，为流式 NDJSON），退出码 0=成功、1=业务错误、2=用法错误。
人读模式下输出中文摘要。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, biliapi, deps, download as dl, login as login_mod, upload as up
from .config import Config
from .result import Result, Ytb2biliError, fail, ok


def _print(result: Result, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
    else:
        _human(result)
    return 0 if result.ok else 1


def _human(r: Result) -> None:
    if not r.ok and r.error:
        print(f"✗ [{r.error.get('code')}] {r.error.get('message')}", file=sys.stderr)
        avail = r.error.get("available")
        if avail:
            for p in avail:
                print(f"    {p['tid']:>4}  {p['parent']} → {p['name']}", file=sys.stderr)
        return
    cmd = r.command
    d = r.data
    if cmd == "doctor":
        s = d
        print(f"平台: {s['platform']}  Python {s['python']}")
        y = s["yt_dlp"]; b = s["biliup"]
        print(f"yt-dlp : {'✓ ' + (y['version'] or '') if y['installed'] else '✗ 未安装'}")
        print(f"biliup : {'✓ ' + (b['version'] or '') if b['installed'] else '✗ 未安装'}")
        print(f"ffmpeg : {'✓ ' + s['ffmpeg'] if s.get('ffmpeg') else '✗ 缺失'}")
        rt = s.get("js_runtime")
        print(f"JS运行时: {'✓ ' + rt if rt else '✗ 缺失(运行 doctor --install 自动装 deno)'}")
        print(f"bin 目录: {s['bin_dir']}")
    elif cmd == "whoami":
        print(f"✓ 已登录: {d.get('uname')} (UID {d.get('mid')}, Lv{d.get('level')})")
    elif cmd == "download":
        print(f"✓ 下载完成: {d.get('title')}")
        print(f"  文件: {d.get('video')}  ({d.get('width')}x{d.get('height')}, "
              f"{(d.get('size_bytes') or 0)//1024//1024}MB)")
        if d.get("cover"):
            print(f"  封面: {d.get('cover')}")
    elif cmd == "export-cookies":
        print(f"✓ 已导出 YouTube cookie: {d.get('youtube_cookies')} ({d.get('lines')} 行)")
        print("  以后下载会自动用它，不再弹钥匙串。")
    elif cmd == "upload":
        print(f"✓ 投稿成功: {d.get('bvid')}  分区 {d.get('tid')} "
              f"{d.get('tid_name') or ''}")
        print(f"  {d.get('url')}")
        print("  稿件将先审核/转码，通过后才公开显示。")
    elif cmd == "transfer":
        u = d.get("upload", {})
        print(f"✓ 搬运完成: {u.get('bvid')}  {u.get('url')}")
    elif cmd == "status":
        print(f"{d.get('bvid')}  [{d.get('state')}] {d.get('state_desc')}  "
              f"分区 {d.get('tid')}")
        if d.get("reject_reason"):
            print(f"  打回原因: {d.get('reject_reason')}")
    elif cmd == "tid-list":
        for p in d.get("partitions", []):
            print(f"{p['tid']:>4}  {p['parent']} → {p['name']}")
    elif cmd == "config":
        for k, v in d.items():
            print(f"{k}: {v}")
    else:
        print(json.dumps(d, ensure_ascii=False, indent=2))


# --------------------------------------------------------------- 子命令 ----

def cmd_doctor(args, cfg) -> Result:
    if args.install:
        deps.ensure_ytdlp()
        deps.ensure_biliup()
        deps.ensure_deno()
    return ok("doctor", **deps.status())


def cmd_login(args, cfg) -> Result:
    info = login_mod.prepare_qr(cfg.cookies_file)
    if args.json:
        # 流式：先吐二维码事件，用户扫码，再吐结果
        print(json.dumps({"event": "qrcode", "qr_png": info["qr_png"],
                          "qr_url": info["qr_url"],
                          "cookies_file": info["cookies_file"]}, ensure_ascii=False),
              flush=True)
    else:
        print("请用哔哩哔哩 App 扫描二维码并确认登录：\n")
        print(info["qr_terminal"])
        if info["qr_png"]:
            login_mod.open_image(__import__("pathlib").Path(info["qr_png"]))
            print(f"（也已用图片打开: {info['qr_png']}）")
    try:
        res = biliapi.poll_login(
            info["auth_code"], cfg.cookies_file,
            on_event=(lambda s: _login_event(s, args.json)),
        )
    except Ytb2biliError as e:
        return fail("login", e.code, e.message, **e.details)
    return ok("login", **res)


def _login_event(state: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"event": "status", "status": state}, ensure_ascii=False), flush=True)
    else:
        msg = {"waiting": "等待扫码…", "scanned": "已扫描，请在手机上确认…",
               "expired": "二维码已过期"}.get(state, state)
        print(msg, file=sys.stderr, flush=True)


def cmd_whoami(args, cfg) -> Result:
    return ok("whoami", **biliapi.whoami(cfg.cookies_file))


def cmd_download(args, cfg) -> Result:
    data = dl.download(
        args.url, cfg,
        output_dir=args.output_dir,
        quality=args.quality,
        cookies_from_browser=args.cookies_from_browser,
        progress=not args.json,
    )
    return ok("download", **data)


def cmd_export_cookies(args, cfg) -> Result:
    data = dl.export_cookies(cfg, args.browser, out_file=args.output)
    # 顺手写进配置，之后自动使用，不再弹钥匙串
    cfg.youtube_cookies = data["youtube_cookies"]
    cfg.save()
    data["saved_to_config"] = True
    return ok("export-cookies", **data)


def cmd_upload(args, cfg) -> Result:
    data = up.upload(
        args.video, cfg,
        title=args.title,
        tid=args.tid,
        tag=args.tag or "",
        desc=args.desc or "",
        source=args.source or "",
        copyright=args.copyright,
        cover=args.cover or "",
        line=args.line,
        submit=args.submit,
        check_tid=not args.no_check_tid,
    )
    return ok("upload", **data)


def cmd_transfer(args, cfg) -> Result:
    d = dl.download(
        args.url, cfg,
        output_dir=args.output_dir,
        quality=args.quality,
        cookies_from_browser=args.cookies_from_browser,
        progress=not args.json,
    )
    title = args.title or d.get("title") or d["id"]
    desc = args.desc or _default_desc(d)
    u = up.upload(
        d["video"], cfg,
        title=title,
        tid=args.tid,
        tag=args.tag or "",
        desc=desc,
        source=d["source_url"],
        copyright=2,
        cover=d.get("cover") or "",
        line=args.line,
        submit=args.submit,
        check_tid=not args.no_check_tid,
    )
    return ok("transfer", download=d, upload=u)


def _default_desc(d: dict) -> str:
    up_name = d.get("uploader") or "原作者"
    return (f"本视频转载自 YouTube，原作者：{up_name}\n"
            f"原视频地址：{d.get('source_url')}\n"
            f"搬运仅作分享交流，版权归原作者所有，如有侵权请联系删除。")


def cmd_status(args, cfg) -> Result:
    return ok("status", **biliapi.archive_status(cfg.cookies_file, args.bvid))


def cmd_tid_list(args, cfg) -> Result:
    parts = biliapi.partitions(cfg.cookies_file)
    if args.query:
        q = args.query
        parts = [p for p in parts if q in p["name"] or q in p["parent"]]
    return ok("tid-list", partitions=parts)


def cmd_config(args, cfg) -> Result:
    changed = False
    for key in ("download_dir", "default_tid", "default_copyright",
                "cookies_from_browser", "youtube_cookies", "quality",
                "biliup_line", "cookies_file"):
        val = getattr(args, key, None)
        if val is not None:
            if key in ("default_tid", "default_copyright", "quality"):
                val = int(val)
            setattr(cfg, key, val)
            changed = True
    if changed:
        cfg.save()
    return ok("config", **cfg.as_dict())


# ------------------------------------------------------------- argparse ----

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ytb2bili",
        description="命令行把 YouTube 视频搬运到哔哩哔哩（下载+扫码登录+投稿）。",
    )
    p.add_argument("--version", action="version", version=f"ytb2bili {__version__}")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（AI/脚本友好）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor", help="检查/安装依赖 (yt-dlp, biliup)")
    sp.add_argument("--install", action="store_true", help="缺失则自动安装")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("login", help="扫码登录 B 站，保存 cookies")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("whoami", help="查看当前登录账号")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("export-cookies",
                        help="从浏览器导出 YouTube cookie 到文件（一次授权，之后不再弹钥匙串）")
    sp.add_argument("--browser", default="chrome",
                    help="浏览器名：chrome/edge/firefox/safari/brave 等")
    sp.add_argument("-o", "--output", help="输出文件路径，默认数据目录下 youtube_cookies.txt")
    sp.set_defaults(func=cmd_export_cookies)

    sp = sub.add_parser("download", help="下载 YouTube 视频")
    sp.add_argument("url")
    sp.add_argument("-o", "--output-dir")
    sp.add_argument("-q", "--quality", type=int, help="最高画质高度，默认 1080")
    sp.add_argument("--cookies-from-browser", help="从浏览器读 YouTube cookie，如 chrome")
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("upload", help="投稿本地视频到 B 站")
    sp.add_argument("video")
    sp.add_argument("--title", required=True)
    sp.add_argument("--tid", type=int, help="分区号，见 tid-list")
    sp.add_argument("--tag", help="逗号分隔标签")
    sp.add_argument("--desc", help="简介")
    sp.add_argument("--source", help="转载来源链接（copyright=2 必填）")
    sp.add_argument("--copyright", type=int, choices=[1, 2], help="1=自制 2=转载")
    sp.add_argument("--cover", help="封面图片路径")
    sp.add_argument("--line", help="上传线路，留空自动测速")
    sp.add_argument("--submit", default="app", choices=["client", "app", "web"])
    sp.add_argument("--no-check-tid", action="store_true", help="跳过分区校验")
    sp.set_defaults(func=cmd_upload)

    sp = sub.add_parser("transfer", help="一条命令：下载 YouTube → 投稿到 B 站（转载）")
    sp.add_argument("url")
    sp.add_argument("--title", help="不填则用 YouTube 原标题")
    sp.add_argument("--tid", type=int)
    sp.add_argument("--tag")
    sp.add_argument("--desc")
    sp.add_argument("-o", "--output-dir")
    sp.add_argument("-q", "--quality", type=int)
    sp.add_argument("--cookies-from-browser")
    sp.add_argument("--line")
    sp.add_argument("--submit", default="app", choices=["client", "app", "web"])
    sp.add_argument("--no-check-tid", action="store_true")
    sp.set_defaults(func=cmd_transfer)

    sp = sub.add_parser("status", help="查询稿件状态")
    sp.add_argument("bvid")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("tid-list", help="列出当前有效分区（含 tid）")
    sp.add_argument("query", nargs="?", help="按分区名过滤，如 游戏")
    sp.set_defaults(func=cmd_tid_list)

    sp = sub.add_parser("config", help="查看/修改默认配置")
    sp.add_argument("--download-dir")
    sp.add_argument("--default-tid")
    sp.add_argument("--default-copyright")
    sp.add_argument("--cookies-from-browser")
    sp.add_argument("--youtube-cookies")
    sp.add_argument("--quality")
    sp.add_argument("--biliup-line")
    sp.add_argument("--cookies-file")
    sp.set_defaults(func=cmd_config)

    return p


def main(argv=None) -> int:
    # Windows 控制台默认 GBK，直接打印 ✓ 等字符会 UnicodeEncodeError；统一切到 UTF-8。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    # 允许 --json 放在子命令前或后
    as_json = getattr(args, "json", False)
    cfg = Config.load()
    try:
        result = args.func(args, cfg)
    except Ytb2biliError as e:
        result = fail(args.cmd, e.code, e.message, **e.details)
    except KeyboardInterrupt:
        result = fail(args.cmd, "interrupted", "已取消")
    except Exception as e:  # noqa: BLE001
        result = fail(args.cmd, "internal_error", str(e))
    # login 已自行流式输出结果之外的事件；此处仍统一打印最终结果
    return _print(result, as_json)


if __name__ == "__main__":
    raise SystemExit(main())
