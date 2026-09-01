# macOS 版打包指南（GitHub Actions）

> 本项目桌面端（station/desktop，Electron）本机只能打 Windows 包；
> 这里用 GitHub Actions 的 macOS runner 在云端交叉打出 mac 版（arm64 / x64），无需 Mac 电脑。

## 一、本次改动清单

| 文件 | 改动 |
| --- | --- |
| `.github/workflows/build-mac.yml` | **新增**。macOS 打包流水线（手动选架构触发 / 打 `v*` tag 自动构建 arm64 + 发布 Release） |
| `station/desktop/package.json` | 新增 `mac` 打包段（zip + dmg，icon.png，免签名 `identity: null`）、`dist:mac*` 脚本、devDeps 增加 `ffmpeg-static`/`ffprobe-static`（mac 静态 ffmpeg 来源） |
| `station/desktop/scripts/prepare-python-runtime.js` | 平台化：新增 **darwin 分支**（裁剪出 `vendor/python/bin/python3` + `lib` 结构），Windows 分支保持原逻辑 |
| `station/desktop/lib/python-runtime.js` | 打包态 python 路径按平台解析（win: `py/python.exe`；mac: `py/bin/python3`） |
| `station/desktop/lib/paths.js` | mac 打包态窗口图标改用 `icon.png`（`.ico` 在 mac 无效） |
| `station/desktop/lib/local-server.js` | ASR 模型探测增加 macOS 路径（`~/Downloads`、`~/Download`、`/opt` 下的 `A-models/sherpa-onnx`） |
| `station/server/pipeline.py` | ffmpeg/ffprobe 默认路径按平台取后缀（win 带 `.exe`，mac 不带） |
| `station/server/yuanbao_client.py` | Edge 候选路径增加 mac 版（`/Applications/Microsoft Edge.app/...`）与提示文案 |
| `.gitignore` + `station/vendor/watermarks/*.ini` | 12 个水印 `.ini` 入库（CI 没有本机 vendor，必须随仓库走） |
| `.gitignore` + `station/vendor/asr_models/**` | 离线 ASR 模型入库（78 MiB；`model.int8.onnx` 单文件 81.8 MB，低于 GitHub 100 MB 硬限制） |
| `station/desktop/scripts/prepare-asr-models.js` | **新增**。把本机 `F:\Download\A-models\sherpa-onnx\sherpa-onnx-paraformer-zh-small-2024-03-09` 复制到 `station/vendor/asr_models/`（剔除 `test_wavs`） |
| `station/desktop/scripts/before-pack.js` | 按平台剔除 ASR 资源：仅 darwin 保留，**Windows 包不受影响**（win 侧未装 `sherpa_onnx`，带上只是白增 78 MiB） |
| `station/desktop/scripts/asr-smoke.wav` | **新增**。CI 冒烟用测试音频（179 KB，取自模型自带 `test_wavs/0.wav`） |
| `station/desktop/package-lock.json` | 同步新 devDeps（供 CI `npm ci` 使用） |

## 二、首次使用前：配置 Secret（一次性）

打包要求 `MIMO_API_KEY`（TTS 运行时密钥），不能提交进仓库，CI 从 Secret 读取：

1. 打开 GitHub 仓库 **Settings → Secrets and variables → Actions → New repository secret**
2. **Name** 填 `MIMO_API_KEY`
3. **Value** 填你本机 `station/server/.env` 里 `MIMO_API_KEY=` 后面的值（与 Windows 包用的是同一个）
4. 保存。没配的话工作流第一步就会红并提示。

## 三、触发打包

**方式 A：手动选架构（推荐，最灵活）**
仓库 **Actions** 页 → 左侧 **Build macOS App** → **Run workflow**：
- `arch`：
  - `arm64` —— Apple Silicon（M1 / M2 / M3 / M4），**现在绝大多数 Mac 都是这个**
  - `x64` —— 老的 Intel Mac
- 跑完后到本次运行页面底部 **Artifacts** 下载 `视频去重工位-mac-<架构>.zip`（内含 dmg 和 zip）

