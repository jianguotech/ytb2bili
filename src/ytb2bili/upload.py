"""biliup 封装：投稿到哔哩哔哩，并在提交前校验分区 tid。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import biliapi, deps
from .config import Config
from .result import Ytb2biliError

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


def validate_tid(cookies_file: str, tid: int) -> dict:
    """校验 tid 是否为当前有效分区。无效则抛错并给出建议。"""
    try:
        valid = biliapi.valid_tids(cookies_file)
    except Ytb2biliError:
        # 拉分区表失败时不阻塞投稿，仅跳过校验
        return {"validated": False, "tid": tid}
    if tid in valid:
        p = valid[tid]
        return {"validated": True, "tid": tid, "name": p["name"], "parent": p["parent"]}
    # 给出同名/近似建议
    suggestions = [
        {"tid": t, "name": p["name"], "parent": p["parent"]}
        for t, p in sorted(valid.items())
    ]
    raise Ytb2biliError(
        "invalid_tid",
        f"分区 tid={tid} 不是当前有效分区（B 站分区会改版）。"
        f"请用 `ytb2bili tid-list` 选择有效分区。",
        available=suggestions[:60],
    )


def upload(
    video: str,
    cfg: Config,
    title: str,
    tid: int | None = None,
    tag: str = "",
    desc: str = "",
    source: str = "",
    copyright: int | None = None,
    cover: str = "",
    line: str | None = None,
    dtime: int | None = None,
    submit: str = "app",
    check_tid: bool = True,
) -> dict:
    """调用 biliup 投稿。返回 {bvid, aid, tid, tid_name, title}。"""
    deps.ensure_biliup()
    video_path = Path(video).expanduser()
    if not video_path.exists():
        raise Ytb2biliError("file_not_found", f"视频文件不存在: {video_path}")

    tid = tid if tid is not None else cfg.default_tid
    cr = copyright if copyright is not None else cfg.default_copyright
    ln = line if line is not None else cfg.biliup_line

    tid_info = {"tid": tid}
    if check_tid:
        tid_info = validate_tid(cfg.cookies_file, tid)

    if cr == 2 and not source:
        raise Ytb2biliError(
            "missing_source", "转载稿件（copyright=2）必须提供 --source 原视频链接"
        )

    cmd = [
        deps.biliup_exe(),
        "-u", cfg.cookies_file,
        "upload", str(video_path),
        "--submit", submit,
        "--copyright", str(cr),
        "--tid", str(tid),
        "--title", title,
    ]
    if tag:
        cmd += ["--tag", tag]
    if desc:
        cmd += ["--desc", desc]
    if source:
        cmd += ["--source", source]
    if cover:
        cmd += ["--cover", str(Path(cover).expanduser())]
    if ln:
        cmd += ["--line", ln]
    if dtime:
        cmd += ["--dtime", str(dtime)]

    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=True,
                              env=deps.subprocess_env_direct())
    except subprocess.CalledProcessError as e:
        raise Ytb2biliError(
            "upload_failed",
            _clean_biliup_error(e.stderr or e.stdout),
            stderr=(e.stderr or "")[-1200:],
        )

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    bvid = _extract(combined, r'"bvid":\s*(?:String\()?"?(BV[0-9A-Za-z]{10})"?') \
        or (_BV_RE.search(combined).group(0) if _BV_RE.search(combined) else None)
    aid = _extract(combined, r'"aid":\s*(?:Number\()?(\d+)')

    if not bvid:
        raise Ytb2biliError(
            "upload_no_bvid",
            "biliup 未返回 bvid，可能投稿失败或接口变动。请检查 B 站登录态（ytb2bili whoami）与网络。",
            output=(combined.strip() or "(biliup 无任何输出)")[-1500:],
        )

    return {
        "bvid": bvid,
        "aid": int(aid) if aid else None,
        "tid": tid,
        "tid_name": tid_info.get("name"),
        "title": title,
        "copyright": cr,
        "source": source or None,
        "url": f"https://www.bilibili.com/video/{bvid}",
    }


def _extract(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _clean_biliup_error(text: str | None) -> str:
    if not text:
        return "biliup 投稿失败（无输出）"
    for line in reversed(text.splitlines()):
        if any(k in line for k in ("ERROR", "error", "失败", "Err", "panic")):
            return line.strip()[:400]
    return text.strip().splitlines()[-1][:400]
