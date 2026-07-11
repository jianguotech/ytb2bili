"""依赖探测与自动安装。

- yt-dlp：作为 Python 依赖随包安装，通过 ``python -m yt_dlp`` 调用（跨平台、
  ``--cookies-from-browser`` 可用）。缺失时尝试 pip 安装。
- biliup：Rust 二进制，非 pip 包。按当前 OS/架构从 GitHub Release 自动下载并
  解压到用户数据目录，赋可执行权限。

全部确定性执行，不需要任何交互。
"""
from __future__ import annotations

import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .config import BIN_DIR, ensure_dirs
from .result import Ytb2biliError

BILIUP_VERSION = "v0.2.4"
BILIUP_RELEASE_BASE = (
    f"https://github.com/biliup/biliup-rs/releases/download/{BILIUP_VERSION}"
)

# (system, machine) -> release asset 文件名
_BILIUP_ASSETS = {
    ("darwin", "arm64"): f"biliupR-{BILIUP_VERSION}-aarch64-macos.tar.xz",
    ("darwin", "aarch64"): f"biliupR-{BILIUP_VERSION}-aarch64-macos.tar.xz",
    ("darwin", "x86_64"): f"biliupR-{BILIUP_VERSION}-x86_64-macos.tar.xz",
    ("linux", "x86_64"): f"biliupR-{BILIUP_VERSION}-x86_64-linux.tar.xz",
    ("linux", "amd64"): f"biliupR-{BILIUP_VERSION}-x86_64-linux.tar.xz",
    ("linux", "aarch64"): f"biliupR-{BILIUP_VERSION}-aarch64-linux.tar.xz",
    ("linux", "arm64"): f"biliupR-{BILIUP_VERSION}-aarch64-linux.tar.xz",
    ("linux", "armv7l"): f"biliupR-{BILIUP_VERSION}-arm-linux.tar.xz",
    ("windows", "amd64"): f"biliupR-{BILIUP_VERSION}-x86_64-windows.zip",
    ("windows", "x86_64"): f"biliupR-{BILIUP_VERSION}-x86_64-windows.zip",
}


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def biliup_path() -> Path:
    name = "biliup.exe" if _is_windows() else "biliup"
    return BIN_DIR / name


# ---------------------------------------------------------------- yt-dlp ----

def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return shutil.which("yt-dlp") is not None


def ytdlp_version() -> str | None:
    try:
        import yt_dlp
        return getattr(yt_dlp.version, "__version__", None) or yt_dlp.version.__version__
    except Exception:
        exe = shutil.which("yt-dlp")
        if not exe:
            return None
        try:
            return subprocess.check_output([exe, "--version"], text=True).strip()
        except Exception:
            return None


def ensure_ytdlp() -> str:
    """确保 yt-dlp 可用，返回版本号。缺失则用 pip 安装。"""
    if ytdlp_available():
        return ytdlp_version() or "unknown"
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            stdout=sys.stderr, stderr=sys.stderr,
        )
    except subprocess.CalledProcessError as e:
        raise Ytb2biliError(
            "ytdlp_install_failed",
            "yt-dlp 安装失败，请手动执行: pip install -U yt-dlp",
            detail=str(e),
        )
    if not ytdlp_available():
        raise Ytb2biliError("ytdlp_missing", "yt-dlp 安装后仍不可用")
    return ytdlp_version() or "unknown"


def ytdlp_cmd() -> list[str]:
    """返回调用 yt-dlp 的命令前缀（优先用当前解释器的模块）。"""
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        exe = shutil.which("yt-dlp")
        if exe:
            return [exe]
    raise Ytb2biliError("ytdlp_missing", "找不到 yt-dlp，请先运行: ytb2bili doctor --install")


# ---------------------------------------------------------------- biliup ----

def biliup_available() -> bool:
    return biliup_path().exists() or shutil.which("biliup") is not None


def biliup_exe() -> str:
    p = biliup_path()
    if p.exists():
        return str(p)
    found = shutil.which("biliup")
    if found:
        return found
    raise Ytb2biliError(
        "biliup_missing", "找不到 biliup，请先运行: ytb2bili doctor --install"
    )


def biliup_version() -> str | None:
    try:
        out = subprocess.check_output([biliup_exe(), "--version"], text=True).strip()
        return out
    except Exception:
        return None


def _asset_for_platform() -> str:
    key = (platform.system().lower(), platform.machine().lower())
    asset = _BILIUP_ASSETS.get(key)
    if not asset:
        raise Ytb2biliError(
            "unsupported_platform",
            f"暂无该平台的 biliup 预编译包: {key}. "
            f"请到 https://github.com/biliup/biliup-rs/releases 手动下载并放到 {BIN_DIR}",
        )
    return asset