**方式 B：打 tag 自动发布 Release**
```powershell
git tag v0.1.0-mac
git push origin v0.1.0-mac
```
推送 `v*` tag 后自动构建 arm64 并创建 GitHub Release（mac 用户在 Releases 页面直接下载）。当前仓库是私有仓库，Actions 会消耗免费分钟额度（macOS runner 按 10 倍计费，一次构建约消耗 100–150 分钟额度，注意控制次数）。

## 四、mac 用户拿到包之后

包是**未签名**的（没有 Apple 开发者证书），首次打开会被 Gatekeeper 拦：

- **推荐**：右键（或按住 Ctrl 点）`视频去重工位.app` → **打开** → 再点「打开」
- 或终端执行：`xattr -cr "/Applications/视频去重工位.app"`（需先拷到 /Applications）

### 功能可用性

| 功能 | macOS 版 | 说明 |
| --- | --- | --- |
| 视频去重 / 画面调整 / 探测 | ✅ | ffmpeg/ffprobe 已内置（静态二进制，arm64/x64 各架构匹配） |
| 本地后端自启动 | ✅ | 包内自带 macOS 版 Python 3.13 精简运行时 + Playwright |
| ASR 提取文案 | ✅ 开箱即用 | 随包内置 **paraformer-zh-small 中文模型**（78 MiB）+ `sherpa_onnx` / `onnxruntime` / `soundfile`。首次转写需加载模型约 2 秒，之后常驻内存。语言覆盖**仅中文**；多语种 SenseVoice 模型 1.1 GB 未随包 |
| 元宝改写 | ⚠️ 需装 Edge | 驱动的是 **Microsoft Edge**。mac 用户需自己安装 Edge（`/Applications/Microsoft Edge.app`），首次用调试窗口扫码登录一次 |
| 素材/产物目录 | ✅ | 自动落到 mac 的「影片」目录下（`~/Movies/视频去重素材`、`~/Movies/视频去重产物`） |

## 五、常见问题排查

- **工作流挂在「裁剪 Python 运行时」**：说明 setup-python 的 3.13 结构非预期（如 stdlib 目录名不同），把报错贴出来，调整 `prepare-python-runtime.js` 的目录匹配即可。
- **dmg 打不出来 / hdiutil 报错**：可临时只留 zip（把 `package.json` 里 `mac.target` 的 dmg 项删掉）。zip 解压一样能跑。
- **artifact 里没有 x64 包**：x64 走 `macos-15-intel` runner，若该标签在你的 GitHub 套餐不可用，改 workflow 里 `runs-on` 的 `'macos-15-intel'` 为其他可用 Intel 标签（如 `macos-26-intel`）。
- **想验证产物**：用任何一台 mac（或借同学的），解压 zip → 把 `.app` 拖进「应用程序」→ 右键打开。核心自检：打开后左上角日志应出现 `[mcp] MCP 就绪: http://127.0.0.1:8765`；ASR 自检是对一段视频做「提取文案」，能出中文字即正常。
- **构建挂在「ASR 实跑冒烟」**：这一步是故意加严的，失败意味着**包还没打出来**，不会给用户坏包。两类典型原因：
  - `libonnxruntime.dylib` 在裁剪后的运行时目录里加载不到 → 需在 `prepare-python-runtime.js` 里对 `site-packages` 下的 `.so` 补 `install_name_tool` 改写；
  - numpy 2.x 与 `sherpa-onnx` 不兼容 → 在 workflow 的 `pip install` 行里 pin `numpy<2`。
- **Windows 包体积莫名大了 78 MiB**：`before-pack.js` 的 `pruneAsrModels()` 没生效。打包日志里搜 `drop ASR models on win32`，没有则检查 `extraResources` 中 `to` 值是否为 `station/vendor/asr_models`。
- **想换 ASR 模型**：本机执行 `cd station/desktop && node scripts/prepare-asr-models.js`（可用 `VU_ASR_SOURCE` 换源目录、`VU_ASR_MODEL` 换模型名），再提交 `station/vendor/asr_models/**` 即可。注意单文件别超过 GitHub 的 100 MB 硬限制，否则要改走 Git LFS。
