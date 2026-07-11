"""哔哩哔哩接口封装：TV 端扫码签名、登录轮询、稿件状态、分区表。

签名与接口对齐 biliup-rs（TV appkey），生成的 cookies.json 与 biliup 完全兼容。
仅用标准库，跨平台无额外依赖。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .result import Ytb2biliError

# 与 biliup-rs 一致的 TV 端 appkey/appsec
TV_APPKEY = "4409e2ce8ffd12b8"
TV_APPSEC = "59b43e04ad6965f34319062b478f83dd"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 BiliApp"


def _sign(params: dict) -> str:
    q = urllib.parse.urlencode(sorted(params.items()))
    return hashlib.md5((q + TV_APPSEC).encode()).hexdigest()


def _post(url: str, params: dict, timeout: int = 20) -> dict:
    body = dict(params)
    body["sign"] = _sign(body)
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(url: str, cookie_header: str = "", timeout: int = 20) -> dict:
    headers = {"User-Agent": _UA}
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ------------------------------------------------------------- 扫码登录 ----

def request_qrcode() -> tuple[str, str]:
    """申请扫码，返回 (二维码内容url, auth_code)。"""
    r = _post(
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code",
        {"appkey": TV_APPKEY, "local_id": "0", "ts": int(time.time())},
    )
    if r.get("code") != 0:
        raise Ytb2biliError("qrcode_failed", f"申请二维码失败: {r.get('message')}", raw=r)
    return r["data"]["url"], r["data"]["auth_code"]


def poll_login(
    auth_code: str,
    cookies_file: str | Path,
    interval: float = 3.0,
    max_wait: float = 180.0,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """轮询扫码结果，成功后把登录态写入 cookies_file（biliup 兼容格式）。

    返回 {"mid":..., "cookies":[...], "cookies_file":...}。
    on_event 会被回调传入状态: waiting / scanned / expired。
    """
    deadline = time.time() + max_wait
    last = None
    while time.time() < deadline:
        time.sleep(interval)
        r = _post(
            "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll",
            {"appkey": TV_APPKEY, "auth_code": auth_code, "local_id": "0",
             "ts": int(time.time())},
        )
        code = r.get("code")
        if code == 0:
            data = r["data"]
            data["platform"] = "BiliTV"
            Path(cookies_file).parent.mkdir(parents=True, exist_ok=True)
            Path(cookies_file).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            names = [c["name"] for c in data["cookie_info"]["cookies"]]
            return {
                "mid": data.get("token_info", {}).get("mid"),
                "cookies": names,
                "cookies_file": str(cookies_file),
            }
        elif code == 86038:
            if on_event:
                on_event("expired")
            raise Ytb2biliError("qrcode_expired", "二维码已过期，请重新登录")
        elif code == 86090:
            if last != "scanned" and on_event:
                on_event("scanned")
            last = "scanned"
        elif code == 86039:
            if last != "waiting" and on_event:
                on_event("waiting")
            last = "waiting"
        else:
            raise Ytb2biliError("login_error", f"登录异常: {r.get('message')}", raw=r)
    raise Ytb2biliError("login_timeout", "扫码超时，请重试")


# ---------------------------------------------------------- cookie 工具 ----

def load_cookies(cookies_file: str | Path) -> dict:
    p = Path(cookies_file)
    if not p.exists():
        raise Ytb2biliError(
            "not_logged_in", f"未登录（找不到 {p}），请先运行: ytb2bili login"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise Ytb2biliError("cookies_corrupt", f"cookies 文件损坏: {p}", detail=str(e))


def cookie_header(cookies_file: str | Path) -> str:
    ck = load_cookies(cookies_file)
    cookies = ck.get("cookie_info", {}).get("cookies", [])
    return "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)


def whoami(cookies_file: str | Path) -> dict:
    """用 cookie 查询当前登录账号。"""
    hdr = cookie_header(cookies_file)
    r = _get("https://api.bilibili.com/x/web-interface/nav", hdr)
    data = r.get("data", {})
    if not data.get("isLogin"):
        raise Ytb2biliError("cookie_invalid", "cookie 已失效，请重新登录: ytb2bili login")
    return {
        "mid": data.get("mid"),
        "uname": data.get("uname"),
        "level": data.get("level_info", {}).get("current_level"),
        "vip": bool(data.get("vipStatus")),
    }


# ------------------------------------------------------------ 稿件状态 ----

_STATE_DESC = {
    0: "开放浏览",
    -1: "待审核",
    -2: "被打回",
    -30: "审核中",
    -60: "审核中/转码中",
    -100: "用户删除",
}


def archive_status(cookies_file: str | Path, bvid: str) -> dict:
    hdr = cookie_header(cookies_file)
    r = _get(
        f"https://member.bilibili.com/x/vupre/web/archive/view?bvid={bvid}", hdr
    )
    if r.get("code") != 0:
        raise Ytb2biliError("status_failed", f"查询失败: {r.get('message')}", raw=r)
    a = r.get("data", {}).get("archive", {})
    state = a.get("state")
    return {
        "bvid": bvid,
        "title": a.get("title"),
        "state": state,
        "state_desc": a.get("state_desc") or _STATE_DESC.get(state, "未知"),
        "reject_reason": a.get("reject_reason") or None,
        "tid": a.get("tid"),
        "copyright": a.get("copyright"),
    }


# ------------------------------------------------------------- 分区表 ----

def partitions(cookies_file: str | Path) -> list[dict]:
    """拉取当前有效的投稿分区表（扁平化：父区 + 子区）。"""
    hdr = cookie_header(cookies_file)
    r = _get("https://member.bilibili.com/x/vupre/web/archive/pre?lang=cn", hdr)
    if r.get("code") != 0:
        raise Ytb2biliError("partitions_failed", f"获取分区失败: {r.get('message')}", raw=r)
    out: list[dict] = []
    for parent in r.get("data", {}).get("typelist", []):
        pname = parent.get("name")
        for child in parent.get("children", []):
            out.append({
                "tid": child.get("id"),
                "name": child.get("name"),
                "parent": pname,
                "desc": child.get("desc", ""),
            })
    return out


def valid_tids(cookies_file: str | Path) -> dict[int, dict]:
    return {p["tid"]: p for p in partitions(cookies_file)}


def recent_archives(cookies_file: str | Path, n: int = 10) -> list[dict]:
    """列出账号最近的稿件（含审核中/转码中），用于确认投稿结果、取 bvid。"""
    hdr = cookie_header(cookies_file)
    r = _get(
        "https://member.bilibili.com/x/web/archives?status=is_pubing,pubed,not_pubed"
        f"&pn=1&ps={n}", hdr
    )
    if r.get("code") != 0:
        raise Ytb2biliError("archives_failed", f"获取稿件列表失败: {r.get('message')}", raw=r)
    out = []
    for v in r.get("data", {}).get("arc_audits", []):
        a = v.get("Archive", {})
        out.append({
            "bvid": a.get("bvid"), "aid": a.get("aid"), "title": a.get("title"),
            "state": a.get("state"), "state_desc": a.get("state_desc"), "ptime": a.get("ptime"),
        })
    return out


def find_archive_by_title(cookies_file: str | Path, title: str) -> dict | None:
    """按标题在最近稿件里找匹配项（投稿后确认用）。"""
    try:
        for a in recent_archives(cookies_file, 15):
            if a.get("title") == title:
                return a
    except Ytb2biliError:
        return None
    return None


def delete_archive(cookies_file: str | Path, aid: int) -> dict:
    """删除稿件（谨慎）。返回接口响应。"""
    ck = load_cookies(cookies_file)
    cookies = {c["name"]: c["value"] for c in ck.get("cookie_info", {}).get("cookies", [])}
    csrf = cookies.get("bili_jct", "")
    hdr = cookie_header(cookies_file)
    data = urllib.parse.urlencode({"aid": str(aid), "csrf": csrf}).encode()
    req = urllib.request.Request(
        "https://member.bilibili.com/x/vu/web/delete",
        data=data, headers={"User-Agent": _UA, "Cookie": hdr,
                            "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)