def ensure_biliup() -> str:
    """确保 biliup 可用，返回可执行路径。缺失则自动下载。"""
    if biliup_available():
        return biliup_exe()
    ensure_dirs()
    asset = _asset_for_platform()
    url = f"{BILIUP_RELEASE_BASE}/{asset}"
    tmp = BIN_DIR / asset
    try:
        _download(url, tmp)
        _extract_biliup(tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    dest = biliup_path()
    if not dest.exists():
        raise Ytb2biliError("biliup_extract_failed", f"解压后未找到 biliup 于 {dest}")
    if not _is_windows():
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
        # macOS: 去掉隔离属性，避免 Gatekeeper 拦截
        if platform.system().lower() == "darwin":
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(dest)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(dest)


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ytb2bili"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        raise Ytb2biliError("download_failed", f"下载 biliup 失败: {url}", detail=str(e))


def _extract_biliup(archive: Path) -> None:
    dest = biliup_path()
    if archive.suffix == ".zip" or archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            member = _find_member(z.namelist())
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:  # tar.xz
        with tarfile.open(archive, "r:xz") as t:
            member = _find_member(t.getnames())
            src = t.extractfile(member)
            if src is None:
                raise Ytb2biliError("biliup_extract_failed", "压缩包内无 biliup 文件")
            with src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def _find_member(names: list[str]) -> str:
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if base in ("biliup", "biliup.exe"):
            return n
    raise Ytb2biliError("biliup_extract_failed", "压缩包内未找到 biliup 可执行文件")


def js_runtime() -> str | None:
    """返回可用的 JS 运行时名（deno/node/bun），用于 YouTube 解 JS 挑战。

    先看自带 deno（BIN_DIR），再看系统 PATH。
    """
    if deno_path().exists():
        return "deno(bundled)"
    for rt in ("deno", "node", "bun"):
        if shutil.which(rt):
            return rt
    return None


# ---------------------------------------------------------------- deno ----

# denoland/deno 各平台 release 资源名（single-binary zip）
_DENO_ASSETS = {
    ("darwin", "arm64"): "deno-aarch64-apple-darwin.zip",
    ("darwin", "aarch64"): "deno-aarch64-apple-darwin.zip",
    ("darwin", "x86_64"): "deno-x86_64-apple-darwin.zip",
    ("linux", "x86_64"): "deno-x86_64-unknown-linux-gnu.zip",
    ("linux", "amd64"): "deno-x86_64-unknown-linux-gnu.zip",
    ("linux", "aarch64"): "deno-aarch64-unknown-linux-gnu.zip",
    ("linux", "arm64"): "deno-aarch64-unknown-linux-gnu.zip",
    ("windows", "amd64"): "deno-x86_64-pc-windows-msvc.zip",
    ("windows", "x86_64"): "deno-x86_64-pc-windows-msvc.zip",
}


def deno_path() -> Path:
    return BIN_DIR / ("deno.exe" if _is_windows() else "deno")


def ensure_deno() -> str | None:
    """确保有可用 JS 运行时。系统已有 deno/node/bun 则直接用；否则自动下载 deno。

    返回运行时描述，若平台不支持自动下载则返回 None（需用户手动装）。
    """
    rt = js_runtime()
    if rt:
        return rt
    key = (platform.system().lower(), platform.machine().lower())
    asset = _DENO_ASSETS.get(key)
    if not asset:
        return None
    ensure_dirs()
    url = f"https://github.com/denoland/deno/releases/latest/download/{asset}"
    tmp = BIN_DIR / asset
    try:
        _download(url, tmp)
        with zipfile.ZipFile(tmp) as z:
            name = "deno.exe" if _is_windows() else "deno"
            member = next((n for n in z.namelist() if n.rsplit("/", 1)[-1] == name), None)
            if not member:
                raise Ytb2biliError("deno_extract_failed", "deno 压缩包内无可执行文件")
            with z.open(member) as src, open(deno_path(), "wb") as out:
                shutil.copyfileobj(src, out)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    dest = deno_path()
    if not _is_windows():
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
        if platform.system().lower() == "darwin":
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(dest)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "deno(bundled)"


# --------------------------------------------------------------- ffmpeg ----

def ffmpeg_location() -> str | None:
    """返回 pip 自带的 ffmpeg 二进制路径（imageio-ffmpeg），供 yt-dlp 使用。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def subprocess_env() -> dict:
    """给 yt-dlp 等子进程用的环境：把 BIN_DIR 加到 PATH 前面，便于找到自带的 deno。"""
    import os
    env = dict(os.environ)
    sep = ";" if _is_windows() else ":"
    env["PATH"] = str(BIN_DIR) + sep + env.get("PATH", "")
    return env


def subprocess_env_direct() -> dict:
    """给 biliup 等「访问国内站、必须直连」的子进程用：在 PATH 注入基础上剥离代理。

    B 站上传走 Clash/系统代理会失败（Windows 实测投稿输出为空），且没有必要——
    B 站是国内站，直连更快更稳。这里清掉所有代理环境变量。
    """
    env = subprocess_env()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                "https_proxy", "all_proxy"):
        env.pop(var, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def status() -> dict:
    """返回依赖状态字典。"""
    ff = ffmpeg_location()
    return {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.machine()}",
        "yt_dlp": {"installed": ytdlp_available(), "version": ytdlp_version()},
        "biliup": {
            "installed": biliup_available(),
            "version": biliup_version() if biliup_available() else None,
            "path": str(biliup_path()) if biliup_path().exists() else None,
        },
        "ffmpeg": ff,
        "js_runtime": js_runtime(),
        "bin_dir": str(BIN_DIR),
    }
