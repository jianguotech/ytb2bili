"""yt-dlp 封装：下载 YouTube 视频并输出结构化信息。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import deps
from .config import Config
from .result import Ytb2biliError

# YouTube 需要解 JS "n challenge" 才能拿到视频格式；让 yt-dlp 下载官方 EJS 求解脚本
# （首次下载后缓存）。需要本机有 JS 运行时（deno 或 node）。
EJS_FLAGS = ["--remote-components", "ejs:github"]


def _ffmpeg_flags() -> list[str]:
    loc = deps.ffmpeg_location()
    return ["--ffmpeg-location", loc] if loc else []


def _youtube_cookie_args(cfg: Config, browser: str | None) -> list[str]:
    """决定给 yt-dlp 的 cookie 参数：优先用 cookie 文件（不弹钥匙串），否则读浏览器。"""
    yc = getattr(cfg, "youtube_cookies", "") or ""
    if yc and Path(yc).expanduser().exists():
        return ["--cookies", str(Path(yc).expanduser())]
    b = browser if browser is not None else cfg.cookies_from_browser
    if b:
        return ["--cookies-from-browser", b]
    return []


def export_cookies(cfg: Config, browser: str, out_file: str | None = None) -> dict:
    """从浏览器导出 YouTube cookie 到 Netscape 文件，供后续 --cookies 复用。

    只需触发一次钥匙串授权，之后下载不再读浏览器、不再弹窗。
    """
    deps.ensure_ytdlp()
    from .config import DATA_DIR
    dest = Path(out_file).expanduser() if out_file else (DATA_DIR / "youtube_cookies.txt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = deps.ytdlp_cmd() + [
        "--cookies-from-browser", browser,
        "--cookies", str(dest),
        "--skip-download", "--no-warnings",
        "--playlist-items", "0",
        "https://www.youtube.com/",
    ]
    # yt-dlp 会在退出时把 cookie jar 写入 --cookies 文件；即便目标页无可提取内容，
    # 只要成功读到浏览器 cookie 就会落盘，因此不强制要求退出码为 0。
    subprocess.run(cmd, text=True, capture_output=True, env=deps.subprocess_env())
    if not dest.exists() or dest.stat().st_size < 100:
        # 回退：用一个真实视频页重试一次
        cmd2 = deps.ytdlp_cmd() + [
            "--cookies-from-browser", browser, "--cookies", str(dest),
            "--skip-download", "--no-warnings",
            "https://www.youtube.com/watch?v=2WJ_4pxB8jc",
        ]
        try:
            subprocess.run(cmd2, text=True, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            raise Ytb2biliError(
                "export_cookies_failed", _clean_ytdlp_error(e.stderr or e.stdout),
                browser=browser,
            )
    if not dest.exists():
        raise Ytb2biliError("export_cookies_failed", f"未能生成 cookie 文件: {dest}")
    n = sum(1 for _ in dest.open(encoding="utf-8", errors="ignore"))
    return {"youtube_cookies": str(dest), "lines": n, "browser": browser}


# 依次尝试的 YouTube player_client；某些客户端更容易绕过 bot 检测/拿到格式
PLAYER_CLIENTS = ["default", "tv", "web_safari,web", "mweb", "ios"]

# 网络重试相关 flag，提升弱网/抖动下的成功率
RETRY_FLAGS = [
    "--retries", "10", "--fragment-retries", "10",
    "--extractor-retries", "3", "--socket-timeout", "30",
]


def _proxy_args(cfg: Config, proxy: str | None) -> list[str]:
    p = proxy if proxy is not None else getattr(cfg, "proxy", "") or ""
    return ["--proxy", p] if p else []


def probe(url: str, cfg: Config | None = None, cookies_from_browser: str = "",
          proxy: str | None = None) -> dict:
    """只取元数据不下载。返回 title/duration/uploader/分辨率/license 等。"""
    cookie_args = (_youtube_cookie_args(cfg, cookies_from_browser) if cfg
                   else (["--cookies-from-browser", cookies_from_browser]
                         if cookies_from_browser else []))
    proxy_args = _proxy_args(cfg, proxy) if cfg else (["--proxy", proxy] if proxy else [])
    last_err = None
    for pc in PLAYER_CLIENTS:
        cmd = (deps.ytdlp_cmd() + ["--no-warnings", "--dump-single-json", "--no-playlist"]
               + EJS_FLAGS + RETRY_FLAGS + ["--extractor-args", f"youtube:player_client={pc}"]
               + cookie_args + proxy_args + [url])
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE,
                                          env=deps.subprocess_env())
            return _summarize(json.loads(out))
        except subprocess.CalledProcessError as e:
            code, _, _ = _classify_ytdlp_error(e.stderr)
            last_err = e.stderr
            if code not in ("youtube_bot_check", "youtube_format_unavailable"):
                break  # 换 client 也没用的错误，直接停
    _raise_ytdlp(last_err, url)


def download(
    url: str,
    cfg: Config,
    output_dir: str | None = None,
    quality: int | None = None,
    cookies_from_browser: str | None = None,
    proxy: str | None = None,
    progress: bool = True,
) -> dict:
    """下载最高不超过 quality 的画质，合并为 mp4，并抓取封面 + info.json。

    自愈策略：依次尝试多个 player_client；遇到 bot 检测/格式不可用时自动换 client 重试。
    返回 {video, cover, info_json, title, duration, width, height, source_url, id}。
    """
    deps.ensure_ytdlp()
    outdir = Path(output_dir or cfg.download_dir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    q = quality or cfg.quality
    cookie_args = _youtube_cookie_args(cfg, cookies_from_browser)
    proxy_args = _proxy_args(cfg, proxy)

    fmt = (
        f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
    )
    outtmpl = str(outdir / "%(id)s.%(ext)s")
    base = deps.ytdlp_cmd() + [
        "--no-playlist",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--write-info-json",
        "--embed-metadata",
        "-o", outtmpl,
        "--print", "after_move:%(id)s",
    ] + EJS_FLAGS + RETRY_FLAGS + _ffmpeg_flags() + cookie_args + proxy_args
    if not progress:
        base.append("--no-progress")

    last_err = None
    proc = None
    for pc in PLAYER_CLIENTS:
        cmd = base + ["--extractor-args", f"youtube:player_client={pc}", url]
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, check=True,
                                  env=deps.subprocess_env())
            break
        except subprocess.CalledProcessError as e:
            code, _, _ = _classify_ytdlp_error(e.stderr or e.stdout)
            last_err = e.stderr or e.stdout
            if code not in ("youtube_bot_check", "youtube_format_unavailable"):
                break  # 换 client 无益的错误（如私有/不存在/网络），直接停
    if proc is None:
        _raise_ytdlp(last_err, url)

    vid = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else None
    if not vid:
        raise Ytb2biliError("download_failed", "未能确定下载文件 id", url=url,
                            stderr=(proc.stderr or "")[-800:])

    video = outdir / f"{vid}.mp4"
    cover = _first_existing([outdir / f"{vid}.jpg", outdir / f"{vid}.webp", outdir / f"{vid}.png"])
    info_json = outdir / f"{vid}.info.json"
    meta = {}
    if info_json.exists():
        try:
            meta = _summarize(json.loads(info_json.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    if not video.exists():
        raise Ytb2biliError("download_failed", f"下载完成但找不到 {video}", url=url)

    return {
        "id": vid,
        "video": str(video),
        "cover": str(cover) if cover else None,
        "info_json": str(info_json) if info_json.exists() else None,
        "source_url": meta.get("source_url") or url,
        "title": meta.get("title"),
        "duration": meta.get("duration"),
        "duration_string": meta.get("duration_string"),
        "uploader": meta.get("uploader"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "size_bytes": video.stat().st_size,
        "output_dir": str(outdir),
    }


def _summarize(info: dict) -> dict:
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "width": info.get("width"),
        "height": info.get("height"),
        "license": info.get("license"),
        "source_url": info.get("webpage_url"),
        "description": (info.get("description") or "")[:500],
        "thumbnail": info.get("thumbnail"),
    }


def _first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _classify_ytdlp_error(stderr: str | None) -> tuple[str, str, str]:
    """把 yt-dlp 报错归类成稳定错误码 + 中文说明 + 可执行建议(hint)。"""
    s = (stderr or "").lower()
    if "sign in to confirm you" in s and "bot" in s:
        return ("youtube_bot_check",
                "YouTube 判定为机器人访问（该出口 IP 被临时限流）",
                "换一个出口 IP：加 --proxy http://host:port 走代理，或稍后重试；也可更新 cookie。")
    if "confirm your age" in s or "age-restricted" in s or "inappropriate" in s:
        return ("youtube_age_restricted", "视频有年龄限制，需登录 cookie 才能下载",
                "用 export-cookies 导出已登录账号的 cookie 再试。")
    if "private video" in s or "video unavailable" in s or "removed" in s or "not available" in s and "format" not in s:
        return ("youtube_unavailable", "视频不可用（私有/已删除/地区限制）",
                "确认链接有效；地区限制可尝试 --proxy 换区。")
    if "requested format is not available" in s or "only images are available" in s:
        return ("youtube_format_unavailable", "拿不到视频格式（多为 JS 挑战未解或需换 client）",
                "确保有 JS 运行时（doctor --install 装 deno）；工具会自动换 player_client 重试。")
    if "timed out" in s or "timeout" in s or "connection" in s or "unable to download" in s and "api" in s:
        return ("youtube_network_timeout", "连接 YouTube 超时（网络不通或未走代理）",
                "检查网络/代理：加 --proxy，或确认 HTTP_PROXY 已设。")
    if "no such file" in s or "unable to open" in s:
        return ("local_io_error", "本地文件读写错误", "检查输出目录权限与磁盘空间。")
    return ("download_failed", _clean_ytdlp_error(stderr), "查看 stderr 细节。")


def _raise_ytdlp(stderr: str | None, url: str):
    code, msg, hint = _classify_ytdlp_error(stderr)
    raise Ytb2biliError(code, msg, url=url, hint=hint,
                        stderr=(stderr or "").strip()[-600:])


def _clean_ytdlp_error(stderr: str | None) -> str:
    if not stderr:
        return "yt-dlp 下载失败（无错误输出）"
    lines = [l for l in stderr.splitlines() if l.strip().startswith("ERROR")]
    if lines:
        return lines[-1].strip()
    return stderr.strip().splitlines()[-1][:400]
