# ytb2bili

命令行把 **YouTube 视频搬运到哔哩哔哩**：下载 → 扫码登录 → 投稿，一条龙。

- **跨平台**：Windows / macOS / Linux（纯 Python + 自动下载 biliup 二进制）。
- **确定性执行**：整个自动化过程是纯代码，无 LLM/agent 参与。
- **AI/脚本友好**：每个子命令都支持 `--json`，输出结构化结果 + 稳定错误码 + 退出码。
- **不会投错分区**：投稿前对照 B 站实时分区表校验 `tid`。

底层依赖：[yt-dlp](https://github.com/yt-dlp/yt-dlp)（下载）+ [biliup-rs](https://github.com/biliup/biliup-rs)（投稿）。**依赖尽量自动化**，详见下方「依赖说明」。

## 安装

需要 **Python ≥ 3.9**。

```bash
# 方式一：从 GitHub 直接安装（推荐）
pip install "git+https://github.com/jianguo66666/ytb2bili.git"

# 方式二：下载 wheel 后本地安装
pip install ytb2bili-0.1.0-py3-none-any.whl

# 方式三：源码开发安装
git clone https://github.com/jianguo66666/ytb2bili.git && cd ytb2bili
pip install -e .

# 安装后，一键补齐其余依赖（biliup 二进制、deno 运行时）
ytb2bili doctor --install
```

> 想要全局命令、和其它 Python 环境隔离，用 [pipx](https://pipx.pypa.io)：
> `pipx install "git+https://github.com/jianguo66666/ytb2bili.git"`

## 依赖说明（各依赖怎么装的）

不同依赖用不同的成熟做法，绝大部分**你不用管**：

| 依赖 | 作用 | 怎么装 |
|------|------|--------|
| yt-dlp、qrcode、pillow | 下载 / 生成二维码 | **pip 自动**（声明在 `pyproject.toml`，`pip install` 时一起装） |
| ffmpeg | 合并音视频 | **pip 自动**（用 `imageio-ffmpeg` 自带的静态二进制，无需系统级安装） |
| biliup | B 站投稿 | **`doctor --install` 自动**（按你的系统/架构从 GitHub 下载对应二进制到数据目录） |
| deno（JS 运行时） | YouTube 解 JS 挑战 | **`doctor --install` 自动**（自动下载 deno 单文件二进制；若系统已有 deno/node 则复用） |

也就是说标准流程只有两步：`pip install ...` 然后 `ytb2bili doctor --install`。

### 万一自动装失败的手动兜底

`deno` 若因网络等原因没自动装上，可用系统包管理器装（装 `node` 亦可）：

```bash
# macOS
brew install deno
# Windows
winget install DenoLand.Deno
# Linux / WSL
curl -fsSL https://deno.land/install.sh | sh
```

`ffmpeg` 如想用系统版而非自带版：`brew install ffmpeg` / `winget install Gyan.FFmpeg` / `sudo apt install ffmpeg`。

## 快速上手

```bash
# 1. 扫码登录（终端显示二维码，并自动打开图片；手机 B 站 App 扫码确认）
ytb2bili login

# 2.（macOS 推荐）导出 YouTube cookie 到文件，避免每次下载弹钥匙串
ytb2bili export-cookies --browser chrome

# 3. 一条命令：下载并搬运（转载，自动带原标题/原链接/封面）
ytb2bili transfer "https://www.youtube.com/watch?v=XXXX" --tid 17

# 4. 查询稿件状态
ytb2bili status BV1xxxxxxxxx
```

分步用法：

```bash
ytb2bili download "https://youtu.be/XXXX" -q 1080 --cookies-from-browser chrome
ytb2bili upload video.mp4 --title "标题" --tid 17 --copyright 2 \
    --source "原链接" --tag "RTS,即时战略" --cover cover.jpg
```

## AI 调用（JSON 模式）

所有命令加 `--json`，stdout 输出单个 JSON 对象；成功 `ok:true`，失败 `ok:false` 且带 `error.code`。退出码 0=成功 / 1=业务错误 / 2=用法错误。

```bash
ytb2bili --json whoami
# {"ok":true,"command":"whoami","mid":234315816,"uname":"坚果zock","level":5,"vip":false}

ytb2bili --json tid-list 游戏
# {"ok":true,"command":"tid-list","partitions":[{"tid":17,"name":"单机游戏",...}]}

ytb2bili --json transfer "https://youtu.be/XXXX" --tid 17
# {"ok":true,"command":"transfer","download":{...},"upload":{"bvid":"BV...","url":...}}
```

`login --json` 特殊：流式 NDJSON——先输出一行 `{"event":"qrcode","qr_png":...,"qr_url":...}`，
用户扫码期间输出 `{"event":"status","status":"scanned"}`，最后输出登录结果。

## 错误码与自愈

失败时 `error.code` 是稳定标识，`error.message` 是中文说明，`error.hint` 是可执行建议；
人读模式下同样会打印「✗ [code] 说明 → 建议」。工具会先自愈（重试 / 换 player_client /
投稿后查 API 确认），救不了才报下面的错误码：

| error.code | 含义 | 该怎么办（hint） |
|------------|------|------------------|
| `youtube_bot_check` | 出口 IP 被 YouTube 判定为机器人/限流 | 加 `--proxy` 换出口 IP，或稍后重试 |
| `youtube_network_timeout` | 连不上 YouTube（无网络/没走代理） | 检查网络，加 `--proxy` 或设 `HTTP_PROXY` |
| `youtube_format_unavailable` | 拿不到视频格式（JS 挑战未解） | `doctor --install` 装 deno；工具会自动换 client 重试 |
| `youtube_age_restricted` | 年龄限制视频 | `export-cookies` 用已登录账号 cookie |
| `youtube_unavailable` | 私有/已删除/地区限制 | 确认链接；地区限制可 `--proxy` 换区 |
| `invalid_tid` | 分区号无效（B 站改版） | 用 `tid-list` 选有效分区（错误里附可选项） |
| `missing_source` | 转载稿件没给来源链接 | 补 `--source` |
| `not_logged_in` / `cookie_invalid` | 未登录 / B 站 cookie 失效 | 重新 `ytb2bili login` |
| `upload_no_bvid` | 投稿后既无输出也查不到同名稿件 | 用 `whoami` / `status` 核对后重试 |

退出码：`0` 成功 / `1` 业务失败 / `2` 用法错误——方便脚本判断。

跑前可用 `ytb2bili preflight [url]` 一次性自检：依赖、B 站登录、YouTube 可达性、是否被限流。

## 命令一览

| 命令 | 说明 |
|------|------|
| `doctor [--install]` | 检查/安装依赖 |
| `login` | 扫码登录，写 cookies |
| `whoami` | 当前登录账号 |
| `export-cookies [--browser chrome]` | 导出 YouTube cookie 到文件（一次授权，之后不再弹钥匙串） |
| `download <url>` | 下载 YouTube 视频（mp4 + 封面 + info.json） |
| `upload <file>` | 投稿本地视频 |
| `transfer <url>` | 下载 + 投稿（转载）一步到位 |
| `status <bvid>` | 查询稿件审核/发布状态 |
| `tid-list [关键词]` | 列出有效分区及 tid |
| `config` | 查看/修改默认配置 |

## 数据与安全

- **cookies.json**（含登录凭据）存在系统数据目录，**不会**放进 iCloud/网盘：
  - macOS：`~/Library/Application Support/ytb2bili/`
  - Linux：`~/.local/share/ytb2bili/`
  - Windows：`%LOCALAPPDATA%\ytb2bili\`
- biliup 二进制下载到同目录的 `bin/`。
- 配置文件：`config.json`（同上目录，Linux 在 `~/.config/ytb2bili/`）。

## 合规提醒

搬运他人视频请确保已获授权或内容为 CC 协议。工具默认以「转载」提交并注明原作者与原链接。

## 常见问题

- **YouTube 报「Sign in to confirm you're not a bot」**：先 `ytb2bili export-cookies --browser chrome`（用已登录浏览器 cookie，导出后不再弹钥匙串）。
- **macOS 每次下载弹钥匙串要密码**：这是 `--cookies-from-browser` 读 Chrome 加密 cookie 所致。改用 `ytb2bili export-cookies` 导出成文件即可根治。
- **下载报「Requested format is not available / Only images available」**：缺 JS 运行时或 EJS 求解脚本。装 `deno`（`brew install deno`）即可；工具已默认带 `--remote-components ejs:github`。
- **yt-dlp 抽取失败**：多为版本过旧，`pip install -U yt-dlp` 升级。
- **分区 tid 无效**：B 站分区会改版，用 `ytb2bili tid-list` 查当前有效分区。
