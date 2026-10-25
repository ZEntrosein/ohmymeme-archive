# OhMyMeme — AI Agent Guide

## 项目概述
轻量化跨平台表情包管理系统，突破表情包数量限制，支持全局快捷键呼出、搜索复制、FTP/S3/R2 同步、局域网互联。

## 架构
```
系统托盘 (pystray) ↔ 全局快捷键 (keyboard/pynput/轮询)
        ↓ show/hide
WebView 窗口 (pywebview) → Bottle HTTP 服务器 (localhost)
        ↓ JS API 桥 (pywebview.api.method)
JsApi / SettingsApi → SQLite (WAL) + 本地缓存 + 远端同步
```

## 技术栈
- **Python 3.12** + **pywebview** (frameless 窗口) + **Bottle** (静态文件/缩略图路由)
- **SQLite** (WAL, `threading.local()` 连接, `threading.Lock()` 写锁)
- **PIL/Pillow** (缩略图, 剪贴板图像)
- **pystray** (托盘, 惰性导入避免 headless CI 崩溃)
- **InnoSetup** (Windows 安装包) / **PyInstaller** (打包)
- **GitHub Actions** (lint+test on Ubuntu, build+installer on Windows/Linux/macOS)

## 核心原则
- **不得重构该项目** — 仅做最小必要修改，不改变现有架构、设计模式、代码组织
- **尽量不创建新文件** — 优先修改现有文件
- **增改同步** — 增加新功能或创建新文件后，同步修改 `README.md`、`CONTRIBUTING.md` 和 `AGENTS.md` 中对应描述（用户可见改动进 README，开发者环境/构建/CI 信息进 CONTRIBUTING，实现细节进 AGENTS）
- **关联文件同步** — 修改后检查是否需要同步更新 `.gitignore`、`Makefile`、`pyproject.toml`、`requirements.txt`、`environment.yml` 等关联文件
- 使用中文回答用户的问题

## 代码规范
- 无类型标注（`database.py`/`updater.py` 除外可使用 `typing` 基本类型）
- 无非必要注释（除非用户明确要求）
- 每段函数需要有简单功能注释
- 无 emoji（除非用户要求）
- 无文档字符串（只对公开 API 使用极简单行 docstring）
- 无冗余前缀/后缀说明（写完代码即结束，不加总结）

## 格式 & Lint
- `black src/` (line-length 88,  black 26.5.1)
- `ruff check src/` (select F, E, W, I)
- 新增依赖同时更新 `requirements.txt` 和 `environment.yml`
- **PR 贡献必须确保 `black --check src/` 和 `ruff check src/` 全部通过**，CI 会检查这两项

## 关键目录
```
src/              # 主代码
  main.py         # CLI 入口, OhMyMemeApp 编排
  webui.py        # pywebview 窗口 + JsApi/SettingsApi + Bottle 路由
  updater.py      # 版本检查 + 并发镜像下载
  database.py     # MemeDB (SQLite, 分组/类别/标签/来源映射)
  config.py       # Config (JSON + Fernet 加密密钥)
  sync.py         # 同步后端 (FTP/S3/R2/WebDAV)
  lan.py          # 局域网互联 (UDP 发现 + TCP 握手 + AES-GCM 会话)
  tray.py         # TrayManager (pystray, 惰性导入)
  hotkey.py       # GlobalHotkey (三级降级: keyboard→pynput→轮询)
  clipboard_util.py # 剪贴板操作 (Win32 ctypes / macOS osascript / Linux xclip)
  media_derivative.py # 原件生成可再生的日常预览/复制副本
  category_storage.py # 单归属类别名称校验与类别目录安全操作
  bulk_read.py      # 虚拟盘原件读取闸门（三级优先级 + 串行 + 分批冷却）
  gif_stego.py     # GIF 增量隐写（实验性：粘贴表情大小 + 无损还原原图）
  native_drag.py   # Windows 原生文件拖拽 (WinForms DoDragDrop + CF_HDROP, 惰性加载 pythonnet)
  crypto_util.py  # 加密 (Fernet + PBKDF2, 降级 XOR)
  manifest.py     # meme-index.json 构建/加载
  plugin_manager.py # 插件系统（发现/校验/加载/权限门控/超时熔断/注册表聚合，v0.7.0）
  platform_util.py # 平台工具 (WSL检测, 开机自启, 单实例互斥)
  adb_util.py      # ADB 自动检测/下载 + QQ 表情包缓存导入（ADB 拉取 + 魔数识别扩展名 + ZIP 打包）
  qqnt_extract.py  # QQNT 本地收藏表情提取（GPL-3.0 衍生模块，纯函数 + 回调接口，无 UI 依赖）
  onebot.py        # NapCat / OneBot HTTP+WebSocket、本机命令监听与聊天记录归档
  tg_stickers.py   # Telegram Desktop 缓存表情包提取（tdata 解密 + webm 转 webp + 入库）
  notion_import.py # Notion 数据库只读导入（Files & media + 标题/标签/分组映射）
  douyin.py        # 抖音表情包下载导入（ABogus 签名 + curl_cffi TLS 指纹 + WebP 原格式入库）
  abogus.py        # ABogus 签名算法（纯 Python，GPL-3.0，源自 TikTokDownloader）
  backup.py        # 本地备份（ZIP 导出/恢复，仅 PC 间整库迁移，仅允许恢复到空库）
  douyin_dl.py     # 抖音下载 CLI 测试入口（独立运行，不依赖 GUI）
  wechat_probe.py  # 微信收藏表情导入（helper 二进制提取密钥 + AES-CBC 解密 DB + CDN 下载，仅 Windows）
  wechat_keyfinder/ # 微信密钥提取 C++ 辅助二进制源码（CMake+MSVC 构建，无 OpenSSL 依赖；
                   # .rc 提供版本资源；helper 随安装包内置，不随 Release 单独分发）
  env_check.py     # 首次运行环境检测（WebView2/.NET 检测 + tkinter/MessageBox 非 webview UI + 标记文件）
  vue-src/       # Vue 3 前端源码（Vite 构建，产物到 webui/dist/ohmymeme.js）
    App.vue      # 根组件：标题栏/搜索/侧边栏/面包屑/体裁与标签栏/统一内容网格/分页
    components/ChatRecordDetails.vue # 聊天记录来源、节点预览与发送命令弹窗
    main.ts      # 入口：挂载 + window.focusSearch 全局（快捷键呼出聚焦搜索）
    style.css    # 主窗口样式（CSS 变量主题，蓝色 #3b82f6）
    types/       # TS 类型定义 (Meme/Collection/Tag)
    utils/       # api 桥接 + esc + renderMarkdown
    composables/ # useMemes 状态 / useDragSort 拖拽 / useContextMenu / useCollectionBuilder
    components/  # Pager/TagEditor/MemeDetails/ImportMenu/NotionImportDialog/ImportProgressOverlay/
                 # SyncOverlay/ContextMenu/CollectionBuilder/CollectionTreeNode/UpdateDialog/SimilarImportDialog/
                 # LargeMemeImportDialog/SetupGuide
  webui/          # 前端静态文件
    vue.html      # 主窗口入口（Vue），Bottle 优先加载
    dist/ohmymeme.js # Vite 构建产物（gitignored）
    settings.html  # 设置窗口 Vue 挂载壳（#settings-mount + dist/ohmymeme.js）
    index.html/index.css/index.js  # 旧主窗口（已备份至 webui-backup/，不再使用）
  vue-src/settings/ # 设置窗口 Vue 源码
    SettingsApp.vue # 入口：注入样式、执行 legacy、移动插件区块
    settings-body.html # 主体模板（旧 settings.html 的 body）
    settings.css      # 样式
    legacy.js         # 逻辑（旧 settings.js 迁移，经典脚本全局函数）
config/
  offsets.json    # wechat_keyfinder 易变参数（版本号等，微信升级时只改此文件）
docs/
  privacy.md      # 隐私策略（中英双语，列出全部主动发起的网络连接；Microsoft Store 上架「隐私策略 URL」指向此文件）
  wechat_keyfinder_protocol.md # helper 二进制协议说明
scripts/
  build.py        # PyInstaller + InnoSetup/MSIX 构建脚本 (i18n zh/en)
  installer/      # windows.iss (InnoSetup) / msix/AppxManifest.xml (MSIX)
  launcher.py     # PyInstaller 入口
  hooks/          # 自定义 PyInstaller hooks（Linux GTK: WebKit2/Soup typelib 收集，内置无对应 hook）
    hook-gi.repository.WebKit2.py
    hook-gi.repository.Soup.py
tests/
  test_core.py    # unittest 风格: Version/Config/Crypto/Database
  test_abogus.py  # unittest 风格: ABogus 签名算法 SM3/RC4/签名
  test_douyin_dl.py # unittest 风格: 抖音下载 CLI (签名URL/verifyFp)
  test_tg_stickers.py # unittest 风格: Telegram webm转换/取消/进度/dedup (mock Popen)
  test_updater.py   # unittest 风格: 非阻塞版本检查缓存机制 (mock check_latest)
  test_startup.py # pytest 风格: 全生命周期集成测试
  test_phash.py   # pytest 风格: 感知哈希(pHash)算法单元测试 (需 PIL)
  test_import_concurrency.py # pytest 风格: _do_import 并发去重 (同图1条/异图都可导)
  test_avoid_webp.py # pytest 风格: 复制时避免 WebP (WebP->GIF/JPG 转换/帧延时/残影/回退)
  test_cloud_direct.py # pytest 风格: 云端直接使用 (manifest diff/云过滤/分页合并/cloud_download/缩略图统一)
  test_plugin_manager.py # pytest 风格: 插件系统 (manifest校验/隔离/熔断/权限/注入/状态roundtrip，monkeypatch `_get_data_dir` 隔离)
  fixtures/grid_slot_probe.cjs # Node 网格拖拽槽位回归探针
  fixtures/tiny_vp9.webm   # TG 转换端到端夹具（VP9+alpha，build.py --verify-ffmpeg 用）
```

## js_api 桥接规范
- `JsApi` 暴露给主窗口，`SettingsApi` 暴露给设置窗口
- JS 调用: `pywebview.api.methodName(...args)` → 自动序列化
- JS 辅助函数: `async function api(method, ...args) { return await pywebview.api[method](...args); }`
- 返回类型: `str` / `int` / `bool` / `dict` / `list`，错误返回 `None` 或 `{"ok": false, "error": "..."}`
- 图片传输: 缩略图通过 `/api/thumb/{id}` HTTP 路径渲染，不通过 JS API JSON

## 关键实现细节

### 系统托盘
- `TrayManager` 在 daemon 线程运行
- **菜单中文化**：右键菜单默认中文（`_MENU_TEXT` 的「显示/隐藏」「退出」）；`_build_icon` 初始化构建失败或 `_run_icon` 运行中 `icon.run` 抛错（如部分 Linux 托盘后端对中文不兼容）时回退英文重建，仅回退一次，循环用 `_running` 守卫 stop 竞态（`tests/test_startup.py` 托盘 3 例）
- 惰性导入: `_pystray_ok()` 避免 headless CI (X11 `DisplayNameError`)
- WSL 自动跳过托盘
- macOS 跳过托盘：pystray 在 macOS 需在主线程抢占 NSApplication runloop，与 pywebview 主循环冲突（会导致窗口无法启动或段错误），与 Linux GTK 冲突同理

### 全局快捷键
- `_patch_windows_keyboard_message_loop` 在 Windows 首次 `keyboard.add_hotkey` 前幂等替换后端 `listen`，用实际 `MSG` + `byref` 传递缓冲区，GetMessage 返回正数才派发、0 正常退出、-1 抛 WinError 由现有守护处理。补丁只存在于本进程，不改 `.venv` 或依赖文件；后续补线程/重注册沿用同一安全循环，其他平台不应用。
- 三级降级: `keyboard` → `pynput` → 200ms 轮询 (`keyboard.is_pressed`)
- **运行期失效自愈**：`keyboard` 0.13.5 的 `GenericListener` 处理线程（`processing_thread`）一旦因未捕获异常崩溃即永久失效（进程存活、界面正常但热键无响应，需重启软件才恢复）。`GlobalHotkey` 两道防线：①`_try_keyboard` 用 `make_safe` 把回调包成 `_safe_callback`，吞掉回调异常，防止其杀死 `processing_thread`；②注册后启动 daemon 守护线程（`_start_keyboard_watchdog`，`KEYBOARD_WATCH_INTERVAL`=5s）周期检查 `keyboard._listener` 的 `listening_thread`/`processing_thread` 是否存活，任一死亡即 `_reregister_keyboard`（`remove_hotkey` → 置 `listener.listening=False` → `start_if_necessary()` → 重新 `add_hotkey`）自动重挂；③（Windows）**钩子心跳探针**：WH_KEYBOARD_LL 会被系统静默摘除（睡眠恢复/回调超时/显示切换），此时线程仍在泵消息、存活检查不可见——`_hook_health_check` 每 `KEYBOARD_PROBE_INTERVAL`(30s) 经 `SendInput`（失败回退 `keybd_event`）注入带 `dwExtraInfo=0x4F484D4D`（ASCII "OHMM"）标记的无害 F15 探针，并由 `keyboard.hook(..., suppress=True)` 注册的**探针阻塞钩子**在 pending 窗口内把探针吞掉——不吞会作为真实按键泄漏到前台窗口（gvim/SSH 终端等响应该键，issue #95），吞掉时在钩子内直接刷新 `_hook_last_seen` 完成探针确认（被吞事件不会到达非阻塞观察者），pending 窗口外的真实 F15 与其余按键一律放行；`KEYBOARD_PROBE_TIMEOUT`(15s) 内钩子未上报任何键盘事件（阻塞钩子/`keyboard.hook` 观察者刷新 `_hook_last_seen`，用户自身按键也算）即判定钩子失效，`_restart_keyboard_listener` 以 `PostThreadMessageW(WM_QUIT)` 结束旧监听线程（其钩子随线程退出被系统移除；线程拒不退出则中止重启避免双钩子导致热键触发两次）→ `start_if_necessary()` 重装钩子 → 重挂热键，约 45s 内自愈。**热键事件日志**：`_get_file_logger` 惰性创建独立 logger，把注册/回调异常/线程死亡/自愈重挂等事件追加到 `data_dir/hotkey.log`（`_log_hotkey_event` 同步写文件+控制台，初始化失败降级为常规 logger 不阻塞）。**pytest 下禁用文件日志**（`PYTEST_CURRENT_TEST` 环境变量守卫）——TestHotkeyWatchdog/test_startup 会触发注册/重注册日志，夹具错误（如 inject-fail）曾污染真实 hotkey.log 被误读为运行期自愈失败（真实自愈必先记录「监听/处理线程已退出」，日志中无此行即无真实失效）。**重注册与注销的正确性**：`_reregister_keyboard` 返回 bool 且与 `unregister` 持同一 `_reregister_lock` 串行化——`add_hotkey` 失败时置 `_reregister_pending=True` 并返回 False（不记录成功、守护下轮继续重试），成功才清 pending 并记成功事件；注销已开始（回调已清空或停止事件已置位）时重注册直接返回 False 不重新挂热键。**生命周期代次 token**：`_watchdog_gen` 仅在 `unregister` 递增（`register`/`_try_keyboard` 不递增——同一实例重复 register 时旧 watchdog 仍受 `unregister` 已递增的代次约束），`_start_keyboard_watchdog` 捕获当前代次并由 `_reregister_keyboard(listener, gen)`/`_restart_keyboard_listener(listener, gen)` 在锁内校验 `gen == _watchdog_gen`，旧 watchdog（旧代次）的重注册操作直接返回 False，杜绝注销后立即重新注册时旧线程对新热键的误操作。
- macOS 跳过 `keyboard` 库（darwin 后端需 root 权限，报 `Error 13` 且 root 下会段错误），直接走 `pynput`（CGEventTap，需辅助功能权限）
- WSL 无法捕获全局快捷键
- 配置 `hotkey_show_at_mouse` 默认 `false`；仅 Windows 生效。开启后仅在全局热键将隐藏主面板显示时，按鼠标所在显示器工作区依次尝试 `(cursor_x, cursor_y)`、`(right-width, cursor_y)`、`(cursor_x, bottom-height)`、`(right-width, bottom-height)`，仅使用首个完整容纳窗口的候选位置；出错或没有可用位置时不移动。托盘保持普通切换，热键回调仍为零参数。
- WebUI 维护非持久的快捷键显示会话状态：仅隐藏主窗口被全局快捷键显示后，成功复制或成功原生向外文件拖拽才会自动隐藏；任意 hide、普通/托盘显示、LAN/其他 show、内部排序拖拽及失败交互均不会触发该自动隐藏。配置 `disable_auto_hide` 默认 `false`，开启后 `schedule_hide()` 入口直接返回 False（复制与拖拽成功均不自动隐藏，会话状态保留），设置页「基础设置 → 全局快捷键」区块复选框 `s-disable-auto-hide`。

### 窗口
- 主题配置 `theme` 默认 `dark`，可在设置页「基础设置 → 窗口」切换 `light`；`get_init_data`、两套 `get_settings`/`reset_settings` 返回主题，保存经 `refreshMemes` 更新主窗口；`_theme_html` 在 HTML 首屏注入主题和底色，前端 `applyTheme` 同步 `data-theme`、底色与 `color-scheme`，CSS 变量覆盖主窗口和设置窗口配色。 主题保存与重置共用 settingsQueue；SettingsApi.save_settings 返回 `{ok,theme}`，成功才应用主题。主窗口 refreshMemes 接受已保存的界面设置快照，并用请求代次丢弃过期响应；快捷键与开机自启未改变时跳过重复设置。旧版 index.html 同样注入主题，index.css 提供浅色变量。
- 主窗口 ~960×640 frameless, 设置窗口 720×560 frameless（每次 `_create_settings_window` 以主窗口当前位置居中创建——`x = 主窗口x + (宽-720)//2`，主窗口坐标不可用时交由系统摆放）
- 设置窗口是独立 webview：`open_settings` 每次销毁重建，`focus_settings_window` 仅做 z-order 提升；`resizable=True`，关闭/重建时 `_save_settings_window_size()` 把尺寸写配置键 `settings_window_width`/`settings_window_height`（0=默认 720×560），下次创建恢复
- **窗口大小调整（配置 `window_resize_enabled`，默认关）**：开启后主窗与设置窗可拖 8 向边框调整——settings.html「窗口」区块 `#s-window-resize`，保存后主窗 `refreshMemes` 同步 `state.windowResizeEnabled` 即时调 `applyWindowResizeHandles()`；拖拽 layer `#win-resize-layer`（App.vue `syncWinResizeHandles()` 按开关增删）与设置窗 `vue-src/settings/legacy.js` 的 `applyWindowResizeHandles()` 均用 `screenX/screenY` 相对起点的**绝对增量**（rAF 节流），拖完一次调 resize API 持久化；`JsApi.resize_window(w,h)` 钳 480-8000/360-6000、`SettingsApi.resize_window(w,h,persist)` 钳 600-8000/480-6000 且 persist 时写 `settings_window_*` 配置；关闭时拖过的尺寸与配置尺寸一并持久化；`SettingsApi.reset_main_window_size()` 清空 `window_width`/`window_height` 并把主窗口恢复 960×640（设置页「恢复默认窗口大小」按钮）
- Windows 全局热键显示位置仅在隐藏到显示的转换时计算，使用鼠标所在显示器工作区；不改变托盘激活或其他窗口显示路径
- 自定义 JS 拖拽: 鼠标事件 → `pywebview.api.move_window(dx, dy)`
- 增量回退（Windows/macOS）用 `screenX/screenY`（**勿改 `clientX/clientY`** — clientX 是相对窗口坐标，窗口自身滞后位移会被下一次 mousemove 当作反向增量回传，形成反馈振荡导致高频抖动）；Linux 走合成器原生拖动不经过此路径
- **Linux 拖拽必须走合成器**：`w.move()` 在 Wayland 下无效（合成器不允许客户端自定位），mousedown 时 JS 调 `start_window_drag()` → 后端 `GLib.idle_add(native.begin_move_drag, ...)` 交给合成器交互式拖动；时间戳用 `Gdk.CURRENT_TIME`（GDK 文档允许未知时间时用它，X11 回填最近输入事件时间、Wayland 不参与）
- `#titlebar` 上可拖拽 (排除 `.title-btn` 按钮区域)
- 侧边栏折叠按钮 `.sidebar-toggle` 位于搜索框左侧（`#search-wrap` 内），点击折叠/展开 `#sidebar`；搜索框 `flex:1` 随侧边栏 180px↔48px 动态伸缩；**滑动手势**：折叠态在侧栏条（48px）内按住右滑（水平位移 ≥40px 且水平主导）展开，展开态在侧栏内左滑折叠（`onSidebarSwipeDown/Move/End` 观察 pointer 事件，触发后的误触 click 由 `onSwipeClickCapture` 在 document 捕获阶段一次性吞掉，下次 pointerdown 复位），`#sidebar` 带 `touch-action: pan-y` 支持触摸

### 设置页（Vue）

- `src/webui/settings.html` 仅为 Vue 挂载壳：`#settings-mount` + `#plugin-sections-source`（服务端注入锚点）+ `/dist/ohmymeme.js`；原 `settings.css`/`settings.js` 已删除
- `src/vue-src/main.ts` 按页面存在 `#settings-mount` 还是 `#app-mount` 挂载 `SettingsApp` 或 `App`，仍为单入口单 bundle（`inlineDynamicImports`）
- `SettingsApp.vue` 以 `?raw` 引入 `settings-body.html`/`legacy.js`、以 `?inline` 引入 `settings.css`：先注入样式，再把 body `v-html` 渲染，随后把 `legacy.js` 作为经典 `<script>` 插入（函数声明成为全局，兼容大量 inline `onclick`），最后移动插件区块并调用 `initSettings`/`initVersion`/`initDirtyTracking`/`initPlugins`
- `legacy.js` 是原设置页逻辑的迁移副本，移除了原 `DOMContentLoaded` 自启块（改由 `SettingsApp` 显式调用）；新增 `confirmManifestToggle`、`testSync` 的证书确认分支与 `resetMainWindowSize` 全局函数

### 插件系统 (plugin_manager.py)
- 全局单例 `get_plugin_manager()`：`main.py` 在 WebUI 创建前 `init(cfg)`、shutdown 时 `unload()`；插件目录 = 配置 `plugin_dirs`（条目自身含 `plugin.json` 即直连该目录，否则扫描其子目录）+ `data_dir/plugins/`；启停状态持久化 `data_dir/plugins_state.json`（`{"disabled":[...]}`，原子写），**插件设置按 id 分文件存 `config_dir/plugins/<id>/settings.json`**（`get_settings`/`save_settings` 内存缓存 + 原子写，pid 须匹配 `_ID_RE`；`config_dir` = 配置文件所在目录即 `%APPDATA%/OhMyMeme`，随用户配置；旧版 `plugins_state.json` 内嵌 `settings` 与旧位置 `data_dir/plugins/<id>/settings.json` 首次 `init` 自动迁移到该路径——`_migrate_settings_location` 新位置已存在则丢弃旧文件、迁空的目录一并移除），插件私有数据 `data_dir/plugins_data/<id>/`
- manifest：`id`（`^[a-z0-9][a-z0-9_-]{0,63}$`）/`name`/`version` 缺失不注册；`api_version` 必须 == `PLUGIN_API_VERSION`(1)；`repo` 可选但须 `https://|git@|ssh://` 开头；`sandbox:true` v0.7 跳过加载（v0.8 子进程）；`entry` 与资产路径必须位于插件目录内；`dependencies` 用 `find_spec` 检测（缺失即 failed，不自动安装）；`vendor/` 加载时追加 `sys.path` 尾部、禁用/卸载时移除
- 加载与熔断全程不抛：状态 `loaded / disabled / failed`（`reason` 展示在设置页）；超时 `LOAD_TIMEOUT` 15s（on_load/on_unload）/ `HANDLER_TIMEOUT` 10s（回调、路由、按钮、`on_settings_changed`）/ `_API_TIMEOUT` 120s（JS API），超时或回调异常 → `status=disabled` 熔断（下次重启恢复）；`_invoke` 放行 `loaded/loading`——on_load 执行期间 status=loading（否则回调内注册会误报 plugin_disabled），运行期其余状态返回 `plugin_disabled`
- 权限门控（manifest `permissions`，未声明的注册调用在 `on_load` 内抛 `PermissionError` → 该插件加载失败）：`route`/`main_api`/`settings_api`/`assets:serve`/`settings:section`/`buttons`/`startup`/`window`/`host:evaluate_js`；未知权限仅告警忽略；`on_settings_changed`/`on_unload` 从 entry 模块 `getattr` 取（可缺省）
- 资产注入：`asset_tags(window)` 按 manifest `windows` 过滤、按 id 序生成 `<link>` 与 `<script defer>`（版本 = `插件版本-md8(设置哈希)`，设置变化自动改 URL 防缓存）；webui 服务端注入——vue.html/settings.html 存在插件标签时 `_inject_before("</head>")`/`_inject_before("</body>")`，settings.html 另有 `<!-- plugin-sections -->` 锚点供 `_inject_after` 插区块，无插件时保持 `static_file` 快路径；插件 defer script 位于宿主脚本（ohmymeme.js）之后按文档序执行，故插件脚本能访问宿主全局与 `pywebview.api`
- 路由与服务：`/plugins/<id>/<path>` 静态服务（`resolve_asset` 仅查 loaded + 路径穿越/缺失 → 404，不查权限；**`data/<path>` 前缀解析到 `data_dir/plugins_data/<id>/`**——启动动画等经 `settings_api` 复制的媒体走此路径，strip 后空/穿越同样 404）；自定义路由 `custom_routes()` 返回 `[(pid, rule, fn)]` 且**必须注册在兜底 `/<filepath:path>` 之前**（Bottle 0.13 按注册顺序匹配动态规则），`call_route(pid, fn, params)` 处理器收单个 dict（query+表单），返回 str → `route_content_type` 按扩展名推 Content-Type、dict → JSON，停用插件路由返回 404；插件路由返回 404 与错误码统一由 `_make_plugin_route_cb` 处理
- 聚合下发：`get_init_data` 并入 `init_fields()` → `startup_video_src`/`startup_bg_color`（空即用默认 `/resources/OhMyMeme.mp4` 与 `#000000`）/`startup_media_duration_ms`（>0 时前端图片按时长收起，默认 0）/`startup_media_type`（`image` 走 `<img>` 分支只播一遍）/`plugin_buttons`/`plugin_button_constraints`；`WebUI._resolve_window_size()` 取值优先级 = 插件 `window_params` > 配置 `window_width`/`window_height`（0 或越界 480-8000/360-6000 回退）> 默认 960×640，`_save_window_position` 关闭窗口时把宽高随位置一并持久化
- 设置页：nav「插件」分组（`data-group="plugin"`）+ 宿主 `#plugin-list` 渲染**折叠栏**（`renderPluginItem` 标题=插件名，点击标题/头部 `pluginToggleCollapse(id)` 展开 `#plugin-body-<id>`（默认收起，`aria-expanded` + `.open` 箭头旋转），右侧 `.plugin-toggle` 开关启停且 `stopPropagation` 不触发折叠；键盘 Enter/Space 经 `pluginAccKey` 触发）；插件区块契约 = `section[data-group="plugin"][data-plugin=<id>]` 内输入带 `data-plugin`+`data-key`（`initPlugins` 把该插件全部 `.section[data-plugin]` 移入其折叠体后再用 `plugin_get_settings` 回填 checkbox/value，`pluginSaveSection(id)` 收集合并保存），**桥接未就绪时 `initPlugins` 经 `initPluginRetries`（20×200ms）重试**——否则 `api()` 返回 null 导致列表渲染「暂无插件」、折叠栏不生成；回填循环完成后派发 `window` CustomEvent **`omm-plugin-section-filled`**（`detail.plugin_id`），插件脚本据以同步下拉/颜色控件等非 `data-key` 控件；`initDirtyTracking` 跳过 `[data-plugin]` 子树；保存成功后后端 `evaluate_js` 推**主窗口** `window.ommPluginRefresh(pid)`（settings legacy 与 App.vue 各实现：刷新 `/plugins/` link 加 `?t=` + 派发 `omm-plugin-refresh` CustomEvent，插件脚本监听该事件重新应用）
- 前端按钮：插件按钮 `v-for state.pluginButtons`（`icon` 非空渲染 `.icon-btn` 含 `.plugin-btn-icon` img，否则 `title-btn`），点击 `JsApi.plugin_button_click`；核心按钮 `v-if=!coreBtnHidden(key)` + `:style order=coreBtnOrder(key)`，`CORE_BTN_ORDER` = sort10/select20/sync25/upload30/download40/import50/refresh60/settings70/close1000（约束 `order` 覆盖、`hide` 隐藏）
- 桥接：JsApi `plugin_call`/`plugin_button_click`/`resize_window`；SettingsApi `plugin_list`/`plugin_set_enabled`/`plugin_get_settings`/`plugin_save_settings`（成功推 `ommPluginRefresh`）/`plugin_open_dir`/`plugin_call`/`resize_window(w,h,persist)`/`plugin_pick_file(plugin_id, media=video|image|any)`（校验插件 loaded 后弹原生文件对话框，返回 `{ok,path}` 或 `{cancelled:true}`，供启动动画等媒体选择）
- 启停语义：禁用立即生效（`clear_registry` + vendor 移除 + 持久化），启用返回 `restart:true` 需重启加载；用户禁用的插件重启后不加载 entry（`status=disabled`）
- 配套仓库：`omm-plugin-template`（开发模板 + manifest/权限/`on_load(ctx)` API/生命周期/区块契约/超时依赖的完整文档）、`omm-personalized-plugin`（官方个性化插件 v1.1.0：启动动画选择视频/图片文件并复制进 `plugins_data` 按 `data/` 资产服务、RGBA+HEX 颜色控件与字体下拉的设置控件 `assets/settings.js`+`settings.css`、背景/字体立即生效、按钮布局约束；**窗口尺寸改由宿主 `window_resize_enabled` 开关接管，不再设 `win_width`/`win_height`**）；测试 `tests/test_plugin_manager.py`（33 例，`monkeypatch src.config._get_data_dir` 隔离真实数据目录）

### 数据库
- 10 表: `memes`, `tags`, `meme_tags`, `collections`, `meme_collections`, `categories`, `favorites`, `recent_uses`, `notion_sources`, `chat_records`
  - `tags`/`meme_tags`：`tags` 以 `(category, name)` 作为不区分大小写的复合唯一标识；旧库启动时重建旧 `name UNIQUE` 表并把已有标签迁入空分类（UI 显示「未分类」），原 tag id 与 `meme_tags` 关联保持不变。DB 同时保留字符串兼容接口，并提供 `get_all_tag_records`/`get_meme_tag_records` 结构化接口；`set_meme_tags`/`add_tags_to_memes`/`rename_tag`/`search` 接受 `{category,name}`，所以不同分类可存在同名标签且独立筛选/改名。主标签栏按分类分组；`MemeDetails` 与 `TagEditor` 保留分类并允许新标签指定分类，`MemeDetails` 的标签编辑入口还能全局修改现有标签的名称与种类，目标已存在时合并关联；`_set_tags` 覆盖式写入；`_prune_orphan_tags` 在 `_set_tags` 与 `delete_meme` 中清理无任何表情使用的孤儿标签
  - `memes.manual_description`/`manual_ocr_text` 与 `memes.ai_description`/`ai_ocr_text`：分别保存人工和 AI 来源的图片描述/图片文字，四项都是普通用户可编辑字段；旧库由 `_migrate` 幂等新增空的人工字段，因此升级前已有值原封不动保留为 AI 内容。`JsApi.get_meme_details` 仅在打开单张详情时按需返回四个独立字段、向旧调用方提供人工优先回退 AI 的中性字段 `description`/`ocr_text`、标题、文件名、直接所属分组（完整层级路径）、全部可选分组、全量标签建议和全图库前后项；新版 `update_meme_details` 独立保存四个字段，旧版中性载荷仍写 AI 字段。详情保存以单事务写入标题、文件名、文字、标签与分组归属。分组更新保留未移除成员关系的 `sort_order`，新加入分组追加到末尾；这里只更新 `meme_collections`，不移动磁盘文件。文件名变化时 JsApi 先校验安全名称/扩展名/冲突，再同步重命名实际文件，DB 写入失败则把文件名回滚，避免加入分页网格载荷
  - `memes.daily_filename`/`daily_file_size`/`daily_mime_type`/`daily_profile`：通常指向 `data_dir/derivatives` 中的可再生日常副本；旧库由 `_migrate` 幂等补列。超过 20 MiB 且选择仅压缩归档时，`compressed_only=1`、`source_file_hash` 保存原始输入哈希，压缩文件直接作为 `cache_dir` 正式归档，`daily_filename` 与 `filename` 相同且不得额外复制 derivative
  - `notion_sources`：本地 Meme 与 Notion 托管文件的多对一来源映射；以 data source/page/property/file index+name 唯一定位，保存 `last_edited_time`，`meme_id` 删除时级联清理；旧库与旧备份由 `_migrate` 自动建表
  - `chat_records`：保存 `/omm` 捕获的完整 JSON 结构包；以 `(account_id, source_message_id)` 唯一，重复归档同一外层消息时更新。`title` 保留 QQ 来源摘要，`name` 是不会被重新归档覆盖的用户名称，`media_folder` 是 `chat-record-media` 下不区分大小写唯一的可编辑目录名；旧库迁移时分别以现有标题和来源哈希回填。`package_json` 保存来源消息、命令位置、`_omm_media_folder` 以及所有已获取的嵌套 forward 数据；详情改目录名必须先词法校验并移动真实目录，再同步重写媒体 `local_path`，DB 失败则回滚目录。`list_chat_records`/`count_chat_records(keyword)` 同时搜索名称、媒体文件夹名、来源标题、会话和标签；`get_chat_record_entry` 返回摘要+完整包；`get_chat_record_by_source` 用于重复归档时复用已落地媒体和自定义目录。删除记录必须经 `onebot.delete_chat_record_archive` 同步删除其独占媒体子目录；旧库与旧备份由 `_migrate` 自动建表
  - `categories`：与 `collections` 分离的单归属层级类别；`memes.category_id` 只保存一个类别节点，父类别筛选递归包含子类别。类别名称经过目录安全校验，`cache_dir/<类别路径>/<filename>` 是原件的真实存储位置；详情换类、类别重命名/删除会同步移动原件，原有多对多分组不改变路径。
- `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`
- `MemeDB.search()`: 动态 WHERE, 多标签交集用 `HAVING COUNT = len(tags)`；keyword 始终匹配 `filename`/`original_name` 和标签名（`meme_tags` JOIN `tags` 子查询 LIKE），`include_description` 同时控制 `manual_description`/`ai_description`，`include_ocr_text` 同时控制 `manual_ocr_text`/`ai_ocr_text`。`genre=image|gif` 是由 MIME/`.gif` 扩展名推导的不可编辑系统体裁条件，不写入 tags 表。`JsApi.search_memes`/`count_memes` 从配置 `search_description`/`search_image_text` 传入；`count()` 必须同步该逻辑以保证分页总数一致
- `MemeDB.add_tags_to_memes(meme_ids, tags)`: 批量合并追加标签（get-or-create + `INSERT OR IGNORE`，不清空各表情已有标签），返回实际存在的表情数；先过滤出实际存在的 meme id（外键开启时对缺失 id 写 `meme_tags` 会整批失败），批量写包 try/rollback/re-raise；供 JsApi `batch_add_tags` 使用
- `MemeDB.add_memes_to_collection(meme_ids, collection_id)` / `MemeDB.move_memes_to_collection(meme_ids, from_ids, to_id)`: 批量加组/移动的单事务实现（先校验表情 id 与目标分组存在，写入失败 rollback 后抛出）；按 `INSERT OR IGNORE` 实际新增关联计数（重复加入目标不计）；move 仅纳入实际属于 `from_ids` 子树的成员（非成员不受删除或移动影响），从子树删除后再加入目标，提交前级联清理源子树内变空的分组（迭代删除无成员且无子分组的叶子空组直到不动点，支持子组先空父组后空；仅限 from_ids 范围，子树外空分组保留；两处 FK 均 ON DELETE CASCADE，故只删叶子防波及）；供 JsApi `batch_add_to_collection`/`batch_move_to_collection` 使用（JsApi 负责目标解析/创建与「不能移入源分组自身或其子分组」校验——`create_collection` 会复用同名顶层分组，故该校验在目标解析后统一执行；move 成功后无条件重建 manifest——`moved==0` 时成员关系仍可能变化，如成员已在目标、仅从源移除）
- `memes.sort_order`: 自定义排序（拖拽更新），默认 0，查询 `ORDER BY sort_order ASC, updated_at DESC`
- `collections.parent_id`: 任意层级分组支持，`NULL` 为顶层分组
- `collections.is_hidden=1` 唯一标记不可重命名、不可删除的“隐藏”系统根分组；`ensure_hidden_collection()` 优先原地升级既有同名顶层分组，否则创建。`hidden_collection_enabled` 默认开启：`JsApi._hidden_collection_state()` 展开该根的完整子树，除非当前正浏览此子树，否则把这些 ID 作为 `exclude_collection_ids` 统一传入表情、聊天记录、最近使用、收藏夹、类别和分组计数查询；SQL 使用 `NOT EXISTS`，因此内容即使同时属于普通分组也不会在外部泄漏。隐藏根仍可创建子分组，子分组沿用普通删除逻辑；移动成员时的空分组清理必须保留 `is_hidden=1` 根节点
- 主窗口侧边栏以 tabs 分开「分组」与「类别」：`CollectionTreeNode` 渲染两棵树，类别列表以虚拟节点 `-1`“全部”开头（计数为表情与聊天记录总数，前端映射到 `activeCategory=null`，不能作为拖拽或详情保存目标），类别支持递归子类别、搜索/详情选择和右键新建/重命名/删除；表情包与聊天记录卡在窗口内向左拖到树节点时分别追加多对多分组或切换单归属类别（图片同步移动原件），继续拖出窗口才进入原生文件/QQ 拖放；类别父节点筛选包含后代，切换 tab 只改变左侧树并清除旧悬停高亮，不清空分组/类别筛选、不调用 search，保留当前内容、页码及选择。右侧路径导航和子文件夹由 contentTab/contentItems/activeContentId 按实际筛选驱动，点击面包屑或子文件夹仍沿当前内容树导航；侧栏点击具体节点才切换筛选，另一棵树不误标“全部”为当前页。节点通过 `node-hover` 把悬停 ID 传给 `App.vue`；列表载荷包含表情的直接 `category_id`、由 `get_meme_collection_ids_map` 一次批量查询得到的 `collection_ids` 和当前页批量计算的 `recently_used`，前端按侧栏树展开后代 ID，只为当前页匹配表情添加 `sidebar-related` 高亮（含类别“全部”、收藏夹、最近使用、未分组和未分类），不在悬停期间逐卡查询后端。
- `meme_collections.sort_order`: 分组内成员自定义排序
- `recent_uses`: `meme_id` + `used_at`，成功使用时以毫秒时间 `INSERT OR REPLACE`，按 `used_at DESC` 取最近使用；虚拟分组计数调用 `count_recent()`，不得用有限条查询结果长度代替总数
- “最近使用”同时覆盖成功复制、复制原件、原生文件拖放及拖到 QQ 后的成功发送，并受 `record_recent_use` 控制；操作完成后前端刷新虚拟分组 `-3` 的计数，当前正在查看该分组时还要重新查询内容。主窗口再次显示时调用 `window.refreshRecent()` 修复隐藏期间的陈旧状态

### 配置
- `%APPDATA%/OhMyMeme/config.json` (Win), JSON 格式
- 密钥字段 (ftp_password, s3_secret_key 等) 用 Fernet 加密存储
- 全局单例: `get_config()`, `get_db()`
- `hotkey_show_at_mouse` 默认 `false`，控制 Windows 上全局热键显示隐藏主面板时是否按鼠标位置放置
- `disable_auto_hide` 默认 `false`，关闭热键会话的自动隐藏（复制/拖拽成功后保持窗口可见）；设置页「基础设置 → 全局快捷键」区块复选框 `s-disable-auto-hide`（`get_settings`/`save_settings`/`reset_settings` 透传，门控在 `WebUI.schedule_hide()` 入口）
- `hover_zoom` 默认 `true`，控制主界面悬停卡片放大预览是否启用；设置页「基础设置 → 悬停预览」区块复选框 `s-hover-zoom`（`get_settings`/`save_settings`/`reset_settings` 与 `get_init_data` 均透传）
- `library_genres` 保存主窗口图片/GIF/聊天记录的体裁勾选，首次默认全选，空数组必须保留；`get_init_data` 经 `normalize_library_genres` 返回，`useMemes` 在首次列表查询前恢复。恢复完成后用单例 watch 捕获体裁变化并串行调用 `set_library_genres` 写本地配置，避免快速勾选的请求乱序覆盖；保存不得扫描文件、等待限流或触发同步/刷新。沿用主页/随机的既有体裁重置行为，重置后的勾选同样保存；重复导入定位必须保留体裁勾选，不得重新全选或覆盖偏好。搜索词、临时多选及分组/类别导航不随此配置持久化，不依赖 WebView 隐私模式下的浏览器存储。
- `show_category_uncategorized` 默认 `false`，设置页「基础设置 → 侧边栏与层级」控制有真实子类别的节点是否增加只读“未分类”虚拟子入口（零计数也保留，叶类别不加）。`get_categories` 与 `get_init_data` 返回这棵浏览树，详情和创建搜索仍走不含虚拟节点的 `_build_category_options`；批量类别选择器从浏览树展平时按既有正 ID 规则排除虚拟节点。虚拟节点 ID 为 `-真实类别ID-4`（类别域内 `< -4`），附 `direct_category_id`；仅用作筛选，不能进入数据库、存储路径、重命名/删除/拖放目标。`_category_filter_ids` 为本地图片、聊天记录与预热统一解析为单一父类别 ID，云端按完整 `category_path` 精确匹配；普通正 ID 仍递归包含子树，根级 `-4` 仍匹配无类别内容，隐藏规则/体裁/标签/关键词/分页照常叠加。两套设置 API 回显/保存/恢复默认保持一致，设置窗口开关变化时通知结构刷新；`refreshCategories` 在虚拟入口被关闭或失去真实子类别时退回父类别，父级已删除则回全部。面包屑、折叠祖先高亮和悬停匹配不得将虚拟 ID 当成真实类别。
- `manifest_include_tags` 默认 `true`，控制 `manifest.build()` 是否把标签写入 `meme-index.json` 每个条目的 `tags` 数组（关闭时条目不含 `tags` 键，本地标签不受影响）；设置页「云端同步」区块开关
- `manifest_include_collections` 默认 `true`，控制 `manifest.build()` 是否把分组树写入 `meme-index.json` 顶层 `collections`（关闭时整个键不写入；设置页「将分组写入同步清单」开关，关闭前弹确认「关闭后将无法云同步分组」）
- `manifest_include_favorites` 默认 `true`，控制 `manifest.build()` 是否把收藏文件名写入 `meme-index.json` 顶层 `favorite` 数组（关闭时整个 `favorite` 键不写入，本地收藏不受影响）；设置页「云端同步」区块开关
- `notion_token` 属于 `_SECRET_KEYS`，与其他密钥一样加密写入配置；Notion URL/ID、data source ID 及属性映射保存在同一配置文件
- `napcat_token` 属于 `_SECRET_KEYS`；`napcat_enabled`、HTTP/WebSocket 根地址、Token、`napcat_command`（默认 `/omm`）和 `napcat_send_mode`（`original_native`/`original_rebuild`/`archive_rebuild`/`self_native_relay`，默认 `original_native`）由设置页「QQ 聊天记录（NapCat）」保存。接口地址强制为 `127.0.0.1`/`localhost`/`::1` 回环地址，HTTP 与 WebSocket 都拒绝重定向且绕过系统代理，命令必须是以 `/` 开头且不含空白的单词
- `Config.derivative_dir` 固定为 `data_dir/derivatives`，存放可再生日常副本；不跟随自定义 `cache_dir` 迁移，不进入备份或远程同步。仅压缩归档的正式文件位于 `cache_dir`，因此正常参与类别移动、备份和同步
- `bulk_read_protection` 默认 `false`；开启后由 `bulk_read.guarded_read(priority=...)` 对可能读取虚拟盘原件的操作单文件串行化，并按 `bulk_read_batch_size`（后端钳制 1–1000，默认 8）和 `bulk_read_pause_seconds`（0–60 秒，默认 1）分批冷却。优先级为 `critical`（保存换类/改名、单图拖入，可越过冷却）、`foreground`（当前页按需生成）和 `background`（后续页预热、旧 pHash 回填）；高优先级在当前单文件操作结束后先于已排队低优先级执行。已接入启动缓存扫描、pHash/日常副本惰性生成、导入、manifest 重建、存储迁移、备份、同步上传和 LAN 拉取；仅为 115/CloudDrive2 等虚拟盘降低突发请求，不应默认开启在普通本地磁盘上
- `cache_dir`（表情包图片与聊天记录媒体目录）可自定义：配置键 `cache_dir` 非空时 `Config.cache_dir` 返回该路径，否则默认 `data_dir/cache`；`Config.chat_record_media_dir` 固定为其下的保留目录 `chat-record-media`，其中每份聊天记录初始使用来源账号+外层消息 ID 哈希出的独占子目录，之后可在详情页安全重命名，结构包保存 cache 相对路径。表情扫描、文件查找、LAN 与 Notion 来源查找必须跳过这个根级保留目录，避免把归档图片误注册成表情。设置页「存储位置」通过 `SettingsApi.pick_storage_dir`/`apply_storage_dir` 切换，`apply_storage_dir` 可选把旧目录文件递归迁移（跳过 `thumbnails`）；**切换后旧文件不再可见**，故未迁移时必须确保文件已存在于新目录；`_storage_dir_validation` 拒绝相对/相同/上下级目录以及 `data_dir`/`thumbnail_dir` 及其上下级（受保护路径）；DB/缩略图/manifest 仍留在 `data_dir`，数据库只存表情文件名，聊天记录媒体保存 cache 相对路径；`reset_settings` 恢复默认时保留 `cache_dir`。迁移为**后台三阶段幂等**设计（避免跨盘长拷贝时进程被杀导致分裂状态）：①复制阶段源只读（`O_EXCL` 排他写入，dst 已存在且大小一致视为已复制跳过——幂等；失败/取消仅清理本次新副本，源完好无分裂，不回滚）；②写配置（唯一切换点，此后新目录已完整）；③删源（失败仅残留旧目录冗余，不阻断）。迁移开始写 `data_dir/storage_migration.json` 清单、完成后删除；`main.py` 启动时若检测到未完成清单则后台幂等续跑（强杀/断电后重启自愈）。取消由 move 回滚改为删新副本，消除回滚自身失败风险

### NapCat / OneBot 连接

- 会话桥接插件的首次 QQ 拖放注入不得由 `plugin_init` 阻塞等待；窗口枚举和各窗口脚本等待上限 1.5 秒，并行处理窗口。超时只停止等待，不取消 Electron 操作；WeakMap 复用同窗口／同操作的未完成 Promise，拒绝重复排队。后台重试捕获生命周期代次，停止／重载后旧回调不得重新注入或释放新代次的 busy 标记；卸载先解除主进程事件处理，再有界等待渲染端清理。
- `qq_drag_cleanup_on_start` 默认 `true`，设置页「联网 → QQ 聊天记录（NapCat）」复选框 `s-qq-drag-cleanup-on-start` 保存后从下次启动生效。`main._start_qq_drag_temp_cleanup` 只在 `OhMyMemeApp.run` 启动 daemon 任务，不在设置保存或插件监听重载时触发。`onebot.cleanup_qq_drag_temp_files` 仅扫描 `tempfile.gettempdir()` 顶层，按最后修改时间严格超过 24 小时删除 `ohmymeme-market-face-<UUIDv4>.ommchat` 与 `ohmymeme-market-face-<UUIDv4>-drag-icon.png`；必须完整匹配名称、跳过目录/符号链接/Windows reparse point，禁止宽泛前缀删除或递归清理。商城表情临时媒体和剪贴板缓存不属于此清理范围；单文件失败继续处理并记录汇总，不阻塞启动。
- `package_json.source_locations` 保存等价原生消息位置，旧归档的 `source` 自动作为候选；按账号/会话/真实序号去重，`last_success_at` 只用于最近可用排序，原位置没有特殊优先权。`MemeDB.add_chat_record_source` 在写锁中原子合并位置，不能覆盖正文、名称、媒体目录或分类；详情保存与 `save_chat_record` 都必须合并数据库中已有位置，防止陈旧载荷覆盖并发新增副本。模式二从其他位置恢复后以 `save_chat_record(..., record_id=...)` 写回原 ID，不能创建新档案。配置 WebUI 时模式一直接按持久位置转发；模式一、二均逐个尝试位置，不在读取失败时删除位置。插件 `/forward-message` 按目标 peer、当前发送者、资源 ID、送达状态与时间匹配新消息回执并返回 `source_location`；`retry_safe=false` 或接口传输失败表示可能已提交发送，禁止尝试下一位置而重复发送。只有完整原生转发和未筛选的 `qqnt_native_replay` 可新增等价副本，过滤、普通重组及自聊重建不得混入。位置随本地数据库备份保存，不额外发消息或改变页面导航。
- 重新归档时媒体路径按节点、正文/引用上下文和同类型槽位匹配，不得将新增引用媒体与旧正文媒体一起按全局序号 zip。已在本记录目录中的媒体原地保留，新媒体遇到既有文件名时分配独立名称，不能覆盖仍被旧结构引用的文件。
- 引用原消息保存为 `reply.data.omm_quoted_message`，其媒体参与归档、预览和重组。`/omm` 与拖拽共用恢复链路；`getMultiMsg` 的递归签名是 `(最外层来源 peer, 最外层 rootMsgId, 当前转发节点 msgId)`，不能以当前节点替换根消息或来源会话。节点 `records` 只有回复快照时不能据此跳过嵌套转发读取。标准解析树与 action 恢复树合并时保留 reply/JSON，最终树再补引用正文。一次恢复共用一份原生媒体索引，桥接导入/恢复请求上限 600 秒和 64 MiB 响应。
- `websocket-client` 连接 NapCat 正向 WebSocket 服务；NapCat 必须为该服务开启 `reportSelfMessage`。`OneBotCommandListener` 仅处理当前账号自己的 `message`/`message_sent` 事件，不调用 `get_recent_contact`。命令包括：回复记录后发 `/omm` 保存、`/omm <message_id>` 排障保存、`/omm list` 列最近十份归档、在目标会话发 `/omm send <ID>` 取用
- 命令事件经单 worker 串行处理，按命令消息 ID 去重，WebSocket 断开后每 3 秒重连；启动、设置保存/立即应用、恢复默认和应用退出分别负责监听器的启动/重载/停止。成功或失败通过 `send_group_msg`/`send_private_msg` 回到命令所在会话
- `build_chat_record_package()` 先用 `get_msg` 获取外层消息，再递归调用 `get_forward_msg`；NapCat 的 OneBot 全局配置必须开启 `parseMultMsg` 才能展开多层嵌套记录。节点正文兼容 `node.message`、NapCat 4.18 的 `node.data.message` 与 `node.data.content`，并把消息段中的 `type: node` 嵌套包装统一视为内联 forward 层级。归档上限为 100 份 forward 资源、12 层、20000 节点和 64 MiB JSON，任何上限、嵌套读取失败或最终仍无正文的空节点都会拒绝写入不完整数据；空节点也可能来自过期、撤回或未支持类型，不能一律归因于 `parseMultMsg`。`archive_chat_record()` 随后沿实际聊天节点树递归查找 image/record/video/file（同时覆盖 forward 资源与 `parseMultMsg` 内联内容），依次尝试桥接插件回填的 QQNT 本地路径或刷新 URL、标准消息段 URL，以及类型专用内部标识，并原子复制到 `cache_dir/chat-record-media/<来源哈希>/`（图片单文件上限 64 MiB，其他媒体 512 MiB），在媒体段写入 cache 相对 `local_path`，摘要以 `media_localized`/`media_localized_count` 标识结果；重复保存先复用既有本地媒体并清理同记录目录中的过期文件。虚拟盘清理必须按归档结构中声明的安全相对路径作词法保留，不能因刚写入文件暂时无法 `is_file`/`resolve` 而误删。模式二、三发送前逐段把媒体解析为真实本地文件，单段失败只降级该段，不能直接把可能过期的接收态 URL 交给 NapCat，也不能因一个媒体失败而降级整份记录。重组时 image 必须保留受限的数字 `sub_type` 与 `summary`，使 `sub_type=1` 的动画/自定义表情仍按表情发送；NapCat 接收 `marketFaceElement` 时会兼容转换为含 `emoji_package_id`/`emoji_id`/`key` 的 image，发送前必须还原为 mface，不能上传成本地普通图片。face/mface/record/video/file 等其他消息段保持原类型。`migrate_legacy_chat_record_media` 在启动后台把旧 `data_dir/chat-record-media` 文件逐记录复制、落库后才删旧文件
- NapCat 的 OneBot `message_id` 是当前进程内 `MessageUnique` LRU 生成的短 ID，重启、淘汰或切换账号后不能作为永久索引；归档必须同时保存来源账号、来源会话、QQ `real_seq`、完整消息段和节点内容，不能只保存 `message_id` 或 forward id。模式一检测到当前账号与归档账号不同时必须跳过短 ID 直转；私聊仅当当前账号等于归档的 `source_peer_id` 时才将 peer 翻转为 `source_self_id`，其他第三方账号必须拒绝；翻转后仍要验证序号、时间、发送者和卡片类型。数据库迁移会用 `package_json.source.real_seq` 修正早期误存短 ID 的 `source_message_seq` 摘要列
- `send_chat_record` 严格按 `napcat_send_mode` 选择单条路径，测试失败时不会跨模式自动回退：`original_native` 在配置 WebUI 且有真实序号时直接由鉴权插件 `/forward-message` 按来源会话+`real_seq` 获取当前进程可用的 QQ 原始 `msgId`，验证时间/发送者/合并转发类型后调用 `MsgApi.forwardMsg`，这仍是同一种原生发送方式。某一位置不可取时尝试其他等价位置；所有位置均不可取时明确失败，不自动改用重组。缺少 WebUI 或旧位置没有 `real_seq` 时仅兼容同账号 OneBot 短 ID 转发；`original_rebuild` 调 `build_chat_record_package` 重新读取原消息节点，并按媒体顺序合并旧归档中仍存在的 `local_path`，取得原始长消息负载后还要 `save_chat_record` 写回同一归档。模式二的 `/recover-message` 必须接收 `source_self_id` 并复用 `storedSourcePeerId`：B 在 B-A 私聊导入而 A 重读时，应转为 A-B 视角查询 B；解析结果还要把 `self_id`、`target_id`/`group_id` 更新为当前账号视角。`archive_rebuild` 只读取本地 `package_json`。两种重组路径先把聊天记录树递归转换成 OneBot `node`；配置 WebUI 时 `_send_qqnt_native_rebuilt_forward` 通过系统临时目录中的随机 JSON 文件调用插件 `/send-native-forward`。存在 `omm_native_payload_b64` 时插件只原样上传该负载并替换外层卡片资源 ID，返回 `qqnt_native_replay`；除最外层筛选专用的 repeated msgBody 删除外，不得修改原生负载内部节点。旧归档没有负载时才逐叶暂存到自己的 QQ 会话，让 `multiForwardMsgWithComment` 生成模板，再在 wire bytes 中重建节点；这条兼容回退不能保证 Windows QQ 接受。不得将原生 action 先解码成 NapCat 对象再用 `UploadForwardMsgV2` 重编码，该路径会丢失/改写 QQNT 未公开字段。新导入节点会保存 `omm_sender_uid`；旧归档可用 `user_id` 转换 UID，转换失败必须报错，不得默认成自己。临时文件限制 64 MiB，插件仅接受系统临时目录内 `ohmymeme-native-forward-` 前缀，读取后立即删除。没有 WebUI 地址时才沿用 OneBot `send_*_forward_msg`。接收态 image/record/video/file 不能原样回传：`_media_send_source` 按 `local_path → path → url → file` 选择 HTTP(S)/file/base64 或确实存在的绝对路径；没有归档副本时，图片内部文件 ID 再经 `download_file_image_stream`（兼容回退 `get_image`）尝试还原，统一写为发送态 `{file: source}`。旧 OneBot 路径遇媒体下载失败时仍会转文字占位重试；QQNT 原生路径失败则明确报错，不盲目重发，避免结果已发出但响应异常时产生重复卡片
- 详情页用结构路径（`n<节点下标>/s<消息段下标>/...`）将「发送时屏蔽」保存到 `package_json.send_filter.blocked_node_paths`，不删除归档正文。屏蔽父节点表示屏蔽整个嵌套子树；服务端必须校验路径实际存在。模式一完全忽略该规则并原样转发；模式二重新读取后要把规则复制到新包，模式二、三、四都由 `_forward_nodes` 在媒体解析前跳过已屏蔽节点。若全部屏蔽路径都指向最外层 `n<下标>`，`_native_forward_replay` 仍传递原始负载与路径，插件只删除主 `MultiMsg` action 对应的 repeated msgBody，保留其他 action、节点消息头和未知字段，并同步卡片计数/预览；只要存在嵌套路径就必须禁用整包重放并使用过滤后的 nodes 重组，绝不能冒险漏发被屏蔽内容。
- `self_native_relay` 只读取本地归档，使用同一 `_forward_nodes` 媒体解析，并强制要求 NapCat WebUI 地址和 Token。Python 不附带已保存的 `native_payload_b64`，而由独立的 `/send-self-native-forward` 路由选择模式；插件返回 `qqnt_self_native_relay`。该模式不得在失败时自动切换到任何其他发送方式。
- 深层重组仍列入 README 待办：`_forward_nodes` 当前保留最多两级内层记录（连同最外层卡片共三层），更深内容在最深可用层加说明后展开，保证不丢消息。`/send-native-forward` 已具备自内向外的 QQNT 原生构造能力，但在完成更深层兼容矩阵前不得擅自移除应用侧层级限制；不直接修改 `napcat.mjs`，也不维护 NapCat fork。
- **聊天记录拖到 QQ 直接发送（Windows）**：聊天记录卡 pointer 位移超过 8px 后调用 `JsApi.start_chat_record_drag`；`native_drag.start_chat_record_target_drag` 用私有 `OhMyMeme.ChatRecord` OLE 格式启动仅用于选目标的 `DoDragDrop`，不向 QQ 投递文件、文本或命令，并用 `QueryContinueDrag` 区分松手与 Esc。松手后按光标位置取得顶层窗口，严格要求进程名为 QQ；`qq_drag.resolve_qq_target` 先向 NapCat WebUI 的当前会话桥接插件读取真实 peer ID（先以 WebUI 初始 Token 换取短期 Credential），插件未安装、Token 未配置或没有可用状态时才通过 Windows UI Automation 对聊天区顶部固定 15 个坐标做点命中，只读 Button/Text 名称（禁止 `FindAll(Descendants)` 遍历整个 QQNT 消息树，也不要恢复上百次跨进程点查询），再与 `get_group_list`/`get_friend_list` 唯一匹配成真实 ID。匹配同时使用好友昵称和备注；允许 QQNT 在唯一别名两侧附加状态文字，但只有全部包含匹配均指向同一个目标时才发送。歧义或不可读时拒绝发送并返回实际采到的控件名称。成功后直接按 `napcat_send_mode` 调 `send_chat_record`，群内只出现最终记录卡片。QQNT 不提供带消息/资源 ID 的标准拖出数据，所以反向拖入 QQ 卡片暂不支持，导入仍用回复卡片后 `/omm`。
- **NapCat 当前会话与持久消息桥接插件**：`napcat-plugin-ohmymeme-current-peer/index.mjs` 注册受 WebUI 鉴权保护的 `/current-peer` 路由。拖拽发送的主路径必须把落点 QQ 顶层 HWND 传给插件；插件用 Electron `BrowserWindow.getNativeWindowHandle()` 精确匹配同一个渲染窗口，只读取 `.chat-header .panel-header__title`，应用再用 `get_group_list`/`get_friend_list` 唯一映射成 OneBot ID。这个 `confidence=renderer_title` 路径不依赖“最近一次切换”，因此多聊天窗口也不会串到前一个会话。旧事件路径只作无 HWND 调用兼容：`onUserTabStatusChanged` 一到即先清空旧目标，`onMsgBoxChanged` 的唯一候选可以更新；`onMsgInfoListUpdate`、`onMsgInfoListAdd`、`onInputStatusPush` 都可能来自后台会话，绝不能注册成当前会话更新源。插件清理时只删除自己安装在复用 listener 上的回调。`/forward-message` 把持久来源 UIN 转为内部 UID，以 `MsgApi.getMsgsBySeqAndCount`（并用查询 API 回退）精确匹配 `msgSeq`，再直接调用 `MsgApi.forwardMsg`。`/recover-forward-resource` 用 `PacketApi.pkt.operation.FetchForwardMsgRaw` 同时读取主 `MultiMsg` 与附属 action，从 `richMsg.m_fileName` 重建内联 forward 树；`src/onebot.py` 对每个顶层 forward 资源都调用它，并把外层来源会话、`real_seq`、时间与发送者一并交给插件，因此节点正文已完整时也会补全媒体；只有存在空节点时插件失败才属于结构恢复错误，否则静默继续后备媒体链路。NapCat 4.18.28 的 Packet 伪造转发把外层及嵌套 `com.tencent.multimsg` ARK 的 `extra` 生成为对象，而 Windows QQNT 原生卡片要求它是末尾带换行的 JSON 字符串；插件分别包装 `MsgApi.sendMsg` 与 Packet 转换器的 `rawMsgWithSendMsgToPacketMsg`/`rawMsgToPacketMsg`，只把该应用且对象形式的 `extra` 规范化为 `JSON.stringify(extra) + "\n"`。已经是字符串的原生转发及其他 ARK 不修改，清理插件时仅在包装函数仍属于本插件时恢复原方法；`/debug-state.forward_card_compat` 返回钩子状态与累计改写数。媒体归档优先复用 QQ Chat Exporter 的路径：按持久来源重新取得外层原消息，再递归调用 `MsgApi.getMultiMsg(sourcePeer, outerMsgId, parentMsgId)` 获取带真实 `msgId`/`elementId` 的内层 RawMessage。转发图片先复用 QQNT 缓存，再用原生 `picElement` 经 `FileApi.getImageUrl` 生成带当前 rkey 的地址下载，取址或下载失败时才回退 `FileApi.downloadMedia` 与原生服务；视频、语音和文件分别复用 Native RawMessage 的路径或 `getVideoUrl`/`getVideoUrlPacket`、`getPttUrl`、`getFileUrl` 刷新地址，再按同类型顺序回填到多 action 协议树。若真实消息树不可取，图片才从 `MsgInfo.index` 回填 NapCat 4.18 `PacketMsgPicElement` 丢失的 `fileUuid`、MD5 和大小，依次尝试现有 QQNT 缓存、协议节点自身身份、visit/UUID 内核接口与签名 URL。图片签名地址下载严格限制为腾讯 `qq.com`/`qq.com.cn`/`qpic.cn` HTTPS 域名、拒绝任意外部主机和 64 MiB 上限，校验图片格式及可用时的 MD5 后原子写入 QQNT 标准媒体缓存，再交给 OhMyMeme 复制到永久归档。受 WebUI 鉴权保护的 `/media-status` 仅保留最近一次恢复的逐媒体阶段、布尔值和 HTTP 状态码，不记录消息 ID、元素 ID、URL、Token、本地绝对路径或媒体内容；`/media-capabilities` 只列出 QQ 内核媒体服务的方法名，用于定位版本特有的拖拽导出能力。`src/onebot.py` 先调用 `/api/auth/login` 用 WebUI 初始 Token 换取短期 Credential，再访问插件路由；插件/渲染标题不可用时才使用 UIA 标题降级。`scripts/install_napcat_current_peer.ps1` 默认只复制插件入口与清单；对包含“只接受内置白名单 ID”限制的 NapCat Framework，可显式追加 `-PatchLocalWhitelist`，脚本会备份并仅在精确锚点存在时修改 `napcat.mjs`。插件内部 API 属于 NapCat 高级 API，升级 QQNT/NapCat 后需复验 Electron DOM 选择器与消息查询签名。
- **Windows QQNT 重组兼容**：导入时 `/recover-forward-resource` 在不超过 5 MiB 时附带 `native_payload_b64`，`src/onebot.py` 对资源式根记录兼容存于根 `forward_records`，对 `parseMultMsg` 已直接内联为 `data.content` 的根记录则通过 `/recover-message` 从真实外层消息下载并存于包顶层 `omm_native_payload_b64`。模式二重新读取后必须写回同一归档；若本次恢复未取得新负载，必须保留旧归档负载，不能用只有正文的新包覆盖。模式二、三调用 `/send-native-forward` 时传入原始负载、归档卡片及来源会话类型/ID，通过 `SsoSendLongMsg` 原样重传以保留发送者与头像，但这两种重建方式仍不保证 Windows QQ 可打开。若内联根记录只有原生负载而没有独立 `native_card`，插件不得保留自聊种子模板的 `news`；必须根据过滤后的 `payload.messages` 生成最多三行 `昵称: 预览`（文本、图片等媒体及嵌套聊天记录占位），并把 `summary` 更新为实际原生负载节点数。`self_native_relay` 是独立的桌面兼容回退：插件把 `_forward_nodes` 内容逐条真实发到当前账号自聊，递归调用 `multiForwardMsgWithComment`生成内层和外层卡片，最后以自聊为 source 调用 `MsgApi.forwardMsg` 发往目标。该路径不使用原始负载，不调用任何发送者头改写，`senderShowName` 必须统一为当前账号昵称；所有节点因此显示为当前账号。为避免服务端资源失效，暂存叶子消息和中间卡片不得自动撤回；不得向自聊转发仍含伪造发送者头的资源卡片。外层 ARK 规范化钩子仅作为旧 OneBot 路径兼容保留。
- 外层 `native_card` 存在时必须完整克隆，只替换 `resid`/`uniseq`/计数等必要的易变字段；卡片缺失时才重建 `news` 预览。遇到 JSON 消息段时，Python 详情预览和 NapCat 外层预览都应完整解码卡片，优先使用 `prompt`，其次使用 meta 子项的标题或描述，只有确实无可读摘要时才显示 `[json]`。
- 合并转发节点里的 `group_id` 等会话字段可能来自兼容回退值，不作为来源依据；会话类型、会话 ID、外层消息 ID/序号必须从用户选择的外层原消息单独保存

### 同步
- manifest 文件: `meme-index.json`
- SHA-256 差异对比, `push(delete_remote)`/`pull(remove_local)`
- **push 仅上传云端缺失/变更文件**: `push()` 拉取远端 `meme-index.json` 后按「文件名缺失 或 `sha256` 不同」过滤出待上传集合（`remote_memes` 为准），只把这些交给 worker；`files_total`/`bytes_total` 与进度只反映待上传文件，`skipped` = 云端清单一致的本地文件数。**不再对每个文件发 `file_exists`（HEAD）复核物理存在**——云端清单为唯一判据（大库同步省去逐文件往返；代价是清单声称存在但物理被带外删除的文件不会自动补传，需重跑/清理孤儿）。远端无 manifest（首次同步）时 `remote_memes` 为空 → 上传全部本地文件
- 远端路径: `{root}/memes/`, `{root}/meme-index.json`
- 同步进度: `_sync_state` 全局变量追踪进度，`get_sync_progress()` 供 JS 轮询
- `push()`/`pull()` 内循环中更新 files_done/bytes_done/current_file 等字段
- 前端 300ms 轮询 `get_sync_progress()` 显示进度条 + 实时速度
- 进度/完成弹窗固定显示：原设置页 4 个开关已移除，`_FIXED_CLOUD_TOGGLES` 在 `Config._load`/`update_from_dict` 强制默认（上传/下载进度与完成提示均开；删除远端/删除本地多余/上传前不再提醒/启动获取索引均关）
- **顶栏同步状态指示（SyncStatusBadge.vue）**: `JsApi.get_sync_status(force=False)` 返回 `configured/manifest_state/refreshing/checked/error/checked_at/local_count/remote_count/local_missing/cloud_missing/synced/transfer`。模块级 `_sync_status_state`（manifest/cache_loaded/checked/refreshing/error/updated_at）由 `_sync_status_lock` 保护，`_sync_status_gen` 代次丢弃在途旧结果；`_sync_status_snapshot()` 首次查询先读 `cloud-index.json` 缓存出值，未检查过则起 daemon 线程 `download_index()`（`_start_sync_status_refresh` 去重，`force` 强制重拉），`run_auto_sync`/`_cloud_refresh_worker`/`SettingsApi.check_sync_status` 复用已拉取清单写入 `_sync_status_set_manifest`，`JsApi`/`SettingsApi` 的 `sync_push`/`sync_pull` 完成后 `_sync_status_invalidate` 让下次轮询重拉；`_cloud_reset` 同时 `_sync_status_reset`。差集口径：`local_missing = len(remote_ready - local)`（`remote_ready` = manifest 中文件名安全且 sha256 合法的远端条目集合，与 `cloud_missing` 过滤一致），`cloud_missing = len(local - remote_names)`（本机有云端没有），`synced = manifest 存在且两者均为 0`；`sync_type` 为空返回 `{"configured": false}`。`transfer` 取 `sync.get_sync_progress()`，`active` 仅 uploading/downloading/deleting，`remaining = max(files_total-files_done, 0)`
- **SyncStatusBadge 交互**: 前端按 `sync_type` 决定图标是否显示（未配置云端不渲染）；状态图标 = 云 + 绿色对勾（已同步）/ 云 + 黄色感叹号（未同步/无清单/拉取失败），拉取中云图标脉冲；传输中切换为「内圈↑↓ + 外圈进度环」，环取 `files_done/files_total`（无文件总数回退 `bytes_done/bytes_total`，均未知为不定进度动画），封顶 99%。点击图标在标题栏下方弹出 272px 浮层（fixed 定位、随按钮定位、箭头对齐图标）；传输中浮层显示「还有 N 个文件」、进度条、`n/N`、速度、当前文件与「传输在后台继续进行」，鼠标移离 220ms 自动关闭；非传输态显示已同步 / 「本地缺失 N 个文件」/「云端缺失 N 个文件」、本机与云端计数、相对检查时间，点击外部或 Esc 关闭（App.vue `onDocKeydown` 在隐藏窗口前先关浮层），右上角刷新按钮 `get_sync_status(true)` 强制重拉。轮询：传输中 600ms、空闲 5s、刷新中 900ms；窗口 focus/可见时立即刷新；传输结束自动关闭传输浮层并补一次普通刷新。`aria-haspopup`/`aria-expanded`、`role="progressbar"`、`role="dialog"`、`prefers-reduced-motion` 降级均有
- **SVG/主题 token**: `style.css` 新增 `--success`/`--warning`/`--warning-text`（浅色主题分别 `#16a34a`/`#d97706`/`#b45309`），供状态角标与缺失计数使用
- **多线程传输**: `push()`/`pull()` 使用 `ThreadPoolExecutor`，每个线程创建独立后端连接
- 并发数: 配置项 `sync_threads`（默认 3，范围 1-8），通过 `config.json` 或 `SettingsApi` 修改
- `_push_worker`/`_pull_worker`: 接收文件子列表，操作独立后端连接，原子递增 `_sync_state`；`_push_worker(entries, remote_root, cache_dir)` 只负责上传调用方已过滤好的缺失/变更文件（无存在性跳过分支），`_pull_worker` 仍按本地清单/文件存在性跳过未变项
- **WebDAV 目录创建去重**: `_push_worker` 同一批次所有文件都上传到 `memes/`，只在循环前 `ensure_remote_dir` 一次；`_WebDAVBackend.ensure_remote_dir` 对**每个目录 URL**在 `_dav_dirs_lock` 内原子执行「缓存检查 + MKCOL + 缓存写入」，成功/405/复核命中均写 `_dav_dirs` 缓存，多 worker 并发对同一目录也只发一次 MKCOL——避免重复 MKCOL 触发远端锁。**`push()` 每次在拿到 `_sync_run_lock` 后先清空 `_dav_dirs`**，避免命中上一次同步缓存、跳过已被删除远端目录的 MKCOL。
- `_sync_lock` (`threading.Lock`) 保护 `_sync_state` 写操作；`_increment_sync_progress()` 提供原子递增
- `_chunk_list(lst, n)` 将文件列表均匀切分给各线程
- **push 动态 manifest 维护**: `push()` 上传过程中每 `_HEARTBEAT_INTERVAL`（5s）用「远端已有 + 本次已确认上传」快照增量更新远端 manifest（`_build_push_manifest` + `_upload_manifest_data`，失败仅告警不中断）；部分失败中断前也上传该快照（避免远端有文件却无有效 manifest）；成功路径在合并远端独有项后补入已上传但不在本地清单的项（去重 guard），保本地被清空等边角。manifest 只列确认上传成功的文件，不产生幻影条目
- **孤儿清理互斥与进度**: `cleanup_remote_orphans(delete=True)` 删除前非阻塞获取 `_sync_run_lock`（被 push/pull 占用时返回「同步正在进行中」，绝不并发删除）；删除循环复用 `_sync_state`（`direction="delete"`，更新 current_file/files_done/files_total/progress，状态 deleting→done），前端复用 `#sync-progress-overlay` 轮询展示；扫描（delete=False）不互斥
- **远端分组递归恢复**: `_apply_remote_collections()` 按 `collections` 树递归创建父子分组（`create_collection(name, parent_id)`），逐节点按 `filenames` 关联已下载表情；跳过非 dict/空名/非法或不安全文件名。修复手机上传的多级分组在桌面 pull 后只剩顶层空分组（#102）。
- **远端文件名校验**: `_safe_remote_fname()` 拒绝路径穿越/绝对路径/隐藏名；`_fetch_remote_memes` 解析远端 manifest 时过滤不安全文件名（含非 dict 条目），`_pull_worker` 下载前二次校验
- **S3 后端 OSS 兼容**: boto3 客户端固定 `signature_version='s3'`（V2 签名，boto3 的 V4 与 chunked encoding 强耦合，OSS 不支持）；寻址方式由 `s3_addressing_style` 配置控制（默认 `"virtual"`，可选 `"path"`），映射到 `BotoConfig(s3={"addressing_style": ...})`；阿里云 OSS 仅支持 virtual-hosted style（bucket 作子域名），path-style 请求被拒绝；设置页 S3 表单「寻址方式」下拉框切换
- **WebDAV 后端**: 配置项 `webdav_url`（服务地址，须 http(s):// 开头且含主机名）/`webdav_user`/`webdav_password`（Basic Auth，密码经 Fernet 加密存储）/`webdav_path`（远程路径前缀，可选）/`webdav_timeout`（默认 30s）/`webdav_allow_insecure`（默认 false，自签名证书时跳过校验）；基于 Python 标准库 `urllib.request` 实现 PROPFIND/MKCOL/PUT/GET/HEAD/DELETE，无新增依赖

### 云端直接使用
- **二次开发集成**：`_cloud_filtered` 与本地查询统一遵守隐藏子树（隐藏页内除外）、单归属类别路径、分类标签键和体裁筛选；最近使用/随机不混入云行。冷启动和刷新创建远端分组/类别名称树，空本地库也须计算云差集。云下载通过安全 `category_path` 拼接远端/本地路径，保留 `source_sha256`/`compressed_only`；系统临时文件经 `shutil.move` 支持跨盘落入原件目录，不覆盖已有文件。SHA 缩略图优先读取本地日常副本，后台生成与旧静态缩略图检查保留 `guarded_read` 优先级闸门。含云行的页面不允许排序，云卡不可编辑或拖出，点击下载后恢复本地操作。
- 配置 `cloud_direct`（**默认开**），设置页「云端同步」区块开关；`sync_type` 空→非空（首次配置云端）时 `showConfirm` 用**「开启/关闭」**按钮二选一确认（默认已勾选时仍询问一次；Esc/点遮罩返回 `null` 表示不改动，与点「关闭」的 `false` 区分，其余调用方按 falsy 判断不受影响），「关闭」即取消勾选（随保存生效）；开启期间 push 上传缩略图（`_push_thumbs`，gate 该开关），远端删除联动删缩略图不 gate
- **启动静默补传缩略图**：配置 `cloud_thumb_auto_push`（默认开），设置页「云端同步」复选框 `s-cloud-thumb-push`；`run_auto_sync`（启动）在 cloud_direct 开且开关开时经 `_start_thumb_autopush`（模块级单飞锁去重，异常仅告警）起 daemon 线程跑 `JsApi._auto_push_thumbs` → `ensure_local_thumbs`（补齐本地缺失/过期缩略图）→ `sync.auto_push_thumbs`（门控 cloud_direct+开关+sync_type，全部在建连前短路；复用 `_push_thumbs`：`list_files` 远端 thumbnails 差集上传，list 不可用回退 `file_exists` 单查；`ensure_remote_dir` 缺失自建）
- **缩略图统一**: 本地 `thumbnails/{sha256}.webp`（静态 150px WebP q85，mkstemp+os.replace 原子写；源为动图时逐帧生成动画 WebP——≤150px、超 100 帧按步长抽帧且帧延时×步长、loop 保真、编码失败回退静态首帧）== 远端 `{root}/thumbnails/{sha256}.webp`（`REMOTE_THUMB_DIR="thumbnails"` 与本地同名），单一路由 `/api/thumb/<sha256>`（`re.fullmatch` 校验）；`_migrate_thumbnails`（start 时）删除旧 `{id}_{size}.png` 等非内容哈希缓存，按需从日常副本再生成；`_thumb_is_stale`（源动图+缩略图单帧的旧产物）由 `ensure_local_thumbs` 在启动后台线程与 push 前重建；`_delete_meme_files`/同步 pull 删除按 `file_hash` 清理
- **云态（webui.py 模块级）**: `_cloud_state`（manifest/missing/local/loaded/refreshing）由 `_cloud_lock` 保护，`_cloud_inflight`+锁做下载防重入；`_cloud_ensure_loaded` 冷启动读 `cloud-index.json` 缓存，`_cloud_view` 本地文件名 frozenset 变化才重算 `sync.cloud_missing`（否则复用）；`_start_cloud_refresh(fetched)` 门控开关+refreshing 去重起 daemon（`run_auto_sync` 内复用 fetch 结果，fetch 关则线程内 `download_index()`，失败保留旧缓存），完成后 `save_cloud_manifest` → `prefetch_thumbs` → `_notify_cloud_ready()`（`evaluate_js("window.onCloudReady&&window.onCloudReady()")`）
- **展示**: `_cloud_filtered`（tags 全含 issubset、keyword 小写子串匹配 name+filename+tags、-2 收藏、-4 无分组、分组按全路径前缀、-3 最近使用不含）→ **清单序穿插合并**：有云行时 `_cloud_order` 取顺序源（全部/收藏/未分类/标签/搜索按 `manifest["memes"]` 全局序，正分组按该分组子树 `filenames` 展平序，本组成员在前递归子组接后），本地全量取回（不带 offset/limit）后 `_merge_manifest_order` 与云行按清单位置穿插、**不在清单的本地行（新导入未推送）保持原序排最前**，对合并结果切 `offset/limit`；清单不可用回退旧「本地前、云尾跨两段切片」；`count_memes` 合计不变（合并长度==本地+云）、`get_tags` 并入云端独有标签、`get_collections` 把云收藏/云无分组计入 -2/-4、`_build_collection_tree` 按全路径计云成员；云行顺序源 = 远端清单序（测试 `test_cloud_order_follows_manifest`、`test_cloud_merge_extras_first_and_collection_order` 锁定）；云行 `cloud:true`+`file_hash`（sha256）且**无 id**——前端收藏/右键/排序拖拽/原生拖出/hover 全守卫，drag-select option `:disabled` 排除框选、卡片点击 stopPropagation 防入 selectedIds，全选与排序持久化过滤云行；卡片左下角 `.cloud-badge`，`window.onCloudReady` 通过统一刷新入口更新网格+标签+分组+类别
- **缩略图自愈**: `serve_thumb` 404 且 sha ∈ 云缺失集 → `_enqueue_thumb_fetch` 单飞队列 → daemon `_thumb_fetch_worker` 调 `prefetch_thumbs` 逐个补拉 → 整批完成 `_notify_cloud_ready()`（前端 thumbRev 重载，滚动到缺图处即自愈无需重启）；`prefetch_thumbs` 失败项第二遍重试；`JsApi.cloud_refresh()`（前端刷新按钮 `rescanCache` 调用）→ `_start_cloud_refresh()` 重拉清单+预取（refreshing/开关去重）；`_ThreadedWSGIServer.request_queue_size=128`（wsgiref 默认 backlog=5，懒加载突发连接易被丢弃致整行图片失败）；前端 `onImgError` 带 cache-buster 有界重试 2 次（`onCloudReady`/加载成功清计数）
- **`JsApi.cloud_download(filename)`**: 开关/同步配置 gate → 清单条目查找 → inflight(busy) → `download_single` → 流式 sha256 比对 → 字节/像素导入限制 + PIL 头校验 → `_IMPORT_LOCK` 内哈希去重或落盘 `add_meme` → 显式移除 missing 条目（本地集合未变不触发重算）→ `_cloud_backfill` daemon（标签/逐段分组链/收藏/`build_manifest`/`_notify_cloud_ready`）→ `copy_meme` 自动复制；status: disabled/no_sync/not_found/busy/download_failed/sha_mismatch/too_large/invalid_image，成功 `{"ok": true, "status": "copied"|"imported", "id": ...}`。前端成功路径先加 `cloudWipe` 类播放 `.cloud-wiping::after` 遮罩自上而下擦除动画（`clip-path: inset(100% 0 0 0)`，0.45s，期间 `cloudBusy` 保持防连点），动画结束再 `search()` 把云行转本地
- **开关/云后端变更**: `SettingsApi/JsApi.save_settings` 检测 `cloud_direct` 或 `sync_type` 变更 → `_cloud_reset()`（清态+删缓存文件），开启时 `_start_cloud_refresh()` 立即重拉；`reset_settings` 同样 `_cloud_reset()`；设置页 `saveSettings` 用 `_lastCloudDirect` 检测本次保存刚开启且 `sync_type` 非空 → `showConfirm` 询问是否立即 `syncPush()`（`doSyncWithProgress` 内部会重存一次设置与常规上传按钮等效），把缩略图推上云端供缺失表情显示
- 远端旧 manifest 缺 sha256 的条目跳过不显示（`cloud_missing` 计日志）；测试 `tests/test_cloud_direct.py`

### 更新
- GitHub API 查询: `/releases/latest` → `/releases?per_page=5` 回退
- **仅检查稳定版**：`_parse_release` 跳过 prerelease 与含 `nightly` 的 tag（保证软件更新绝不指向非正式版）；`_parse_version` 跳过非数字段（如 `0.6.0-nightly`）
- **非阻塞检查**：`check_latest_cached(force=False)` 是唯一入口——**`_ensure_check_started` 先查 `_check_running`（在跑则一律返回 `pending`，含 force 刷新未完成时，绝不命中旧 `_check_result`），再对非 force + 新鲜缓存（`_CHECK_TTL`=24h）直接返回**；无缓存/缓存过期/`force=True` 触发后台 daemon 线程跑 `check_latest()` 填 `_check_result`+`_check_result_at`（`_check_lock` 保护，幂等只启动一次），立即返回 `pending: true`（永不阻塞网络 3.8s~20s+）。**generation token**：`reset_check_cache()` 推进 `_check_generation`，后台 `_task` 完成时仅当代号匹配才写结果，防在途旧任务覆盖 reset 后新状态。`webui.py` 的 `JsApi.check_update`/`SettingsApi.check_update` 支持 `(debug, force)` 透传；前端 `App.vue` 的 `checkUpdateAndPrompt`（onMounted/24h 定时）首发 `force=true`、pending 时转 `checkUpdateResult` 非 force 轮询，settings legacy 的 `checkUpdate` 首发 force、while 轮询暂取非 force——避免完成后再次 force 触发新检查造成永久 pending。**这解决了启动期间 `check_update` 同步阻塞曾导致的界面交互卡顿，及缓存不失效时 24h 定时器形同虚设的问题**
- 镜像并发: `_urlopen_mirror` / `_urlretrieve_mirror` 用 `ThreadPoolExecutor` + `as_completed`
- 镜像列表: `github.dpik.top` → `gh.dpik.top` → `gh-proxy.org` → 直连 GitHub（已移除 `proxy.starsfire.top`——该代理仅浏览器可访问，程序化请求 404）
- 下载进度: `start_download()` → 后台线程 → JS 每 500ms 轮询 `get_download_progress()`
- Linux 更新: `_pick_asset_url` 选取 `.AppImage` 资产；`run_installer` Linux 分支 chmod +x 后直接 `Popen`（AppImage 是 ELF 非 shell 脚本），无 `/dev/fuse` 时追加 `--appimage-extract-and-run` 回退（`_needs_appimage_fallback`）；下载默认文件名走 `_default_asset_name()`（Linux 为 `OhMyMeme-v{version}-x86_64.AppImage`）
- macOS 更新: `_pick_asset_url` 按当前架构选取 `.dmg` 资产（arm64/x86_64）；`run_installer` 走 `_install_dmg_macos`（`hdiutil attach` → `ditto` 复制 `.app` 到 `/Applications` → 打开应用程序目录）；`_default_asset_name()` 为 `OhMyMeme-v{version}-{arch}.dmg`

### 局域网互联 (lan.py)
- **入口**: `lan.start(port, secret)` / `lan.stop()`，`get_status()` 供设置页轮询；`set_allow_secret_config()` 控制是否允许密钥传输（仅内存生效），`set_confirm_callback()` 注入设备确认回调（WebUI 提供）
- **UDP 发现**: 绑定 `0.0.0.0:port`，收到 `{"t":"discover"}` → 单播回 `{"t":"hello","name","os","ver","need_secret"}`（**不含任何密钥信息**）；启用 `IP_PKTINFO`（Linux/Windows）后用 `recvmsg` 取广播到达接口（Linux `ipi_spec_dst` 得接口 IP、Windows 8 字节 `in_pktinfo` 只有接口索引无 spec_dst），`sendmsg` 把回包源地址钉在该接口（Windows 用 `IP_UNICAST_IF`+`connect`+`getsockname` 由索引反查接口 IP，发送时 `ipi_addr` 字段填源地址），虚拟网卡/多网卡环境回包不会走错接口或带上虚拟适配器 IP；`recvmsg` 不可用或非 Linux/Windows 退化 `recvfrom`/`sendto`
- **TCP 握手（明文帧）**: `[4B 长度][JSON]`；服务端发 `challenge{nonce}` → 客户端回 `proof{HMAC-SHA256(secret, nonce)}` → 验 `ok`/`no`（3 次错误断开）；无密钥时直接放行
- **数据帧（加密）**: `[4B 长度][12B IV][AES-GCM 密文+16B tag]`；密钥由 PBKDF2(secret, 100000) 派生；JSON 载荷，命令由手机（客户端）发起
- **设备确认（连接前置）**: 客户端握手后发 `device_info` 帧（`{name,model,os,ver}`，手机 Build.MODEL/MANUFACTURER/versionName）；桌面端 `_cmd_device_info` 弹窗展示设备信息，用户允许/拒绝后回 `{ok, approved, allow_secret_config}`；**未确认期间其他命令挂起**（`confirmed` Event，等待超时 60s 后拒），无确认回调（测试/无 UI）默认放行；`confirm_device()` 由 JS 回传批准结果（`pending_confirm` 记录 + `threading.Event`）；确认弹窗在**设置窗口**：`_lan_confirm_cb` 确保 `_settings_window` 存在（无则 `_create_settings_window`，创建失败才 `confirm_device(False)`）→ `focus_settings_window()` → 设置页 `showLanDeviceConfirm()` 弹窗 → `SettingsApi.lan_confirm_device` 回传；`get_status()` 暴露 `pending_confirm`（仅设备信息 dict），evaluate_js 推送失败不立即拒绝、由设置页 `lan_get_status` 轮询兜底展示（Esc = 拒绝）
- **传输进度**: `_transfer_state` 全局（`_transfer_lock` 保护，active/direction/files_done/files_total/bytes_done/bytes_total/current_file/start_time/updated），`_transfer_update()` 在 `pull_file`/`push_file`/`get_config`/`send_config` 处理成功时累计**本端实际收发字节与文件数**，总量取手机帧附带的 `meta`（`{files_total, bytes_total}`，无 meta 的旧手机为 0 走降级显示）；`get_status()` 返回 `transfer` 快照，空闲超 5s（`_TRANSFER_IDLE`）或 `files_done>=files_total` 时 `active=False`，下次操作/陈旧状态自动重置计数；设置页 `#lan-progress-overlay` 复刻云同步进度浮层（pct + speed 并排 12dp、进度条、「后台运行」按钮），settings legacy 自适应轮询（有传输 300ms / 空闲 5s），字节总量>0 显示百分比、否则文件计数（`n/N 文件`）、均未知显示「已传输 N 文件」，转 idle 自动隐藏（「后台运行」后本传输不再弹出）；与云同步浮层互斥列为 `_SETTINGS_OVERLAY_IDS`，Esc 关闭，方向标题「发送到手机/接收手机数据」
- **命令**: `pull_manifest` / `push_manifest`（复用 sync 的 `_apply_remote_order`/`_apply_remote_collections`/`_apply_remote_tags`）/ `pull_file` / `push_file`（base64 传输）/ `get_config` / `send_config` / `device_info` / `ping`
- **配置同步（双向，电脑为权威源）**: `get_config` 由手机拉取、`send_config` 由手机推送；两端均剔除 `_SECRET_KEYS`（FTP/S3/R2/WebDAV 密码等）；设置页「允许密钥传输」开关（`lan_set_allow_secret_config`，仅内存生效）开启后配置同步才包含密钥字段，开启前弹窗警示「请勿在公共网络或不信任的网络进行此操作！」；`device_info` 确认响应携带 `allow_secret_config` bool，手机端据此动态显示密钥拉取/推送按钮
- **文件安全（与手机端对称）**: `_safe_fname` 拒绝路径穿越/绝对路径；`push_file` 四重校验：文件名安全 → 字节 ≤`MAX_FILE_SIZE`（64MB）→ 可选 `sha256` 与本地计算一致 → `_import_bytes` 先解码校验宽高>0 合法才写盘；**不合法字节绝不落盘**（杜绝孤儿缓存文件）；合法图片按 SHA-256 去重后存 `cache_dir/{hash[:16]}{ext}` 并入库
- **生命周期**: 设置页开关临时启动（重启默认关，不写入 config）；`lan_port`/`lan_secret` 持久化（`lan_secret` 加密存储）；`main.py` `shutdown()` 兜底 `lan.stop()`

### 本地 HTTP 安全加固
- Bottle 只绑 `127.0.0.1` 随机端口；`before_request` 校验 `Host` 必须为本机回环（`_host_allowed`），POST 额外校验 `Origin` 同源且 `Sec-Fetch-Site` 非 `cross-site`，拒绝则 403（阻断 DNS rebinding / 跨站注入）
- **多线程服务器**：`bottle.run` 通过 `server_class=_ThreadedWSGIServer`（`ThreadingMixIn + WSGIServer`，`daemon_threads=True`，`request_queue_size=128`——wsgiref 默认 backlog=5，网格懒加载缩略图的突发连接易被丢弃导致整行图片加载失败）启用多线程——Bottle 默认 wsgiref 单线程串行处理，慢请求（`/api/contributors` 外网抓取、`/api/thumb` 现场生成缩略图）会阻塞其他路由投递，导致设置页 bundle/settings.css 排队、JS 监听未注册期间窗口可见但拖动/点击全部无效
- `/api/contributors` 结果带 1h TTL 缓存（模块级 `_CONTRIBUTORS_CACHE`，刷新在 `_CONTRIBUTORS_LOCK` 内单飞：**取锁后刷新时间并重查退避期**，等待中的并发请求不再重复抓取；刷新失败一律退避 60s 重试——有旧缓存回退旧缓存，无缓存冷启动失败返回 502，不再每个请求都反复触发 10s 慢抓取），设置页该图片加 `loading="lazy"`（位于默认隐藏的「关于」section，仅切换到该分组时才发起请求）；缩略图生成写盘为先写临时文件再 `os.replace` 原子替换（多线程下并发请求同一未缓存缩略图不再交错写产生永久损坏的缓存文件）
- `after_request` 统一加 `X-Content-Type-Options: nosniff` / `Referrer-Policy: no-referrer` / `X-Frame-Options: DENY`，`/api/` 路由 `Cache-Control: no-store`
- 文件名安全：`_safe_serve_filename`（webui）与 `_safe_remote_fname`（sync）拒绝含 `/` `\`、以 `.` `/` `\` `~` `..` 开头的名字；`_find_meme_file` 入口校验，远端 manifest 文件名在 `_fetch_remote_memes` 过滤 + `_pull_worker` 写盘前再防御
- 前端 XSS：`utils/api.ts` 的 `esc()`/`renderMarkdown()` 转义所有拼入 innerHTML 的外部/动态数据（远端分组名、GitHub 版本号、QQ 昵称、输出目录、弹窗标题/正文等）；设置窗口 `legacy.js` 同理
- **键盘无障碍（主窗口）**：全局 `:focus-visible` 焦点环（2px `var(--primary)`）；meme 卡/文件夹卡 `role="button" tabindex="0"` + Enter/Space 复制或打开分组，标签栏 span 同理（`aria-pressed`）；标题栏图标按钮全部带 `aria-label`；弹窗焦点管理：`utils/api.ts` 的 `rememberFocus()`/`restoreFocus()`/`trapTabFocus()`（模块级 `_focusTarget` 记录打开前焦点、关闭归还、Tab 在弹窗内循环），InputDialog/TagEditor 打开聚焦输入框、ConfirmDialog 默认聚焦「取消」（危险操作需显式点「确定」）、CollectionBuilder 聚焦 `#cb-name`；`#meme-grid.sort-enabled .meme-card:focus-visible` 为 2px 实线焦点环
- **键盘无障碍（设置窗口）**：`settings.css` 全局 `:focus-visible` 焦点环（2px `var(--accent)`）；`.btn/.import-row/.nav-item/.title-btn:focus-visible` 用 box-shadow 双环、`.nav-item.danger:focus-visible` 红色环、`.check-row input[type="checkbox"]:focus-visible`；危险按钮统一 `.btn-danger-outline` 类（hover 变红 + 红色 focus 环，取代内联 `border-color:#ef4444`）；覆盖层焦点管理：`legacy.js` 的 `rememberSettingsFocus()`/`restoreSettingsFocus()`/`trapSettingsFocus()`/`visibleSettingsOverlay()`（模块级 `_settingsFocusTarget` + `_SETTINGS_OVERLAY_IDS` 列表），各覆盖层（LAN 确认/danger/sync 进度与完成/LAN 进度/QQ/QQNT/TG/抖音/微信/上传确认/更新弹窗）打开时记住焦点、打开后聚焦首元素、关闭归还；全局 keydown 先对可见覆盖层做 Tab 循环陷阱，再按 Escape 依次关 danger→lan-confirm（拒绝）→dy→tg→wechat→qq→qqnt→sync-progress→lan-progress→sync-done→关设置窗口；静态覆盖层 `role="dialog" aria-modal="true"`，`#toast` 加 `role="status" aria-live="polite"`（主窗口 `#toast` 同理）
- **设置窗口 UX**：保存模型—表单控件改动经 `initDirtyTracking()` 置 `_settingsDirty`，`closeSettings()` 变 async，脏时弹「有未保存的更改」确认再关（取消/×/Esc 均触发）；`saveSettings`/`getSettings`/`resetSettings` 成功后清脏；真正立即生效的控件（LAN 开关、密钥传输、存储位置「应用更改」）带 `.immediate-hint`「立即生效」标注；状态色收敛为 token：`--success: #22c55e`/`--danger: #ef4444`，JS 用 `setStatusColor()` 切 `.status-ok/.status-error` 类（不再写死 `#4caf50/#f44336`），HTML/CSS 内联色改 `var(--danger)`；复制处理下拉用 `.select-row`（label span + select），不再包 `.check-row`
- **关于页**：设置窗口左侧导航末项「关于」（`data-group="about"`）收纳从基础设置迁出的版本更新区块（`s-ver-current`/`btn-check-update`/`s-update-status`，逻辑不变）；页面含大号 OhMyMeme logo（`.about-logo`，span 用 `--accent`，复刻主窗口标题栏效果）、版本号+检查更新，及贡献者名单（`.about-contributors` 深色卡片）。贡献者头像不走外部直连：`/api/contributors` 路由（webui.py Bottle）用 urllib 抓取 `contributor.starsfire.top/TNTXZ/OhMyMeme` 的 SVG（该服务忽略 `?bg=` 参数且无 CORS 头，浏览器直接 fetch 会失败），用 `svg.replace` 剥离白色背景 `<rect>` 后以 `image/svg+xml` 返回，使圆形头像直接落在深色页面上；`onerror` 时隐藏图片并显示「贡献者名单加载失败」回退文案。版本行下方含「GitHub 项目地址」「加入 QQ 群」「打开环境检测」按钮（前两者 `openAboutUrl` → `SettingsApi.open_url`，仅允许 http/https scheme，经 `os.startfile`/`open`/`xdg-open` 交系统默认浏览器打开，失败 toast 提示；后者 `openEnvCheck` → `SettingsApi.open_env_check` → `env_check.spawn_ui()` 非阻塞开检测窗口，成功/失败 toast）
- **对比度（H2）**：`--muted: #8a94a8`（在 bg/surface 上 ≥5:1）；实心主按钮/激活态文字/选中态 outline 用 `--primary-strong: #1d4ed8`（白色文字 6.7:1、`--primary-light` 背景文字 5.49:1）；`--primary #3b82f6` 仅用于 hover 高亮，不作小字/浅底文字色

### 环境检测
- WSL 检测: `/proc/version` 包含 "microsoft"
- WSL 时设置 `MESA_LOADER_DRIVER_OVERRIDE=llvmpipe`, `LIBGL_ALWAYS_SOFTWARE=1` 等软渲染环境变量
- **首次运行 WebView2 检测 (`env_check.py`)**：仅 Windows，检测项为 WebView2 Runtime 安装与版本 + .NET Framework ≥ 4.6.2（`NDP\v4\Full` 的 `Release >= 394802`，同 pywebview `_is_chromium` 阈值）；非 Windows 返回占位通过项。**版本门槛 = max(pywebview 源码阈值, 项目门槛 `_PROJECT_MIN_WEBVIEW2`)**：前者运行时正则解析已装 pywebview 的 `platforms/winforms.py` 中 `_is_new_version('86.0.622.0', build)`（后端可用底线，解析失败回退 `_MIN_WEBVIEW2_FALLBACK`）；后者 `94.0.992.0`——pywebview 6 在 CoreWebView2 初始化回调中**无条件**执行 `settings.IsSwipeNavigationEnabled = False`（`ICoreWebView2Settings6`，SDK 1.0.992.28 引入），按 WebView2 forward-compat 规则（API 所在 SDK 第三段 build ≤ Runtime 第三段 build）需 Runtime ≥ 94.0.992.x，更旧 Runtime 上该 setter 抛 NotImplementedException 中断初始化（白屏）。本项目**未用** pywebview 文件拖放的 `AdditionalObjects`（SDK 1.0.1774.30 = Runtime 113 门槛——无 `file_drop` 监听，`num_listeners==0` host 侧早退，拖入走 HTML5 drop + `/api/upload`），故 113 不构成门槛。Runtime 读取：EdgeUpdate 4 渠道 GUID（Stable/Beta/Dev/Canary）HKCU+HKLM 读 `pv`，取最高且非 `0.0.0.0`。**UI 始终独立子进程**（源码 `[sys.executable, "-m", "src.env_check"]` + cwd=repo root，frozen `[sys.executable, "--env-check-ui"]`，Windows 下 `CREATE_NO_WINDOW` + stdout/stderr=DEVNULL）：启动处 `show_blocking()` 用 `subprocess.run` 阻塞（关闭后继续启动软件，失败仅 log warning 不阻断），设置页 `spawn_ui()` 用 `Popen` 非阻塞（`SettingsApi.open_env_check` → 「关于 → 打开环境检测」按钮）。UI 为 tkinter 窗口（每项通过/未通过 + 名称 + detail，失败时提示后续支持 winget 安装；「确 定」/Return 写标记，×/ESC 不写），tkinter 不可用回退 `ctypes.windll.user32.MessageBoxW`（IDOK 才写标记）。标记文件 `%APPDATA%/OhMyMeme/env_check.json`（`_get_config_dir()`，含 time+checks 快照），存在即不再自动显示；`--debug-env` 任意平台强制打开并把 `detect_checks()` 逐条 log

### 启动流程 (关键时序)
- **单实例互斥（防多开）**：`main()` 在 logging 配置后、`OhMyMemeApp()` 创建前调 `platform_util.acquire_single_instance()`——Windows 用 `CreateMutexW("OhMyMeme_SingleInstance")`（`GetLastError()==ERROR_ALREADY_EXISTS` 即已有实例，句柄存模块级 `_single_instance_handle` 防 GC，进程退出内核自动释放，崩溃安全），POSIX（Linux/macOS/WSL）对 `tempdir/ohmymeme-<uid>.lock` `fcntl.flock(LOCK_EX|LOCK_NB)`（fd 保持打开）；已有实例运行时 Windows 弹 MessageBoxW「OhMyMeme 已在运行」后 `sys.exit(0)`，其余平台仅日志退出；互斥机制自身异常一律返回 True 不阻塞正常启动。Bottle 端口随机分配不构成冲突防线，故必须显式互斥
- **首次运行环境检测（UI 子进程须在单实例检查前）**：`--env-check-ui`（内部旗标，`help=argparse.SUPPRESS`）分支在 logging 配置后、`acquire_single_instance()` **之前** `run_ui()` 后 return——父进程可能持有单实例 mutex，子进程再检测会直接退出。检测 UI 关闭后、`OhMyMemeApp()` 创建前（debug 块之后）：`args.env_debug or (os.name == "nt" and not is_done())` 时 `show_blocking()`（惰性 import，异常 log warning 不阻断）；`--debug-env` 先把 `detect_checks()` 逐条 `logger.info`
- **源码运行自动编译前端**：`main.py` 启动时 `_ensure_vue_frontend()` 检查 `src/webui/dist/ohmymeme.js`，缺失（打包 `frozen` 或已有产物时跳过）则用 `npx.cmd`(Windows)/`npx`(其他) 跑 `vite build` 一次，失败仅告警不阻断启动
- **启动动画**：`App.vue` 挂载时播放 `src/resources/OhMyMeme.mp4`（通过 Bottle 路由 `/resources/<filepath:path>` 提供，`webui.py` 的 `RESOURCES_DIR`，basename 校验防路径穿越，路由须在兜底 `/` 之前注册；PyInstaller 以 `--add-data src/resources` 打包）；`onMounted` 设置 6s 兜底定时器 + `<video>` `@ended` 移除遮罩，`#startup-anim` 全屏遮罩 z-index 2000，`.startup-fade` 0.4s 淡出。**仅启动时播放**：快捷键/托盘仅 toggle 窗口显隐不重载页面，故不会重复播放。设置页「显示启动动画」开关（配置键 `show_startup_animation`，默认开，`useMemes` state 同步）控制：开启时 `loadInitData` 后立即 `startupVideoReady=true` 挂载视频并**并行加载**（无 300ms 延时，动画天然覆盖桥接稳定时间）；关闭时 `dismissStartupAnim()` + `setTimeout(..., 300)` 降级为 300ms 延时。`get_init_data`/`reset_settings`/`get_settings` 均透传该键。**遮罩背景贴合视频边框**：OhMyMeme.mp4 边框为纯黑，`webui.py` 写死 `_STARTUP_BG_COLOR = "#000000"`（不做运行时 ffmpeg 采样，避免影响启动速度），经 `get_init_data` 的 `startup_bg_color` 传给前端，`App.vue` 把该色同时应用到 `#startup-anim` 与 html/body 背景。**可跳过**：点击遮罩立即 `dismissStartupAnim()`；系统 `prefers-reduced-motion: reduce` 时直接跳过动画走 300ms 降级路径（`window.matchMedia` 检测）。**插件媒体（图片/动图）**：`startup_media_type == "image"`（或 `startup_video_src` 扩展名匹配）时改用 `<img>` 分支，`@load` 后按 `startup_media_duration_ms`（缺省 3000，上限 15000）定时收起，GIF 时长由插件侧 PIL 逐帧解析；视频分支仍 `@ended` 收起，插件 `set_startup_override(..., duration_ms, media_type)` 经 `init_fields` 下发这两个字段
- Vue `App.vue` 挂载后:
  - `waitForPywebview` 必须等待 `typeof window.pywebview?.api?.get_init_data === 'function'`，不能只判断 `pywebview.api` 对象存在：pywebview 分两阶段注入，先创建空 `api` 再注册方法。过早请求会被 `api()` 捕获并返回 null，跳过 `get_init_data`，导致已落盘的体裁选择不恢复（并可能抢跑更新检查）。`test_genre_restore_waits_for_registered_bridge_in_actual_app` 以内存构建的完整应用覆盖桥接缺失、空对象、部分注册及已就绪四种启动时序，并通过重开页面和真实 checkbox 点击校验保存恢复。
  1. 立即: `loadInitData()` → `get_init_data()` 加载数据库数据 → 秒开
  2. `checkUpdateAndPrompt()` 立即执行（与 rescan/同步并行）
  3. **动画开启**：视频播放期间即并行 `rescan_cache()` → `run_auto_sync()` → 重新搜索/标签/分组（无延时）；**动画关闭**：降级 `setTimeout(..., 300)` 后执行上述步骤
  4. `setInterval` 每 24h 再跑一次更新检测
- **300ms 延时不可移除** — 给 Bottle + pywebview 桥接稳定时间
- **必须先 rescan_cache 再 run_auto_sync** — 确保本地文件与 DB 一致后再对比远端，否则同步产生错误 diff
- **check_update 必须静默** — GitHub API 失败不阻塞启动

### 缓存扫描 (rescan_cache)
- 遍历 `cache_dir`，对每个非 `thumbnails/` 子目录的图片文件:
  1. 按文件名查 DB (`get_by_filename`) 跳过已存在
  2. SHA-256 哈希（64KB 分块）
  3. 按哈希查 DB (`get_by_hash`) 跳过重复内容
- **双重去重** — 文件名去重防止每次启动重复注册，哈希去重防止同图不同名重复
- `_do_import`（拖入/导入对话框）同样有哈希去重，且文件重命名为 `{hash[:16]}{ext}`
- **感知哈希相似去重**（`download_original_image` 单图导入路径）：`memes.perceptual_hash` 列（TEXT 存 16 进制，旧库自动 ALTER 迁移）持久化每张图的 64 位感知哈希（`_perceptual_hash`，8x8 可分离 DCT pHash，比均值哈希对浅色/低信息图判别力更强）。导入时哈希未命中则 `_find_similar_candidates` **只算新图 phash + 从 DB 读存量 phash 比对**（整数 XOR，微秒级）；`perceptual_hash` 为空的旧库行全部交给单例后台线程惰性回填，本次只比对已填的，禁止单图导入同步遍历虚拟盘。后台 `_build_cache_index` 一次性构建文件索引避免逐行 walk，并以 `background` 优先级让行。汉明距离 `_PHASH_SIMILAR_DIST<=12` 视为近似，**全库比对无截断漏检**。命中候选时将文件复制到独立临时文件登记 `_PENDING_SIMILAR`（token 随机、TTL 300s 过期时在 pop/next-register 时删除临时文件），返回 `similar_pending`，前端 `SimilarImportDialog` 弹窗让用户选：保留新图 / 保留旧图 / 跳过（discard）/ 都保留（keep_both），经 `JsApi.resolve_similar_import(token, action)` 决定导入或放弃。哈希精确命中返回 `duplicate` 提示「已存在」。`_do_import`/`scan_cache` 新建时写入 `perceptual_hash`（`add_meme` 内部转 hex，规避 64 位溢出 SQLite INTEGER）。两条单图交互路径都启用：`download_original_image`（URL 拖放/下载原图）与 `/api/upload/`（File 拖放，单张时走 `_import_with_similar_decision`）；HTTP File 拖放的 Base64 数据必须先写入系统本地临时目录，不能把待判重临时文件写到可能位于虚拟盘的 `cache_dir`。批量路径（多文件拖放/文件夹/同步 pull/LAN）不做感知去重（多文件走 `_do_import` 避免逐个打断）。`_do_import` 去重关键区（`get_by_hash`检查→copy2→`add_meme`→回查）由模块级 `_IMPORT_LOCK` 串行化：并发拖入完全相同字节的图也不产生重复记录（测试 `test_import_concurrency.py`）
- **相似候选缩略图**：`MemeDB.get_all_phash` 必须同时返回存储文件的 `file_hash`，由 `_find_similar_candidates` 原样传入候选；`SimilarImportDialog` 使用 `/api/thumb/<file_hash>`。不能把感知哈希或压缩归档的 `source_file_hash` 当作缩略图键，也不能通过重读原件补算哈希。`tests/test_phash.py` 覆盖真实数据库候选、缓存命中和从本地日用副本生成后通过 HTTP 返回 WebP 的链路。
- **重复导入定位**：所有交互式导入的精确哈希重复结果必须回传已有记录的 `duplicate_ids`（后台任务也写入进度状态）；前端通过显式 `record_meme_uses` 将这些本地记录写入最近使用，然后切到分组 tab 的 `-3` 虚拟分组，清除类别、标签和搜索并刷新，但必须保持 `activeGenres`（包括空数组），不能全选体裁或改写保存的勾选偏好。已有图片即使被当前体裁排除，也不能为了定位而自动改勾选。显式定位不受“自动记录最近使用”开关影响。相似图选择“保留旧图”时按距离最小候选执行同样定位；单纯“跳过”不定位。`test_duplicate_import_keeps_genres_and_saved_preference` 用真实 composable 的持久化 watcher 验证精确重复、保留旧图和大文件决策后的重复结果均保留体裁，最近使用定位及其他筛选清理仍正常。
- **原件与日常副本**：`config.py` 的 `_IMPORT_MAX_BYTES=200MiB` 是普通媒体硬上限，`_IMPORT_MAX_PX=2560` 只作为可选长边阈值的默认值；LAN 还受协议 `MAX_FILE_SIZE=64MiB` 限制。超过 `_LARGE_MEME_IMPORT_BYTES=20MiB` 时，配置 `large_meme_import_mode=ask|keep_original|compressed_only` 默认询问；主窗口统一对话框可保持选择，设置页也可切换。`compressed_only` 把 `create_daily_copy()` 的结果移入 `cache_dir` 作为正式归档，写入压缩后 `file_hash` 和原始 `source_file_hash` 防重复导入，不保留原件及重复 derivative；聊天记录媒体使用独立归档链路，始终保存原件。其余普通导入仍保存原件并生成可再生日常副本。同步清单携带压缩归档标记与原始哈希。仅压缩归档的预览、复制、拖拽和“复制归档版”均读取正式归档文件，删除时不得把同名 `daily_filename` 再当 derivative 删除
- **可选导入长边限制**：`import_resolution_limit_enabled` 默认 `false`（旧配置缺键也关闭），`import_resolution_limit_px` 默认 2560。设置页「导入」提供复选框及 1–65535px 数值框，关闭时禁用数值框但保留阈值；`Config.set` 规范化两项，`normalize_import_max_px` 钳制阈值，`get_import_max_px(cfg)` 在关闭时返回 0。普通单图/批量导入（含 Notion）、缓存扫描、云端单图下载、同步拉取新记录、LAN 导入都必须调用同一配置门控，不得继续直接用 `_IMPORT_MAX_PX` 拒绝。只影响新入库记录，不删除已入库内容，不适用于聊天记录内媒体；文件大小、Pillow 解码及 JPEG 总像素安全上限、1600px 日用副本和 150px/100 帧缩略图规则均不改变。两套设置 API 的回显、保存、恢复默认保持一致。
- **虚拟盘后续页预热**：`bulk_read_prefetch_pages` 仅在 `bulk_read_protection` 开启时生效；`useMemes.search/loadInitData` 每次把当前关键词、结构化标签、分组、类别、类型、当前 offset/page size/total 传给 `JsApi.start_page_cache_warmup`。新调用递增 generation 取消旧查询任务；worker 跳过当前页，从后续 meme offset 分块调用无图片探测的 `_search_meme_rows`，逐项以 `background` 优先级调用 `_get_thumbnail_path`，从而按需生成持久化日常副本与缩略图。当前页走 `foreground`，保存/单图拖入走 `critical`；闸门保证高优先级在当前单文件读取完成后抢占已排队后台任务。列表搜索的 WebP 动画判断只读取 `data_dir/derivatives` 中已有本地副本，缺失副本时先返回静态标记，不在查询阶段探测网盘原件。不创建前端 Image/DOM 缓存，不长期占用整库解码内存
- **文件夹导入** (`JsApi.import_folder`)：FOLDER 对话框 → `os.walk` 递归收集图片（扩展名过滤）→ **后台线程导入**（`start_import_job` + `_IMPORT_JOB_STATE`，前端 `ImportProgressOverlay` 300ms 轮询进度条 + 取消，取消时 `progress_cb` 返回 False 中断 `_do_import`，保留实际进度）→ `make_collection`（前端导入菜单「自动创建分组」勾选，默认开）时以文件夹名 `create_collection` + 批量 `add_to_collection`（同名分组复用，重复导入并入）。`import_memes`（文件对话框）同样后台化，走同一 job；`import_from_clipboard`（剪贴板，通常单张瞬时）保持同步返回 id。`_do_import` 提供可选 `progress_cb`（逐文件回调，返回 False 中断）
- **渠道自动分组**：3 个入库渠道导入后调 `WebUI.ensure_import_collection(ids, 固定名)` 自动归入固定名分组——TG→「Telegram」、抖音→「抖音」、微信→「微信」；同一渠道不同时间导入复用同名分组。QQ（导出 ZIP 到外部）、QQNT（提取到输出文件夹）不入库故不建组。`create_collection` 现为「先按 name+parent_id 查已存在→返回既有 id，否则 INSERT」——**不再产生重复空分组**（仓库同名字段多次导入只一个分组，成员靠 meme_collections 的 PRIMARY KEY 去重），测试 `test_ensure_collection_same_name_reused`/`empty_args`

### 本地备份与恢复 (backup.py)
- **定位**: 仅 PC 间整库迁移（导出 ZIP → 拷贝 → 恢复），手动触发，不做增量/自动清理/加密包；不碰手机 LAN 端
- **备份** (`create_backup`): 打包 cache_dir 全部原件（文件名即 hash 前缀跨机稳定，保留类别子目录）+ memes.db（`MemeDB.backup_to`，sqlite backup API 保证 WAL 一致快照）+ meme-index.json + backup.json 清单；`derivatives` 日常副本可再生，不重复打包。ZIP_STORED 不压缩（图片已是压缩格式）；先写 .tmp 再 os.replace 原子落盘。包结构：`db/memes.db`、`files/<类别路径>/<文件名>`、`meme-index.json`、`backup.json`
- **恢复** (`restore_backup`): 仅允许空库（`has_any_data()`——memes/collections/categories/tags/favorites 任一有数据即非空，含 stego 载体行与空类别/分组；SettingsApi 前置校验，落库前 `restore_from` 锁内复查）；恢复 worker 全程持有 `_IMPORT_LOCK`（与 `_do_import`/同步 pull 同锁），恢复期间导入请求阻塞等待；`create_backup` 与 `apply_storage_dir` 均校验备份目录与 cache_dir 不得相同/互为嵌套（防备份递归自包含）；ZIP 安全限制（成员数 ≤100k 且不得重名、单成员解压 ≤256MB、总解压 ≤20GB、仅 STORED/DEFLATED）；解包到 `data_dir/backup_restore_tmp` staging，成员名安全校验（防路径穿越，允许已校验类别子路径）+ 文件数与 backup.json 核对（防不完整包）→ **候选库先在隔离连接上 `MemeDB.prepare_restore_source`（integrity_check + 必需表存在性 + `_migrate` 预迁移），通过后才移动缓存文件**（记录本次新增文件，`restore_from` 失败即回滚删除；同名覆盖文件内容一致无需还原）→ `MemeDB.restore_from` 经 backup API 原子替换库内容 + `_migrate` 补齐旧版本缺失列 + 重设 WAL → `build_manifest()`；日常副本在首次使用时重建，staging 任一步失败自动清理，不留半成品
- **备份目录**: config `backup_dir`（空=默认 data_dir/backups），设置页选择立即生效（校验可创建/可写）
- **SettingsApi**: `backup_get_info`/`backup_pick_dir`/`backup_create`/`backup_delete`（文件名白名单校验防穿越）/`backup_pick_zip`/`backup_restore`/`backup_progress`；后台线程 + 前端 300ms 轮询进度（`_BACKUP_STATE`，与存储迁移同模式），恢复成功后自动 build_manifest
- **设置页 UI**: 独立「备份与恢复」导航分组（日志与关于之间）：备份目录选择、立即备份、备份列表（文件名/大小/删除，删除走 showConfirm 危险确认）、从 ZIP 恢复（pick_zip → showConfirm → 恢复）；`backup-progress-overlay` 进度弹窗（运行中不可关闭，完成后"关闭"按钮 + Esc）；列入 _SETTINGS_OVERLAY_IDS 做 Tab 焦点陷阱
- **测试**: TestBackup 覆盖创建/列表/删除（thumbnails 排除、路径穿越拒绝）、恢复元数据保真（tags/收藏/分组）、孤儿保留与同名覆盖、非空库拒绝（零落盘 + staging 清理）、非法包/非 ZIP（BadZipFile 上抛由 worker 捕获）/文件数不符
- **危险操作（清空全部数据）**: 设置页「危险操作」区块两个入口，均走 showConfirm 双重确认（输入 confirm）：`SettingsApi.delete_all_local`（`db.delete_all()` 清空库 + 删除 cache/与 thumbnails/ 全部文件 + 重建空 manifest）与 `SettingsApi.delete_all_cloud`（`sync.delete_all_remote()` 遍历远端清单删除 `memes/` 全部文件并删除远端 `meme-index.json`）

### 剪贴板 (GIF/WebP 直接传送)
- **Windows 编辑框文本历史**：两类窗口的 `events.loaded` 安装 `_TEXT_CLIPBOARD_SCRIPT`，仅接管普通 input/textarea 的非空选区 copy 事件，不拦截 cut、图片、密码框或密码自动填充字段；API 不可用时保留原生行为。复制请求按 FIFO 串行，并在下一事件轮处理；原生失败且焦点、内容与选区未变化时才回退浏览器复制，回退有重入保护。`JsApi/SettingsApi.copy_editor_text` 使用各自窗口 HWND，`copy_text_with_history` 预分配 UTF-16 文本和两个 DWORD，在同一次打开剪贴板中写入 `CanIncludeInClipboardHistory=1`、`CanUploadToCloudClipboard=0`、CF_UNICODETEXT。保留 WebView2 隐私模式，最多接受 4 MiB 的 Unicode 数据，不记录正文；失败时只释放未交给系统的内存并关闭剪贴板。
- **剪贴板测试隔离**：`tests/test_startup.py::test_clipboard` 必须模拟 pyperclip 及全部平台图片复制分发，禁止用应用图标或测试文字覆盖真实系统剪贴板。原生文本复制测试用假 Win32 API 验证历史/云标记、64 位句柄和失败清理；JS 使用 jsdom 验证选区、密码排除和失败回退，不运行真实复制做冒烟测试。
- `_copy_gif_windows` 同时写入三个剪贴板格式:
  - **CF_DIB** — BMP 首帧（去掉 14 字节 BMP 头），旧应用兼容
  - **CF_HDROP** — `DROPFILES` 结构体 + 文件 UTF-16 路径，QQ/微信需要此格式才能粘贴动图
  - **自定义 "GIF"** — 原始 GIF 字节，注册 `RegisterClipboardFormatW("GIF")`
- `_copy_webp_windows` 直接传送 WebP 原文件（不再转 GIF）:
  - **CF_HDROP** — 指向 `.webp` 文件路径，QQ/微信原生解码 WebP（含动画+透明）
  - **自定义 "WebP"** — 原始 WebP 字节，注册 `RegisterClipboardFormatW("WebP")`
  - **CF_DIB** — 首帧 BMP 静态回退
- `_copy_png_windows` — 带透明的 PNG 走此路径保留 alpha（CF_HDROP 指向 `.png` 文件 + 自定义 `"PNG"` 格式 + CF_DIB 回退）；不透明 PNG/JPG 仍走 CF_DIB（BMP）路径
- **移除 CF_HDROP 会导致 QQ/微信粘贴 GIF 变静态图**
- **复制处理模式** — config `copy_resize_mode`（0不处理；1webp缩放，默认；2转gif；3转gif隐写原图），仅设置页「复制处理」下拉选择（无主窗口开关）。复制超过 `copy_resize_max`（默认 200px）的静态图时按模式处理（动图 GIF/动画 WebP 不受影响）：`convert_image_mode_1` → `_resize_static_to_webp` 转 WebP 并**缩放到限制内**（唯一缩放原图的模式）；`convert_image_mode_2` → `_static_to_gif` 按**原分辨率**转普通 GIF（不隐写、不缩放）；`convert_image_mode_3` → `_make_stego_gif` 按原分辨率转隐写 GIF（失败原样复制原图，不回退缩放）。处理结果存系统临时目录（**不删除**，CF_HDROP 需在 QQ 粘贴时仍可读取）：`ohmm_resize_<md5>_<max>_q<质量>_v<版本>.webp` / `ohmm_gif_<md5>_v<版本>.gif` / `ohmm_stego_<md5>_v1.gif`；缓存键含编码参数与版本号，改编码逻辑后旧缓存自动失效，命中时校验完整性，同一表情重复复制复用。旧配置迁移：`experimental_stego=true` → mode 3，`copy_resize_enabled=false` → mode 0（仅当旧配置无 `copy_resize_mode` 键时）。开关 `copy_avoid_webp` 开启时 `convert_image_mode_1` 额外接收 `avoid_webp=True`，缩放产物改为 `ohmm_resize_<md5>_<max>_q<质量>_v<版本>.jpg`（不透明）或 `ohmm_resize_<md5>_<max>_v<版本>.png`（带透明，PNG 无质量参数故键中不含 `_q`），模式 2/3 不受影响
- **复制时避免 WebP** — config `copy_avoid_webp`（默认 `false`），设置页「复制处理」区块复选项。微信等应用把复制的 WebP 当作**文件**而非图片（`_copy_webp_windows` 的 `CF_HDROP` 指向 `.webp` 文件，微信优先读它），开启后**复制路径上的产物一律不含 WebP**。`JsApi.copy_meme` 读取开关：模式阶段把 `avoid_webp` 透传给 `convert_image_mode_1` → `_resize_static_to_webp(..., avoid_webp=True)`，**直接按目标格式编码**（不透明→JPG `_RESIZE_JPG_QUALITY`，带透明→PNG），避免「先转 WebP 再转 JPG」的二次有损；模式阶段之后统一过 `convert_avoid_webp(path, resize_max)` 兜底——按 Pillow 解码判定 `img.format == "WEBP"` 与 `is_animated` 分流：动图 → `_animated_webp_to_gif(image_path, max_side)`（`ohmm_webp_gif_<md5>_<max>_v<版本>.gif`），静态 → `_static_webp_to_jpg`（`ohmm_webp_jpg_<md5>_q<质量>_v<版本>.jpg`，带透明合成白底）。非 WebP 原样返回；转换失败**回退复制原图并 `logger.warning` 记录**，不阻断复制。**体积与缩放（实测定量，勿凭直觉改）**：GIF 无帧间压缩、逐帧整幅写入，长动图体积可远超源 WebP（实测本机全库 56 个动图：无上限 84MB，最大单个 6.4MB），故动图转 GIF 传入 `copy_resize_max`（默认 200px）作最长边上限并**等比缩小、永不放大**（`max(frames[0].size) > max_side` 才缩），实测 200px 上限把总量降到 22MB、最大单个 1.6MB，且 0.5x 缩放比隔帧抽取更有效（3965K→1298K vs →1983K）。**上限只作用于动图转 GIF，静态转 JPG 保持原分辨率**（维持「不处理」模式不缩放原图的既有语义）。**帧缩放须在迭代前定尺寸、逐帧 convert 后即时 resize**（勿收集全尺寸帧再统一缩）——长动画全尺寸 RGBA 帧可达上百 MB（实测最大 428x512/128 帧约 107MB），旧写法会同时持有全尺寸与缩放后两份。该重排为纯等价重构（实测 56 动图 x 3 种上限的 168 个产物 SHA-256/尺寸/帧数/延时逐条一致），故测试无法区分两种写法，勿据此认为缺少保护。`_has_alpha` 须同时判 `mode in ("RGBA","LA","PA")` 与 `"transparency" in img.info`——灰度 PNG 的 tRNS 以 `info.transparency` 表达且 `mode` 为 `L`，漏判会让透明像素被 `convert("RGB")` 当实色保留（实测该像素读数 255 而非合成白底）。**调色板降色不是有效杠杆**：实测 256→128 色体积无变化（0.99x）、64 色仅降 12%，且**预量化会丢透明**（`quantize()` 产生的 P 帧无 `transparency` 键，实测透明保留 0%），故帧一律以 RGBA 交给 Pillow 逐帧量化（透明保留 95%+），不得改为预量化。**动图转 GIF 的两个关键点（勿回退）**：①帧延时**保真**（`max(20, duration)`，仅防 0 值），**不得沿用已删除旧实现的 `<50ms→100ms` 钳制**——实测库内动图延时中位数 42ms，钳制会让动画慢 2 倍多；②`disposal=2` 逐帧清空画布，否则透明区域透出上一帧形成**残影**（该问题正是 v0.3.6 撤下 `_webp_to_gif` 的原因，HISTORY.md / commit `c1fb6bf`）。与模式 2/3 的产物（GIF）无冲突。库内文件/DB/缩略图/manifest/同步均不动，仅写系统临时目录（**不删除**，CF_HDROP 需在粘贴时仍可读）；**仅作用于复制到剪贴板，不覆盖 `start_native_drag` 拖拽出库**。测试 `tests/test_avoid_webp.py`（含帧延时/残影/白底/mode1 接线/上限与不放大小图/透明度元数据识别/尺寸与宽高比七项变异验证；注意转换产物缓存不删除，测试用 autouse fixture 隔离 `tempfile.gettempdir` 否则会命中上次残留而假通过）
- **GIF 隐写（复制模式 3 + 导入自动解码）** — ①复制输出：`copy_resize_mode=3` 时 `_make_stego_gif` **懒加载** `gif_stego.make_stego_gif` 生成携带无损原图的隐写 GIF（与原图同分辨率）再复制，失败原样复制原图；缓存 `ohmm_stego_<md5>_v1.gif`（不删除，CF_HDROP 需存在）。②导入含 `STG3` 的 GIF 时**无论模式与否**都会自动解码，且**只入库还原的原图**（`_try_decode_stego` 解码到临时文件 → 原图正常入库，`from_stego=1`，载体 GIF 不入库、不进缓存目录）。③隐写缓存：复制原图时通过临时缓存 `ohmm_stego_<md5>_v1.gif` 复用（命中即校验，不再重新编码）；`memes.stego_of_hash` 字段与 `get_by_stego_of` 保留用于兼容旧库中已入库的载体行。④前端展示：隐写载体在查询层隐藏（`search`/`count`/`get_recent` 统一加 `stego_of_hash IS NULL` 过滤，仅对旧库残留载体行生效），网格只显示还原后的原图；原图行 `from_stego=1`（`memes.from_stego` 列），卡片正常渲染图像并叠加琥珀色「隐写导入」徽标。⑤本地生成（复制路径）的隐写文件不写入 DB/不同步。`src/gif_stego.py` 支持 `encode`/`decode`/`make_stego_gif`/CLI，`quiet=True` 供应用调用

### 加密降级 (crypto_util)
- 优先 `cryptography.fernet.Fernet` (AES-128-CBC + HMAC)
- 降级: `hashlib.pbkdf2_hmac` 派生密钥 + XOR + base64（防意外泄露，不防专业破解）
- **不能移除 XOR 降级** — 无 `cryptography` 时系统崩溃

### Sync pull 集合合并
- `_apply_remote_collections` 递归按名称树以**并集**方式合并远端分组（含空层级），不清除本地已有成员
- 远端 manifest 中的 `collections` 用文件名关联（非 ID），跨设备稳定
- `categories` 以名称树和每个 meme 的 `category_path` 同步，pull 会创建空类别，并把下载/已有原件移动到类别目录；远端图片文件仍保持 `memes/<filename>` 平铺以兼容旧后端。
- `_apply_remote_order` 按远端 manifest 的 `memes` 顺序重排本地 `sort_order`（`reorder_memes`），确保 pull 后本地显示顺序与云端一致，再次 push 不致覆盖云端排序
- `_apply_remote_tags` 以**并集**合并远端标签（只增不清，复用 `add_tags_to_memes`）：读条目内 `tags` 数组，缺失时回退顶层 `tag_map`（旧版安卓清单），均缺失/空列表则跳过不动本地标签；`pull()` 与局域网 `_cmd_push_manifest` 均在 order/collections 之后调用
- `_apply_remote_favorites` 以**并集**合并远端收藏（只增不清，复用 `MemeDB.add_favorite` 幂等 `INSERT OR IGNORE`）：读顶层 `favorite` 文件名数组（键缺失/非 list 直接跳过），经 `_safe_remote_fname` 过滤后按 filename 关联本地表情；`pull()` 与局域网 `_cmd_push_manifest` 均在 tags 之后调用，不受 `manifest_include_favorites` 开关限制

### 排序同步闭环
- 排序相关的 `reorder_memes`/`reorder_collections`/`reorder_collection_members` 更新 DB 后即调 `build_manifest()`，本地 `meme-index.json` 保持最新
- push：末尾 `build_manifest()` 按 DB 当前 `sort_order` 重建并上传，云端 manifest 顺序反映本地排序
- pull：`_apply_remote_order` 按云端 manifest 顺序回写本地 `sort_order`，实现双向闭环

### Manifest
- `build()` 递归遍历嵌套分组树，并保留显式创建的空分组路径以便持久化和跨设备同步
- 远端 manifest 中的 `collections` 以嵌套格式存储（`name`/`filenames`/`children`），version 2 旧格式启动时自动转换
- version 4 额外保存 `categories` 名称树、`category_path` 与本地 `storage_path`；旧 version 3 清单仍可读取，缺省类别按根目录处理。
- 分类标签额外以 `tag_records` 保存名称和种类，批量读取 `get_tag_records_map()`；远端读取优先该字段，旧 `tags` 字符串数组仍保持兼容。增量新增/结构更新只刷新对应条目的标签及顶层收藏，不重读原件；仅标题修改不额外读取数据库元数据，也不改变其他条目。
- 每个 meme 条目含 `tags` 数组（无标签为 `[]`，批量取自 `MemeDB.get_tags_map()` 单查询），受配置 `manifest_include_tags`（默认开）控制，关闭时条目不含 `tags` 键；`JsApi.set_meme_tags`/`batch_add_tags` 变更后即时 `build_manifest()`
- 顶层 `favorite` 数组列出收藏表情的文件名（无收藏为 `[]`，取自 `MemeDB.search(favorite_only=True)`），受配置 `manifest_include_favorites`（默认开）控制，关闭时整个 `favorite` 键不写入；旧清单缺失该字段时不清除本地收藏。

### 自定义排序
- `memes.sort_order` 字段存储全局展示顺序；前端可拖拽排序仅在正 ID 分组/子分组内进行，成员顺序存 `meme_collections.sort_order`
- **分页展示**：`search_memes` 支持 `offset`/`limit`（`MEME_PAGE=200`，前端 index.js 与后端 webui.py 同步维护）；`count_memes`（后端 `count()`/`count_recent()`）统计总数供前端 `renderPager()` 在 `#grid-wrap` 底部渲染翻页条（`<` 上一页/页码窗口含 `…`/`>` 下一页/`>>` 末页）；`refreshMemes()` 重置回第 1 页并重新 count，`goToPage(p)` 按 `offset=(p-1)*MEME_PAGE` 拉取（过期响应用 `memeGen` 丢弃），当前页无数据时回退到可用末页；`memePage`/`memeTotal`/`memePageCount` 维护分页状态；`memes` 数组是已加载子集；`loadMoreMemes()` 保留（含 sort-enter 入场动画）供兼容/测试，主流程不再由滚动触发
- **模型驱动**：`memes` 数组为唯一真源，拖拽跨槽时先 `moveInArray` 同步模型、再挪 DOM 节点（不再以 DOM 顺序回读重建数组）；`initDragReorder()` 在 `#meme-grid` 上绑定一次
- **Pointer Events + 指针捕获**：`pointerdown/pointermove/pointerup`，拖拽激活（位移 >8px）时才 `setPointerCapture`（避免普通点击被捕获重定向）；无 `PointerEvent` 的旧 WebView 自动回退 mouse 事件（`mousemove/mouseup` 挂 document）；`pointercancel`/`blur` 取消并回滚模型 + 重渲染
- **网格感知插入点**：`gridMetrics()` 按首卡片实测宽/高 + `columnGap` 推算 `cols`，`gridSlotIndex(x,y)` 先定位绝对格子（含 folder-card 占位）再映射到非 folder 的 meme 卡数组索引并 clamp（分组内 folder 卡混排时插槽不串位）
- **FLIP 让位动画**：跨槽时对被挤开卡片记录 First/Last rect，invert 后靠 `#meme-grid.drag-active .meme-card` 的 `transition: transform 200ms` 归位，实时显示空位跟随指针
- 落点持久化：前端可拖拽排序仅在正 ID 分组/子分组内调用 `reorder_collection_members(collection_id, id[])` 更新 `meme_collections.sort_order`；`reorder_memes(id[])` 仍用于维护全局 `sort_order`；API 失败回滚 `originalOrder` 并重渲染 + toast
- `canReorderMemes()`: 搜索或标签筛选时禁用；**全局开关 `dragSortEnabled`（标题栏「拖拽排序」图标按钮，位于上传/下载左侧，图标蓝色高亮=开，灰色=关）关闭时禁用排序**；仅正 ID 分组（含子分组）、全部（null）与未分组（-4）视图可排序，收藏夹/最近使用等特殊集合（-2/-3）不可排
- **整理/多选模式拆分（互斥）**：`sortEnabled`（拖拽排序）与 `selectMode`（多选）为两个独立标题栏按钮状态，开启一个自动关闭另一个（`toggleSort`/`toggleSelect` 用 `drag.enable()/drag.disable()` 而非 `drag.toggle()`）。排序模式：drag-select `:click-option-to-select=false`，点击卡不复制不勾选，仅拖拽换位；多选模式：drag-select `:click-option-to-select=true` 支持点选/框选，`#batch-bar`（`v-if="selectMode"`：全选当前页/取消选择/加入分组/打标签/移动到分组（仅正 ID 分组视图显示）/批量删除）显示，`#meme-grid.select-enabled` 生效。批量操作作用于 `selectedIds`：`batchAddToCollection`/`batchMoveToCollection` 复用 CollectionBuilder 选择模式（`cb.openPick(mode, ids, fromId)`，仅显示可搜索分组下拉 + 新建分组，无双栏表情列表）调 JsApi `batch_add_to_collection`/`batch_move_to_collection`（追加/移出+加入语义，`collection_id<=0` 时按 name 创建/复用顶层分组；move 模式下拉排除源分组及其整棵子树，后端同样拒绝后代分组作目标——移入后代等于移出后又加回递归视图；批量移动后原分组移空且无子分组时自动删除）；`batchTag` 用 TagEditor 批量模式（`openBatch()`，不加载单图已有标签）调 JsApi `batch_add_tags` 合并追加。ESC 顺序：右键菜单 → 多选 → 整理 → 隐藏窗口；`handleCopy`/`onCardPointerDown`/`onDocPointerMove` 均以 `sortEnabled || selectMode` 守卫（多选/整理中不复制、不走原生拖拽）
- **排序视觉反馈**：`renderGrid()` 按 `canReorderMemes()` 切换 `sort-enabled`；启用时仅普通 meme 卡（排除 `.folder-card` 和 `.dragging`）最终显示 `scale(0.95)`、3px `var(--border-light)` 描边及 3px 偏移。稳定卡使用独立 `rotate` 属性作轻微快速晃动，且必须排除 `.drag-active`、`.sort-enter`、`.folder-card` 和 `.dragging`；`prefers-reduced-motion: reduce` 时禁用晃动。不得用 `transform` 实现晃动，避免覆盖拖拽和 FLIP 的变换。正在拖拽的卡内联变换固定为最终 `translate(...) scale(0.90)`，与现有透明度、阴影和 FLIP 效果并存，CSS 与内联变换不得叠加。多选模式选中态独立：`#meme-grid.select-enabled .meme-card.selected` 为 `2px solid var(--primary-strong)` outline（与整理模式选中态规则并存，后者仅作用于 `sort-enabled`）。开启工具栏排序开关时沿用现有入场反馈，只有明确关闭该开关才保留当前卡片播放退场动画。搜索、标签、分组或虚拟分组导致的资格变化均按普通刷新处理，不播放退场动画；文件夹卡不显示排序反馈
- **拖拽到外部应用**：关闭拖拽排序后 meme 卡**不用 HTML5 拖拽**（WebView2 http 源的 `text/uri-list`/`DownloadURL` 不生成 CF_HDROP，QQ/微信会报"图片拖拽失败"或资源管理器无反应）；改用 **WinForms 原生文件拖拽**（`native_drag.py`）：`pointerdown` 记录起点 → `pointermove` 位移 >8px 时 `JsApi.start_meme_drag(id)` → 后端用 `webview.windows[0].native`（主 Form）`Invoke` 在 UI 线程执行 `DoDragDrop`（`DataObject` + `DataFormats.FileDrop` → CF_HDROP）→ 拖到微信/桌面等目标是真实本地文件。配置 `napcat_image_send_mode=sticker`（默认）时，任意表情图片（静态图或 GIF）松手到 QQ 顶层窗口都会在 `QueryContinueDrag` 中取消 CF_HDROP 投递，记录落点后复用 `qq_drag.resolve_qq_target` 解析当前会话，再由 `onebot.send_meme_image` 以 OneBot `image` 段的 `sub_type=1` 和 `summary=[表情]` 发送；这样 QQ 只收到一条表情包消息，不会再额外收到普通图片。设为 `image` 时恢复原始文件拖拽，非 QQ 目标始终不拦截。短期旧配置键 `napcat_gif_send_mode` 会迁移到新键。`DoDragDrop` 返回 `DragDropEffects.None`（拖回取消）时返回 cancelled，不触发 `schedule_hide`；`native_drag.py` 懒加载 pythonnet/WinForms，非 Windows或无 .NET 时返回错误，JS 端 toast 提示；**原生拖拽进行中回拖到窗口**用全局 `nativeDragActive` 标志抑制 drop 导入处理器（dragenter/dragover/dragleave/drop 均忽略，视为取消，不弹导入浮层）；`nativeDragActive` 在 `pointermove` 位移 >8px 触发原生拖拽**前**置 true，`start_meme_drag` Promise `.then/.catch` 中重置，拖拽期间（后端 `DoDragDrop` 阻塞 UI 线程）保持 true，确保拖回窗口不会误触发导入
- **QQ 表情与聊天记录拖入**：桥接插件加载后默认常驻启用，向现有及后续新建 BrowserWindow 注入捕获阶段 `pointerdown`、`dragstart`、`pointerup` 与 `pointercancel` 监听器，不依赖 OhMyMeme 设置页开关或限时诊断会话。生产脚本只识别拖拽目标并启动原生文件拖放，不采集通用鼠标元素结构、文本、HTML、URL 形状或属性元数据，不写入专用拖放诊断日志。商城表情只接受 `.market-face-element` 的 `data:image/...;base64`，以文件魔数识别 PNG/GIF/JPEG/WebP/BMP，解码上限 32 MiB；聊天记录只接受带可验证数字消息 ID 的 `.ml-item` 合并转发卡片。聊天记录会先同步生成 `.ommchat` 描述符并启动 `webContents.startDrag`，然后在后台按 `msgId -> peer` 缓存或唯一窗口标题恢复来源会话。描述符只能包含 schema、插件版本和随机 UUID，对应结构只存插件内存，两分钟过期且 `/consume-chat-record-ticket` 消费即删除。OhMyMeme 上传入口只接受单独一个不超过 64 KiB 的 `.ommchat`，消费成功后复用 `archive_chat_record` 完成媒体本地化、数据库幂等保存和库变更通知，只刷新当前视图，不改变当前筛选状态。所有 `executeJavaScript` 动态脚本都要由测试对最终展开字符串执行 `new Function` 语法检查；QQ 插件更新后要求重新执行安装脚本并完全重启 NapCat
- 排序拖拽与原生拖拽共用 `onCardPointerDown`/`onDocPointerMove` pointer 事件：`onCardPointerDown` 按 `sortEnabled && canReorder()` 决定走 `drag.onPointerDown`（排序）还是记录 `nativeDragStart`（原生拖拽），`onDocPointerMove` 按 `drag.dragState.memeId` 是否存在分支，`onDocPointerUp`/`onDocPointerCancel` 对原生拖拽仅清 `nativeDragStart` 跳过排序回滚
- `search()` 带 `collection_id` 时按 `meme_collections.sort_order ASC, m.updated_at DESC` 排序（子查询取该 meme 在目标分组内的 sort_order）
- 拖拽后通过 `ignoreClick` 抑制误触发的 `click`（防止误复制），下一次 `pointerdown` 时重置

### 多级分组与类别路径
- `collections.parent_id` 自引用实现嵌套
- `create_collection(name, parent_id)` / `create_category(name, parent_id)` 接受 `/` 分隔的路径；从根级或指定父节点开始逐级复用同名节点并创建全部缺失层级，不限制嵌套深度，空层级及 `.`/`..` 会拒绝
- 侧边栏「分组」「类别」tab 共用右侧「＋」创建根级路径；节点 hover 的「⋯」分别提供「新建子分组路径」和「新建子类别路径」，输入路径相对当前节点解析。新建结构调用合并的后台 manifest 重建，不在 pywebview RPC 中同步扫描整库
- **统一层级删除策略**：配置 `hierarchy_delete_mode=ask|promote|cascade`，默认 `ask`。主界面询问框可选“保留子级并提升”或“删除全部子级”，勾选“始终使用此方式”后通过 `set_hierarchy_delete_mode` 持久化；设置页可直接切换三态。`MemeDB.delete_category/delete_collection(..., mode)` 必须在单事务内同时处理图片与聊天记录：`promote` 只删除当前节点并提升直属子节点，`cascade` 删除整棵结构；两者都把受影响内容归入父节点，根节点内容则变为未分类/未分组。类别 API 还要同步移动实体原件目录，数据库失败时回滚文件移动。删除成功后前端先按 `deleted_ids/parent_id` 修正激活筛选，再等待对应树刷新并重新搜索，禁止用 toggle setter 制造竞态
- **分组名自然排序回退**：`MemeDB.get_collections`/`get_child_collections` 仅按 `sort_order ASC` 查询后用 Python `_name_sort_key` 稳定排序——`sort_order` 相同（未拖拽）时数字段按整数（1,2,10 而非 1,10,2）、中文按拼音（`pypinyin` 惰性导入，缺失时退回原名小写码点序），替代原 SQL `ORDER BY name` 的二进制码点序
- 顶层分组在 `#colbar` 渲染为 tab，选中后展开子分组
- `#tagbar`/`#colbar` 横向溢出：细滚动条可见（`scrollbar-width: thin` + 5px webkit 样式），`initHScroll(barId)` 把滚轮竖向增量转成 `scrollLeft`（按 `deltaMode` 归一化），`DOMContentLoaded` 时对两个栏各绑定一次
- 节点右键或「⋯」→ 从当前节点新建子路径
- 右键表情包 → 加入分组 → 弹窗列出完整分组树

### 主窗口 UI/UX
- **聊天记录文字拖选**：`ChatRecordDetails` 仅为只读文本元素（标题、来源元数据、消息/引用正文、发言者、时间、嵌套标记和命令文字）设置 scoped `-webkit-user-select: text; user-select: text; cursor: text`，覆盖主窗口 body 的 `user-select:none`；不得放开整页或将按钮、标签选择器和屏蔽 checkbox 一并改为可选文字。Ctrl+C 沿用浏览器默认行为，不写自定义剪贴板或点击复制逻辑，不更改字号/排版；既有 `createBackdropClickGuard` 必须继续允许从正文拖选到遮罩松手而不关闭详情。
- **侧栏名称提示**：`CollectionTreeNode.tree-row` 的原生 `title` 显示完整 `node.name`（类别/分组、递归子节点及折叠态共用）；随机和类别内“未分类”保留原操作说明并在前面加名称。展开态图标不得设置空 `title`，否则会阻断祖先行的完整名称提示；无需查询后端、定时器或自定义浮层，不改变点击、拖动和省略布局。
- **层级拖动**：展开侧栏内的真实类别/普通分组通过 `CollectionTreeNode.node-pointerdown`（递归转发原 pointer event）启动 App 的独立 `hierarchyDrag`，鼠标/笔移动超过 8px 后捕获指针，`elementFromPoint` 仅接受同类型节点；侧栏 header 为恢复顶层目标，类别“全部”也可作为顶层目标。松手先清理捕获和高亮，再经既有 `confirmAsk` 明确确认；取消/Esc/blur、自己/后代/原父级/虚拟目标均不写入。拖后 click 被 capture 守卫消费，节点拖动不触发左右滑动折叠；触摸及折叠栏保留原滑动行为，控件不作为拖源。“隐藏”根不能移动但可接收普通分组。`JsApi.move_hierarchy_node` → `MemeDB.move_hierarchy_node` 在同一 DB 写锁内校验同类真实 ID、目标存在、循环、同级不区分大小写重名及根级媒体保留名，只改根节点 `parent_id`，保留子树 ID/排序/图片与聊天记录关联；目录存储 context 在校验通过后才进入，DB 失败时 rollback 并恢复目录。类别 API 必须先取 `_IMPORT_LOCK` 和 `guarded_read(critical)` 再取 DB 写锁，禁止反序导致缓存/导入死锁；仅 `os.replace` 整目录，不 walk、hash 或复制图片，拒绝覆盖目标、不可用的本地原件目录及子路径符号链接/junction，不调用 resolve。成功后只词法更新已缓存的源子树文件路径，其他缓存保留；聊天媒体根不移动。分组移动不得访问磁盘或限流闸门。清单仅后台请求重建，前端保持导航并等待结构刷新，不整页 reload。
- **Esc 分层路由**：两种详情的 Esc 都 `preventDefault + stopPropagation`，长按重复事件只消费、不继续关闭。详情暴露 `close/isOpen`，`App.onDocKeydown` 在焦点离开详情盒子时仍优先关闭当前详情，不得继续执行 `hideWindow`；已处理、IME/229 和 repeat 事件不进入主页面退出流程。保留内层候选/表单、右键菜单及主页面多选/整理的既有优先级；详情关闭后新一次 Esc 才可隐藏主窗口。
- **子级弹窗拖放隔离**：`App.ignoreImportDrag` 在文档级 dragenter/dragover/dragleave/drop 的 `preventDefault` 和数据读取之前检查可见模态框/子页遮罩，打开时重置导入遮罩与计数并直接返回。普通文本落在可编辑 input/textarea/contenteditable 时不取消浏览器默认行为，以保留文本拖动排序；文件或非编辑区落点只阻止导航，不读取正文、不触发下载/上传/导入提示。弹窗关闭后主页面导入自动恢复，不另行维护易过期的可见性状态。
- **详情键盘编辑**：两种详情在捕获阶段用 `handleDetailSaveShortcut` 处理 Ctrl/Cmd+Enter 与 Ctrl/Cmd+S，阻止同一事件再触发标签 Enter/表单提交。冒泡阶段用 `focusNextDetailInput` 将普通 Enter 移到下一个可见、可编辑、未禁用的输入框（或原生 select）；多行 Shift+Enter、IME composition/229、已被候选列表处理的 Enter 及按钮键盘激活不干扰，最后一项只阻止默认提交、不循环或自动保存。
- **详情遮罩点击保护**：`MemeDetails` 与 `ChatRecordDetails` 使用 `createBackdropClickGuard`，捕获主指针按下/松开并确认起止都在当前遮罩，随后才允许 click 关闭。详情内部拖选到外部松开、遮罩按下但内部松开、指针取消及无匹配起止事件的点击均不关闭；打开/关闭时重置手势，不阻止默认文字选择、不拦截关闭按钮或 Esc。
- **多选设置类别**：多选栏的「设置类别」复用 `CollectionBuilder.openPick` 的 `category` 模式；只读取类别树，不加载图库或分组成员，支持完整路径搜索、`/` 新建层级和 ID `0`“未分类”。`batch_set_meme_category` 先移动所选表情的原件，再以 `MemeDB.set_memes_category` 单事务提交类别；移动或提交失败时逆序回滚文件，不改分组/标签，成功后后台重建清单并由前端等待结构刷新。多选仍只支持本地表情包，不扩展聊天记录或云端内容的选择语义。
- **详情固定操作栏**：`MemeDetails.details-actions` 与 `ChatRecordDetails.chat-management-actions` 是弹窗 flex 列中的非收缩底部区域，位于可滚动内容之外，关闭/保存靠右排列。窄窗口仅滚动内容，不滚动整个弹窗；`MemeDetails.open` 同时将信息栏与窄窗口内容滚动位置复位，切图仍保留当前位置。不得改变原有保存、关闭丢弃和焦点管理行为。
- **统一内容库与体裁**：`useMemes` 通过 `JsApi.search_library` 统一分页，状态分存 `memes` 与 `chatRecords`；`activeGenres` 为 `image|gif|chat_record` 数组，默认三项全选。`#tagbar` 首行「体裁」是不可编辑系统多选筛选（不进入 tags/meme_tags、TagEditor 或 MemeDetails），每项独立勾选，合集语义，空数组表示不显示任何内容；体裁切换不清空分组、类别、关键词或普通标签。`_library_genre_filter` 为查询和后台预热共用规范化规则，同时兼容旧字符串 `all|image|gif|chat_record` 调用；混合分页中聊天记录按更新时间置前，剩余名额再接被选中的图片/GIF。收藏夹/最近使用/随机仍仅含表情包，普通分组与类别支持聊天记录。聊天记录卡点击打开 `ChatRecordDetails`，显示来源/统计/扁平消息预览，并可复制 `/omm send <ID>`；聊天记录参与网格或未选图片/GIF时禁用图片拖拽排序
- **内容变更即时刷新**：`library_events` 用弱引用保存当前 `WebUI` 回调；NapCat 归档、LAN 推送、图片导入以及图片/聊天记录删除完成后发布内容变化事件。`WebUI` 在 120ms 内合并连续通知，把事件类型传给前端 `window.refreshLibrary`，只重新执行当前筛选并回到第一页，不整页 reload、不重新扫描图库。导入和删除路径禁止同步调用 `build_manifest()`：它会逐条 `exists/stat` 整个虚拟盘并使响应及刷新一起等待缓存队列。新增条目调用 `manifest.add_memes()`，删除条目调用 `manifest.remove_memes()`，二者只用 DB 数据非阻塞增量更新本地清单；清单锁忙或清单不可用时调用 `request_background_rebuild()` 合并后台低优先级重建。前端单删、批删与聊天记录删除都必须等待 `refreshLibraryView('structure')`，禁止发出未等待的 `search/refreshCollections/refreshCategories/refreshTags` 竞态请求。禁止使用 `window.location.reload()`，否则 Vue 会被重新挂载并重播只应在真正启动时出现的开屏动画
- **缓存扫描桥接生命周期**：`JsApi.rescan_cache` 只能调用 `WebUI.start_cache_scan()` 启动单例 daemon 任务并立即返回，不能在 pywebview RPC 线程中同步等待 `scan_cache()`。整库扫描可能持续数分钟；若页面在此期间关闭或重建，pywebview 回传结果会命中已被销毁的 `_returnValuesCallbacks` 并抛 `JavascriptException: ... is not a function`。扫描新增条目后由后台任务调用 `notify_changed("structure")` 完成局部刷新
- **表情包详情**：右键菜单「查看表情包详情」或在非整理/非多选模式下长按卡片 500ms 打开 `MemeDetails`；长按期间移动超过 8px 即取消并继续原生拖拽，成功触发后屏蔽该次 click/contextmenu，绝不复制到剪贴板。弹窗使用 `/api/original`（历史路由名，实际返回日常副本）在左侧按原始比例 `object-fit: contain` 大图展示；图片框 hover 显示前后按钮，非输入控件焦点下 ←/→ 按全图库稳定的 `sort_order ASC, id DESC` 顺序切图（详情编辑更新 `updated_at` 时不会跳序），首尾禁用。右侧明确标注并可编辑标题和文件名（文件名同步磁盘原件，禁止改扩展名/路径/保留名并检查冲突）；所属分组显示所有直接归属的完整层级路径，可移除或通过项目风格的搜索下拉框添加已有分组，支持多分组且不移动磁盘文件。搜索名称没有精确命中时，详情可调用 `create_collection`/`create_category` 就地创建任意层级路径并立即选中，也可选择任意既有分组或类别作为父节点继续创建。结构节点创建后立即持久化并刷新侧边栏，但当前表情的分组关系或类别归属仍只在显式保存/切图保存时由 `update_meme_details` 写入。详情也可添加/移除/全局编辑标签，并分别编辑人工/AI 图片描述及人工/AI 图片文字，不适用的一侧可留空以明确来源。已选标签与自绘建议下拉层均提供编辑入口，可同时修改名称和种类，修改影响所有表情，目标标签已存在时自动合并。标签及分组建议样式与项目分组选择器一致（不用系统 `datalist`），支持搜索、鼠标选择及 ↑/↓/Enter。显式保存与切图前保存走 `update_meme_details`，失败则保留当前页面；纯标题变化调用 `manifest.update_meme_name` 原子更新本地清单，不访问原件，只有文件名/类别/分组结构变化或增量更新无法定位条目时才完整重建 manifest。关闭、遮罩点击与 Esc 直接放弃未保存修改。支持 Ctrl/Cmd+S 保存、Tab 焦点循环及关闭后焦点归还
- **设置向导（SetupGuide.vue）**：`loadInitData` 后若 `state.guideOk` 为假（`get_init_data` 的 `guide_ok` = 配置 `config.json` 的 `guide == "ok"`，新装/旧版升级均未标记；`guide_ok()`/`set_guide_ok()` 在 `config.py`，首次读取时自动把旧版清单里的 `guide` 字段迁移到配置并保存）则启动即 `setupGuide.show()`；启动动画遮罩 z-index 2000 更高，动画结束后自然露出。7 步流程 welcome→hotkey→autostart→gif→sync→import→done，每步「下一步」即时 `api('save_settings', {...})`（hotkey/auto_start/auto_play_gif/sync_type），import 步打开主窗口导入菜单（`@open-import-menu`→`showImportMenu`）或跳设置页；**关闭即完成**：任意关闭（ESC/遮罩/×/完成）都调 `api('complete_guide')` 写配置标记，下次启动不再弹。焦点管理复用 `rememberFocus/restoreFocus/trapTabFocus`（z-index 350，低于启动动画 2000、导入菜单 400、高于更新弹窗 300）。**`guide` 标记存 `config.json`（本机配置，`Config.reset()` 恢复默认时保留），绝不写入 `meme-index.json` 清单**——清单会 push 到远端并随 pull 跨机同步，写清单会把向导完成状态带到其他设备且随重建/同步引入不必要的字段维护。重跑入口：设置页「基础设置」→「打开设置向导」（`SettingsApi.open_guide` **同步**执行：限时 5s 逐个 `evaluate_js("window.showGuide&&showGuide();")` → `self._webui.show()`，两步各用 daemon 线程 + `join(timeout)` 兜底，任一步挂起也能返回、不与随后的设置窗口关闭并发；前端成功 toast 后 `closeSettings()` 关闭设置窗，保证主窗口与向导完整可见）
- **悬停放大预览**：卡片 `mouseenter` 起 500ms 定时器（`scheduleHoverPreview`，受配置 `hover_zoom`（默认开）门控——设置页「基础设置 → 悬停预览」区块开关，`get_init_data` 下发首屏状态、`refreshMemes` 时经 `get_settings` 同步，关闭时 `scheduleHoverPreview` 直接返回；排序/多选模式不触发、卡片已卸载不弹），触发时按卡片 rect 计算中心并按预览盒上限 `min(72vw,900)×72vh` 夹紧保证整盒可见 → `.hover-preview` fixed 定位（`transform: translate(-50%,-50%)`，z-index 440，`pointer-events:none`，0.12s 淡入），预览图本地用 `/api/original`（云行回退缩略图）；收起时机：mouseleave / `#grid-wrap` 滚动 / 右键 / 卡片指针按下 / Esc（右键菜单之后的第二优先级）/ `state.memes` 列表刷新（watch）/ 组件卸载；与 GIF 悬停播放（150ms `img.src` 换原图，独立 `hoverTimers`）独立并存
- **标题栏 logo 返回主页**：`#titlebar .logo` 点击 `goHome()`（清空搜索/标签/分组筛选 + `clearSelection` + `search()`），`onTitlebarMouseDown` 排除 `.logo` 不影响窗口拖拽
- **折叠侧边栏分组可辨识**：`CollectionTreeNode.vue` 在折叠态（`collapsed`）以 `.tree-avatar`（26px 圆角块，取分组名首 1-2 字符，`avatarText` computed）替代统一文件夹图标，active 行高亮；展开态保持原图标
- **功能发现性**：meme 卡左上 `.fav-btn` 心形快捷收藏（hover/active/focus-visible 显示，selectMode/sortEnabled 时隐藏，`@click.stop`+`@pointerdown.stop`，调 `JsApi.toggle_favorite` 并本地翻转 `meme.favorited` 后 `refreshCollections`）；侧边栏树行 hover 显示 `.tree-more`「⋯」按钮（展开态，`@click.stop` 发 folder-context 复用右键菜单）
- **空状态**：`#empty` 从 kaomoji 改为 SVG 插画（`.empty-svg`）+「导入表情包」按钮（`showImportMenu`）
- **搜索清除**：搜索框右侧 `.search-clear`「×」按钮（`v-if="state.searchQuery"`，点击 `clearSearch()` 清空并 `search()`），输入框 `padding-right:32px` 防文字被按钮遮挡
- **垂直空间压缩**：titlebar 38px、`#search-wrap` padding 6px 12px、`#search` padding 7px 12px、`#tagbar` padding 4px 12px + max-height 52px、`#breadcrumb` padding 2px 12px、`#grid-wrap` padding 10px、pager padding 5px 10px
- **标题栏统一**：主/设置窗口关闭按钮均为 `×`；拖拽排序图标为上下箭头（非汉堡线）
- **右键子菜单点击展开**：`ContextMenu.vue` 的「加入分组/新建子分组」从 hover 触发改为 click 切换（`onItemClick`，展开时锚定点击项右缘 `getBoundingClientRect().right + 4`，再次点击 `hide-submenu` 收起），保留越界 clamp，`has-submenu` 项显示 `▸` 指示符
- **加入分组任意视图可用**：表情右键「加入分组」不再要求处于分组视图，`onShowSubmenu` 列出全部分组树（顶层直显、子分组带「父/子」路径扁平列表，子菜单仅一层故扁平化）；只要存在可选分组，`ContextMenu` 就在子菜单顶部显示固定搜索框，按完整路径即时过滤，Enter 选择首个结果，Esc 先清空再关闭，选项区独立滚动。顶部保留「新建分组路径/新建子分组路径」（分组视图内相对当前分组创建，其他视图从根级创建），随后把表情加入最终叶节点；文件夹右键和「⋯」直接走 `new-collection-child`，在**当前分组**（`trigger.folderId`）下创建路径
- **CollectionBuilder 分组下拉搜索**：`collectionOptions` 由分组树扁平化（含子分组，label 带「父/子」路径），`filteredCollectionOptions` computed 按 `#cb-name` 输入词过滤（匹配组名或父路径），无匹配显示「无匹配分组」；`createNew` 点击「创建新分组」后保留已输入名称不再清空；`selectCollection` 的 `selectedName` 与输入框显示值保持一致避免 watch 误判手动改名

### 未分组（虚拟分组）
- **`collection_id = -4`** 标识「未分组」虚拟分组：展示未加入任何分组的表情包（`meme_collections` 无记录），**不写入 DB/manifest，动态生成**
- `MemeDB.search()/count()` 新增 `uncategorized_only` 参数（`NOT EXISTS` 于 `meme_collections`），`search_memes` 中 `collection_id == -4` 路由到该参数，同时过滤隐写载体
- `get_init_data`/`get_collections` 始终在分组栏首项生成 `-4`“未分组”，包括零成员时；类别栏固定为 `-1`“全部”后紧接 `-4`“未分类”。旧 `show_uncategorized` 配置保留兼容读取，但不再控制这两个入口，设置页不再提供显示开关
- Vue 前端走 `CollectionTreeNode` 通用渲染路径，零计数也保留入口；未分组视图可拖拽排序（复用全局 `sort_order`，走 `reorder_memes` 持久化，`canReorder` 对 -4 返回 true）
- 未分组集合内的删除/加入分组等操作经 `refreshCollections` 后计数自动刷新，全部归类后仍显示零计数的“未分组”入口

### 随机（虚拟分组）
- `collection_id=-5` 为固定、不可重命名或删除的虚拟入口，不写入 DB/manifest，不参与分组归属、子分组创建或拖拽排序；仅展示图片/GIF，不含聊天记录
- `JsApi._get_random_meme_ids` 在首次查询或显式 `search_library(..., refresh_random=True)` 时用 SQLite 随机抽样最多 `MEME_PAGE=200` 张；ID 和随机顺序只保存在本次应用会话内。普通查询、侧栏刷新及离开再返回均复用该批 ID，元数据实时查询，已删除/隐藏的成员不自动补位
- 前端仅在侧栏重复点击当前 `-5` 时传递重抽标记；首次进入或返回只显示缓存批次。进入/重抽时清除关键词、标签、类别、体裁筛选；随机页内仍可筛选当前批次。抽样和后续查询均遵循隐藏分组开关

### 最近使用
- `recent_uses` 表：`meme_id` + `used_at`
- `copy_meme` 时自动 `record_use`（`INSERT OR REPLACE`）
- `get_init_data` 中 `collection_id = -3` 标识最近使用，`search_memes` 路由到 `get_recent()`
- 前端复制后自动刷新最近使用列表
- 右键「最近使用」分组 → 「清空最近使用」菜单项（`clear_recent` 清空全表）；右键列表内表情 → 「从最近使用中删除」（`remove_from_recent`）

### QQ 表情包导入 (adb_util.py)
- **入口**: `start_qq_import()` — 后台线程执行完整流程
- **流程**: 检测/下载 ADB → `adb start-server` → 轮询 `adb devices` 等待设备（最多 300s） → `adb pull` 拉取 `QQ_Favorite` 目录 → 魔数识别扩展名 → ZIP 打包到临时目录
- **路径回退** (`_find_qq_favorite_dir`): 后缀固定为 `Android/data/com.tencent.mobileqq/Tencent/QQ_Favorite`，依次尝试主存储 `/storage/emulated/0`、`/sdcard`，再枚举 `/storage/` 下其他卷（TF 卡通常挂载 `/storage/XXXX-XXXX`），首个 `ls` 命中的即拉取（不做多卡场景）
- **魔数识别** (`_detect_ext`): 支持 PNG (`\x89PNG`), JPEG (`\xff\xd8`), GIF (`GIF87a`/`GIF89a`), WebP (`RIFF`+`WEBP`), BMP (`BM`)
- **ADB 下载** (`_download_with_progress`): 从 googledownloads.cn （国内同步镜像源） 下载 platform-tools ZIP，解压到 `.adb/platform-tools/`，更新 `dl_progress` 供前端显示下载百分比
- **进度状态** (`_QQ_STATE`): `idle` → `downloading_adb` → `starting_adb` → `waiting_device` → `pulling` → `processing` → `done`/`error`，前端 300ms 轮询 `get_qq_import_progress()`
- **保存**: `save_qq_zip()` 通过系统另存为对话框保存 ZIP 到用户位置
- **前端 UI**: 设置页「导入」分组下 `.import-row` 列表行（硬编码 SVG 图标 + 名称），点击手机版 QQ 行直接开始导入并弹进度覆盖层；其余导入来源点击先弹配置对话框再开始
- `.adb/` 文件夹同时供 ADB 检测和 QQ 导入共用

### Notion 数据库导入 (notion_import.py)
- **定位**: 只读导入，不向 Notion 写入或删除数据；主窗口「导入表情包 → 从 Notion 数据库导入」弹窗填写 Internal Integration Token、数据库 URL/ID、可选 data source ID 及属性映射
- **API**: `Notion-Version: 2026-03-11`；数据库只有一个 data source 时由 `GET /databases/{id}` 自动解析，页面通过 `POST /data_sources/{id}/query` 分页读取；429/5xx 与网络错误有限重试
- **映射**: 必填 Files & media 图片属性；Title 可留空自动识别；`notion_tag_mappings` 是可增删的 `{category, property}` 列表，每个 Multi-select/Select/Status/Rich text/Relation 属性独立映射为一个本地标签分类，分类留空时取属性标题；旧 `notion_tags_property` 自动兼容为同名分类。Select/Status/Multi-select/Relation 仍可映射到「Notion」顶层分组下的子分组。Relation 跨全部标签列与分组列去重收集 page id，经 `GET /pages/{id}` 读取关联页面 Title 并缓存为名称
- **媒体落地与增量跳过**: 首次下载后走 WebUI `_do_import` 的大小/分辨率限制、魔数识别、SHA-256 去重和本地缓存，并把 data source/page/property/file index+name/`last_edited_time` → `meme_id` 写入 `notion_sources`。再次导入时仅当页面版本一致且本地缓存文件仍存在，才在下载前跳过；页面变化、映射缺失或本地文件缺失时重新下载并以 SHA-256 兜底。旧数据需再下载一次建立映射；外部 URL 内容可原地变化，始终下载校验
- **来源安全**: 不保存约一小时失效的 Notion 签名 URL；来源映射随 SQLite 本地备份保存，但不写入远程同步 manifest
- **下载安全**: 仅允许公网 HTTPS，初始 URL及每次重定向均做 DNS/IP 校验并拒绝私网、回环、链路本地和保留地址；流式下载原件硬限制 200 MiB，只接受 PNG/JPG/GIF/WebP/BMP 魔数
- **进度与取消**: `start_notion_import` 后台线程 + `_NOTION_STATE`，主窗口 `NotionImportDialog` 350ms 轮询；取消在分页之间、逐文件之间和下载分块间检查
- **主窗口弹窗**: `ImportMenu` 提供入口，`NotionImportDialog.vue` 提供连接测试、属性自动识别、动态增删标签列、开始/取消与内联进度；Token 通过 Config 加密保存

### QQNT 提取 (qqnt_extract.py)
- **来源**: 改编自 GPL-3.0 项目 QQFavoriteExtract (main_gui.py)，**GPL-3.0 合规**：该模块按 GPL-3.0 分发（头部含原作者署名与协议链接），引入后整体作品再分发需按 GPL-3.0 处理
- **表情目录**: `<UserDataSavePath>/<QQ号>/nt_qq/nt_data/Emoji/personal_emoji/Ori`，`UserDataSavePath` 从 `C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini` 的 `[UserDataSet]` 段读取
- **编码自适应**: `read_file_with_correct_encoding` 按候选编码严格解码，需命中 `[UserDataSet]` 且内容含中文或全 ASCII（`is_content_valid`，注意 `\u4e000` 恒 False 的坑，应为 `\u4e00`）
- **昵称** (`get_user_nickname`): `uapis.cn` API + `%APPDATA%/OhMyMeme/nickname_cache.json` 本地缓存 1 小时；用 `urllib`（无 requests 依赖），失败返回空串
- **复制** (`copy_directory_with_progress`): `os.walk` + `shutil.copy2`，**逐文件容错**（失败跳过继续并 `on_error(src,msg)`，清理半成品），进度走 `on_progress(done,total,src,dst)`/日志走 `on_log(msg)`；返回 `{total,copied,failed,skipped}`；`image_only=True` 时仅复制魔数可识别的图片
- **扩展名修正**: 纯魔数检测（`FILE_SIGNATURES`，兼容无扩展名/错误扩展名），webp 需校验 `RIFF` + `header[8:12]==b'WEBP'`；冲突名跳过不覆盖；返回 `{total,renamed,unrecognized}`
- **无弹窗/sys.exit**: 失败抛异常（`RuntimeError`/`FileNotFoundError`/`FileExistsError`）或返回统计 dict；输出目录已存在且非空抛 `FileExistsError`，`overwrite=True` 才清空后写入；输出目录与源表情目录相同抛 `ValueError` 防误删
- **入口**: `extract_qq_emojis(qq_number, output_dir, ...)`（新增 `image_only`/`overwrite`/`should_stop`）；环境探测 `get_extract_status()` 区分 `config`/`path_missing`/空账号三态；辅助 `get_available_qq_numbers()`/`get_default_output_dir()`
- **GUI 集成**: `webui.py` 的 `_QQNT_STATE`/`_qqnt_worker` 后台驱动 + `SettingsApi.qqnt_*` 方法（`qqnt_check_env`/`qqnt_pick_ini`/`qqnt_pick_userdata`/`qqnt_pick_base`/`qqnt_start`/`qqnt_get_progress`/`qqnt_cancel`/`qqnt_open_dir`）；设置页「电脑版 QQ（QQNT）」`.import-row` 点击开向导（环境/选账号 → 输出位置 → 进度 → 汇总），300ms 轮询 `qqnt_get_progress`；手动选择的 INI/用户数据目录持久化到 `config.json` 的 `qqnt_ini_path`/`qqnt_userdata_path`；`should_stop` 实现取消。**手动重定向始终可见**：`qqntRenderEnv` 在探测成功时也显示「选择配置文件/选择用户数据目录」按钮（`userdata_save_path` 传入时完全覆盖 INI 推导路径），应对多用户 Windows 下 `UserDataInfo.ini` 只记录第一个用户路径的场景

### Telegram 缓存导入 (tg_stickers.py)
- **入口**: `start_tg_import(webui, tdata_path, passcode, convert_webm)` — 后台线程执行完整流程；`tdata_path` 为空时回退到配置 `tg_tdata_path`，再自动检测
- **tdata 路径检测** (`find_tdata_path`): 跨平台回退链 — Windows `%APPDATA%\Telegram Desktop\tdata` → macOS `~/Library/Application Support/Telegram Desktop/tdata` → Linux `~/.local/share/TelegramDesktop/tdata` + Snap/Flatpak 变体；未找到则报 `error_code="no_tdata"` 引导手动指定
- **手动指定目录**: 设置页「手动指定 tdata 目录」按钮（`SettingsApi.pick_tg_tdata`）弹文件夹对话框，`is_valid_tdata()` 校验含 `key_datas`/`key_data`，通过后持久化到 config 键 `tg_tdata_path`（下次启动预填显示）；导入失败弹窗内 `error_code` 为 `no_tdata`/`invalid_tdata`/`no_cache` 时显示「手动选择 tdata 目录」重试按钮
- **解密机制**: Telegram Desktop 缓存使用 AES-IGE（TDF$ 文件）和 AES-CTR（TDEF 文件）加密，本地密钥从 `tdata/key_datas` 读取，通过 PBKDF2-HMAC-SHA512 派生（有本地密码时 100k 迭代，无密码时 1 次）；`bad_key` 错误提示本地密码场景
- **解密流程**: `read_local_key()` 读取密钥 → 遍历 `user_data/cache` + `user_data/media_cache` → `decrypt_tdf_file()`/`decrypt_tdef_file()` 按魔数识别格式解密 → `detect_extension()` 通过文件头识别扩展名 → 仅保留 webp/webm
- **webm 转换** (`convert_webm_to_webp`): 默认开启，ffmpeg 将 webm 转 animated webp，**有损 q80**（`-lossless 0 -quality 80`，无损编码实测 11-35s/个过慢改用有损，贴纸场景质量几乎无损），`-loop 1` 循环播放，保持宽高比（最长边 512，不放大），删除原 webm；转换前 `_check_ffmpeg()` 预检（`_resolve_ffmpeg()`：打包内置 `_MEIPASS/tools/ffmpeg/ffmpeg.exe` → PATH 系统 ffmpeg → None，命中即缓存到 `_FFMPEG_PATH`），缺失时报 `error_code="no_ffmpeg"` 中止；命令由 `_webm_cmd(ffmpeg, webm, out)` 构建（与打包校验 `build.py --verify-ffmpeg` 共用一份参数，防两处漂移）；**单个转换失败的文件跳过不导入**（`convert_failed` 计数并在完成消息提示）。**并行转换**：`_tg_worker` 内用 `ThreadPoolExecutor(max_workers=min(os.cpu_count(), 4))` 受控并发，实测单张 ~1.7s → 4 路约 2.9x 加速（千张 30 分 → ~11 分）；进度 `done/convert_failed` 在 `_TG_LOCK` 内原子累加，非 webm（webp）直接透传并计入进度；进度条按 `done/total`（total=全部待处理含 webp）平滑递增。**取消/回收**：`convert_webm_to_webp` 用 `Popen`+有界 `communicate(timeout)`，受 `_TG_ACTIVE_PROC` 集合（`_TG_LOCK` 保护）跟踪活动进程；`cancel_tg_import()` / `_reset_state()` 对所有运行中进程 `terminate()`；`_reap_proc()` 统一回收——kill 后无条件 `wait`（纠 zombie），wait 超时二次 SIGKILL 兜底，**仅进程实际退出（`poll()` 非 None）才从 `_TG_ACTIVE_PROC` 移除**；转换循环 `finally` 用 `executor.shutdown(wait=True, cancel_futures=True)` 等待已启动 future 退出，避免与 temp_dir 清理/下次导入交错。**ETA 用 `time.monotonic()`**（`_TG_T0` 起点与计算同单调时钟）。**防双 worker**：`start_tg_import` 在 `_TG_LOCK` 内检查后立即 `_update_tg(status="scanning")` 占位运行态，线程在锁外创建，并发第二次调用在锁内见 scanning 即返回 False
- **静态版去重** (`dedup_static_against_animated`): Telegram 对同一动态贴纸缓存两份（512 webm 动画 + 320 webp 静态版），入库前用 PIL 识别动画 webp（`n_frames>1`），将每个静态 webp 与所有动画 webp 首帧做**归一化灰度差分**（白底合成 32×32，阈值 diff<0.02，实测匹配组 0.002-0.005 / 非匹配组 >0.1 间隔安全），内容一致的静态版跳过只保留动画版；无动画或 PIL 缺失时原样返回；`.webm` 文件（未转换时）不受影响。实测 127 静态中 45 个被判重跳过，0 误杀 0 漏跳，全量比较耗时 ~2s
- **进度状态** (`_TG_STATE`): `idle` → `scanning` → `loading_key` → `decrypting` → `converting` → `importing` → `done`/`error`/`cancelled`，含 `error_code` 字段，前端 300ms 轮询 `get_tg_import_progress()`。**ETA 展示**：`_TG_STATE["elapsed_s"]` 由 worker 起点 `_TG_T0` 计算，`_refresh_tg_elapsed()` 在 `_update_tg`/`get_tg_progress` 内按运行中状态推进（idle/结束不推进）；前端 `legacy.js` 的 `updateTgEta()` 据 `elapsed_s` + `progress` 线性外推「已用 X · 预计剩余 Y」（`formatDuration()` 折算分钟/秒），`tg-import-eta` 元素随轮询刷新、overlay 打开时清空
- **入库**: 解密到临时目录后调 `webui._do_import()` 入库，完成后自动清理临时文件
- **取消**: `cancel_tg_import()` 设置标志位，工作线程在每个阶段检查并中止
- **前端 UI**: 设置页「导入」分组下 `.import-row` 列表行（硬编码 SVG 图标 + 名称），点击对应行弹出该软件的导入对话框（Telegram：tdata 目录手动指定 + 本地密码 + WebM 转换开关 → 进度覆盖层，错误时 `no_tdata`/`invalid_tdata`/`no_cache` 显示「手动选择 tdata 目录」重试按钮）
- **多账号**: Telegram Desktop 多账号共享 `user_data/cache`，无法区分来源账号，统一提取
- **透明动画（已解决）**: Telegram 视频贴纸 webm 内含有效 VP9+alpha（`yuva420p`）数据，但 ffmpeg **原生 VP9 解码器会静默丢弃 alpha 平面**（解码结果全不透明），导致转换出的动画 webp 背景不透明。修复：`convert_webm_to_webp` 在 `-i` 前加 `-c:v libvpx-vp9` 强制使用 libvpx 解码器保留 alpha。实测 48 个 webm 中 47 个恢复透明，透明像素分布与同表情静态 webp 完全一致

### 抖音表情包导入 (douyin.py + abogus.py)
- **架构**: 纯协议驱动（无浏览器自动化），`src/abogus.py` 提供 ABogus 签名算法绕过抖音 WAF，`curl_cffi` 模拟 Chrome 124 TLS 指纹绕过 JA3/JA4 检测
- **入口**: `start_douyin_import(webui, cookie)` — 后台线程执行完整流程，下载全部表情包
- **签名算法** (`abogus.py`): 纯 Python 实现，源自 GPL-3.0 项目 TikTokDownloader。流程：参数 SM3 哈希 → 与 UA 指纹/浏览器指纹/时间戳拼接 → RC4 加密 → 自定义 Base64 编码表输出。`gmssl.sm3` 做国密哈希
- **TLS 指纹绕过**: `curl_cffi.requests.Session(impersonate="chrome124")` 模拟 Chrome 124 的 JA3/JA4/H2 指纹，WAF 视为合法浏览器
- **Cookie 认证**: 用户从浏览器复制完整 Cookie 字符串 → 解析 key=value 注入 Session。额外自动预置基础 Cookie（ttwid、verifyFp、s_v_web_id、msToken）无需登录也可获取部分接口数据
- **API**: `GET /aweme/v1/web/im/resource/list/aggregation` 分页拉取自定义表情列表，参数 `scenes=CUSTOM_STICKER_PAGE`，每页 100 个
- **URL 签名**: 每个请求需附加 `a_bogus` 参数，由 ABogus 算法对 URL 参数 + HTTP 方法 + 浏览器指纹计算得出
- **下载**: 优先取 `animate_url.url_list`（动图），回退 `static_url`，`curl_cffi` 保持 `impersonate="chrome124"` 下载
- **入库**: 下载为临时文件 → 调 `webui._do_import()` 哈希去重入库 → 原始 WebP 格式保存（不转 GIF，保留最佳画质和最小体积）
- **进度状态** (`_DOUYIN_STATE`): `idle` → `running`（含 message/progress/done/total）→ `done`/`error`/`cancelled`，前端 300ms 轮询 `get_douyin_import_progress()`
- **取消**: `cancel_douyin_import()` 设置标志位，工作线程检查后中止
- **错误码**: `login_failed`（Cookie 无效）、`sign_failed`（403 签名失败）、`no_stickers`（无表情数据）
- **前端 UI**: 设置页「导入」分组下 `.import-row` 列表行（硬编码 SVG 图标 + 名称），点击抖音行弹出对话框（Cookie 输入框 + 下载按钮 → 进度覆盖层），下载全部表情
- **GPL-3.0 合规**: `abogus.py` 按 GPL-3.0 分发（头部含原作者署名与协议链接），整体作品再分发需按 GPL-3.0 处理

### 微信导入 (wechat_probe.py + wechat_keyfinder)
- **架构**: 独立 C++ 二进制 `wechat_keyfinder` 处理 Windows 进程内存取证（**只读**读取微信进程内存提取密钥），Python 侧通过 subprocess + JSON 协议协调完成 DB 解密/SQLite 查询/CDN 下载/入库；仅 Windows
- **随包分发（防误报关键）**: helper 由 `build.py` 的 `build_keyfinder_helper()` 在打包前用 cmake+MSVC 编译（**每次都重新配置并构建 Release**，不复用旧 exe、不按 mtime 挑产物——否则可能把 Debug 或带 `WKF_ENABLE_TEST_KEY` 的开发产物打进包；产物只认确定的 `<build>/Release/` 路径，缺失即中止；**cmake 缺失或编译失败默认 `sys.exit(1)` 中止打包**，避免静默产出无微信导入的安装包，仅本地开发可用 `--allow-missing-keyfinder` 放行），经 `--add-binary` 打进 `_internal/src/wechat_keyfinder/`；`wechat_probe` 的 `_bundled_binary_path()`/`_offsets_path()` 优先从 `sys._MEIPASS` 解析（开发态回退源码目录）。**不再运行时从 GitHub 下载 exe**（旧 `_WECHAT_KEYFINDER_URLS`/`_download_task`/`_get_wechat_dir` 已移除）——「未签名 exe + 运行时公网下载 + 读进程内存」是被 Defender 重点标记的组合
- **SHA-256 构建期注入**: MSVC 构建非确定性（嵌入时间戳），故 `build.py` 的 `pin_keyfinder_hash()` 按**实际产物**计算哈希并改写 `_WECHAT_KEYFINDER_SHA256`，PyInstaller 打包后由 `unpin_keyfinder_hash()` 还原源文件（与 `set_version` 同模式，`finally` 保证还原；正则未命中即抛错，避免产物带过期哈希导致运行期必失败）。校验意义在于检测**安装后被篡改**，而非绑定某个特定构建
- **去特征化**: 已删除 `scan_memory_for_urls` 快照扫描死代码（生产恒传 `--no-snapshot` 从不执行，但其 `kNonStoreEmoticonTable`/`md5 IN(`/`vweixinf.tc.qq.com` 字符串是最强静态信号）及 `scan_overlap` 配置项；`--key` 测试分支改为编译期开关 `WKF_ENABLE_TEST_KEY`（默认 OFF，发布产物不含）；`.rc` 提供 CompanyName/ProductName/FileDescription 版本资源（消除「无签名+零元数据」特征）
- **无 OpenSSL 依赖**: 仅需 PBKDF2-HMAC-SHA512 与 HMAC-SHA512 两个原语，源码内自包含实现（`namespace sha512`）替代 OpenSSL，避免静态 libcrypto 的体积膨胀（产物体积 4.07MB → 92KB）与额外静态特征面。**回归保护 `src/wechat_keyfinder/test_wechat_keyfinder.cpp`**（cmake 选项 `WKF_BUILD_TESTS`，默认 OFF 使打包构建不连带编译；CI `check.yml` 的 `keyfinder-tests` job 在 windows-latest 上 `-DWKF_BUILD_TESTS=ON` 跑 ctest）：23 个密码学已知答案向量（SHA-512 覆盖块边界 0/1/55/56/111/112/113/127/128/129/255/256/1000 + 流式分块一致性；HMAC 覆盖 4/20/128/131B key 含 block 边界与超块长分支；PBKDF2 覆盖 iter 1/2/4096 与真实调用形态 32B 口令/16B salt/32B 输出）+ 6 个密钥提取端到端断言（掩码恢复、旧 RVA+MAC 校验、篡改 DB 页 MAC 被拒、**篡改密钥缓冲被 MAC 拒**、还原后恢复）。期望值全部由 Python hashlib/hmac 生成内联，避免手写或由被测实现自产。测试产物写系统临时目录，不在仓库留残留
- **目录层级**: 微信文件目录（root，默认 `%USERPROFILE%\Documents\xwechat_files` 或 `\WeChat Files`）→ 账号目录（root 下 `wxid_*` 文件夹，每个微信账号一个）→ `db_storage/emoticon/emoticon.db`（表情库，加密）+ `db_storage/favorite/favorite.db`（收藏库）
- **环境检测** (`inspect_wechat_environment`): 传入路径 basename 以 `wxid_` 开头或 `_find_emoticon_db` 命中则视为单账号，否则扫描子目录收集 `wxid_*` 前缀或含表情库的账号目录；每账号 `_inspect_account` 检查 DB 是否存在且为 SQLite header（否则 `encrypted_index`）；返回 `{status, reason, root, root_exists, account_directory_count, accounts: [{id, path, status, reason, db_path}]}`
- **账号选择**: `_pick_account` 未指定且多账号时返回 None，调用方报 `multiple_accounts` 引导前端选择；`list_wechat_stickers`/`start_wechat_import`/`_wechat_worker` 支持 `account_path` 参数指定账号
- **密钥提取**: 二进制扫描微信进程内存，通过特征码定位密钥对象（RVA 偏移在 `config/offsets.json` 配置），XOR 解码 + salt 比对 + HMAC-SHA512 校验。`offsets.json` 由 `build.py` `--add-data` 打包进产物 `config/` 目录（冻结后 `_offsets_path` 解析到 `_internal/config/offsets.json`，缺失时 helper 报 `config_invalid`）。**掩码恢复为主路径**（`find_wechat_key_masked`）：利用 DB 前 16 字节 salt 反推 32 字节 XOR 掩码，按 `x'<96hex>'` 格式识别被掩码的 99 字节密钥缓冲，**无需 RVA**，微信升级不易失效；旧 RVA 特征码扫描仅作回退；`--key <hex64>` 仅存在于测试构建（`cmake -DWKF_ENABLE_TEST_KEY=ON`，默认 OFF），发布产物不含该参数分支，用途是注入已验证密钥绕开取证做手工验证。**多进程**：未指定 `--pid` 时枚举所有 `Weixin.exe` 逐个尝试，掩码恢复天然命中运行目标账号的进程（`key_not_found` 表示均未命中）
- **账号目录识别不依赖 `wxid_` 前缀**：`_find_account_dirs` 收集「`wxid_` 前缀子目录 ∪ `_find_emoticon_db` 命中（`db_storage/emoticon/emoticon.db` 或 `Msg/emoticon.db` 存在）的子目录」；`_find_emoticon_db` 两个固定候选未命中时在 `db_storage` 一层内兜底查 `emoticon.db`（`db_storage/emoticon.db` 或 `db_storage/*/emoticon.db`，覆盖布局差异）；`inspect_wechat_environment` 对用户所选目录本身同理放宽（前缀或含表情库即视为单账号目录）。`_inspect_account` 在 db 缺失时附带 `db_files`（`db_storage` 内实际存在的 `.db` 清单，有界 30 条），前端 `no_database` 时翻译为中文提示并展示该清单用于诊断真实布局；检测到微信 3.x 旧版布局（账号目录含 `Msg/Multi` 或 `Msg/MicroMsg.db`，表情库为 `Msg/Emotion.db`）时返回 `unsupported_version`/`wechat_3x_unsupported`，前端引导升级微信 4.x（Emotion.db 的 `CustomEmotion` 表、3.x SQLCipher 页布局与密钥内存格式均与 4.x 不同，整条链路不支持）。测试 `tests/test_wechat_env.py`
- **DB 解密** (`_decrypt_database`): AES-256-CBC 逐页解密（每页 4096 字节，页 1 带 16 字节偏移，IV 取页尾 80 字节偏移处），首页替换为 "SQLite format 3" header；**合并 WAL**（`_apply_wal`）：微信运行中表结构与记录在 `emoticon.db-wal` 里，按 WAL 帧（24B 头 + 4096B 加密页）解密并回写到对应页，主文件旧快照 + WAL 帧 = 完整数据
- **元数据查询** (`_query_sticker_metadata`): SQLite 查询 `kNonStoreEmoticonTable`（type/md5/aes_key/cdn_url/encrypt_url/extern_url），返回 md5+url+aes_key 列表
- **下载校验** (`_download_sticker`): urllib 下载 → 超限拒绝 → **明文优先**（`_detect_image_ext` 命中即原样返回）→ 非明文且带 `aes_key` 时才 AES-128-CBC 解密（IV=key）→ 再次魔数校验。**明文必须优先判定**：`cdn_url`（`/20401/`）恒定返回明文图片，旧实现先按 `aes_key` 存在且 `len(data) % 16 == 0` 无条件解密，会把明文解坏后判为非法而静默丢弃——实测全量 328 个里恰好丢 20 个（≈1/16，与概率吻合）；长度非 16 倍数者因跳过解密分支而侥幸成功，掩盖了该 bug。回归测试见 `tests/test_wechat_env.py` 的 `test_plaintext_16_multiple_survives_with_aes_key`（对旧逻辑必然失败）。**防 SSRF**：仅允许白名单 CDN 主机（`vweixinf.tc.qq.com`/`wxapp.tc.qq.com`），解析后拒绝回环/私网/链路本地地址，重定向逐目标复检
- **完整性校验**: **仅发布态（`sys.frozen`）比对固定哈希**——源码运行用的是本地自编译产物，其哈希与随包固定值必然不同（MSVC 构建非确定性），且该哈希描述的是打包件而非工作副本，故开发态跳过比对（否则开发者自行编译后反而无法使用微信导入）；发布态哈希不匹配或未配置时**拒绝执行**（`ensure_wechat_keyfinder` 返回空串，前端报 `no_binary`）。哈希由构建期自动注入，无需手工 `certutil`（见上「SHA-256 构建期注入」）
- **源码运行按需构建** (`_ensure_dev_helper`): 开发态缺 helper 时自动 `cmake` 构建一次并拷回源码目录（与 `main._ensure_vue_frontend` 同思路），保证新克隆仓库执行 `python -m src` 即可用微信导入；**pytest 下跳过**（沿用 `hotkey.py` 的 `PYTEST_CURRENT_TEST` 守卫），构建失败仅告警不阻断启动
- **前端 UI**: 设置页「导入」分组下 `.import-row` 列表行（硬编码 SVG 图标 + 名称），点击微信行弹出对话框（目录选择 + 环境检测 + 多账号下拉 → 进度覆盖层）；对话框标题下常驻用户协议警告框（红字：「该功能可能不符合微信用户协议，请谨慎使用！」）

## 数据与配置路径 (config.py)
- **config_dir**（`_get_config_dir`）: Windows `%APPDATA%/OhMyMeme`；macOS `~/Library/Application Support/OhMyMeme`；Linux `$XDG_CONFIG_HOME/OhMyMeme`（默认 `~/.config/OhMyMeme`）
- **data_dir**（`_get_data_dir`）: Windows `%LOCALAPPDATA%/OhMyMeme`；macOS `~/Library/Caches/OhMyMeme`；Linux `$XDG_DATA_HOME/OhMyMeme`（默认 `~/.local/share/OhMyMeme`）
- config_dir 内容: `config.json`（唯一用户配置）、`env_check.json`（环境检测标记）、`plugins/<插件id>/settings.json`（插件设置）、`nickname_cache.json`（QQNT 昵称缓存）
- data_dir 内容: `memes.db`、`cache/`（原图，可经 `cache_dir` 配置重定向）、`thumbnails/`、`meme-index.json`（同步清单）、`cloud-index.json`（云端清单缓存）、`hotkey.log`（热键事件日志）、`plugins/`（插件代码）、`plugins_data/<id>/`（插件私有数据）、`plugins_state.json`（启停状态）、`backups/`（备份目录，可经 `backup_dir` 重定向）、`storage_migration.json`（存储迁移清单，完成后删除）

## 构建 & 测试
```bash
pip install -r requirements.txt
npm install                 # Vue 前端依赖（开发时）
npx vite build              # 构建 Vue 前端 → webui/dist/ohmymeme.js
python -m src     # 开发运行
python -m pytest tests/ -v  # 运行测试
ruff check src/   # lint 检查
black src/        # 格式化
python scripts/build.py  # PyInstaller + InnoSetup 完整构建
python scripts/build.py --installer-only --msix  # 追加 MSIX（Windows）
python scripts/build.py --lang en  # 指定语言构建
```

### CLI 参数 (main.py argparse)
- `--debug` — 输出所有 DEBUG 级别日志（控制台级别由 INFO 提到 DEBUG）
- `--silent` — 启动时最小化到托盘；源码模式下需显式传入（忽略配置文件的 `silent_start`）
- `--debug-update` — 强制弹出更新对话框（测试用）
- `--debug-startup` — 输出开机自启检测详情（注册表键、启动文件夹）
- `--debug-adb` — 输出 ADB 检测详情及运行时日志（路径、版本、adb 命令）
- `--debug-env` — 输出环境检测详情（WebView2/.NET）并强制打开检测窗口
- `--env-check-ui` — 内部旗标（`argparse.SUPPRESS`，不显示在 help），独立运行环境检测 UI 子进程入口，在单实例检查之前执行

### 构建脚本选项 (scripts/build.py, PyInstaller 6.0+)
- `--windows`/`--linux`/`--macos` — 目标平台；**PyInstaller 不支持交叉编译**，各参数只能在对应系统上使用
- `--macos --arch arm64|x86_64` — 指定 macOS 架构（默认按机器自动检测）；产出 `.app` + `.dmg`
- `--linux --package all|appimage|deb|rpm` — Linux 包类型（默认 all）；构建机需 GTK/WebKit 依赖
- `--build-only` — 仅 PyInstaller 打包，跳过安装包
- `--installer-only` — 仅制作安装包（PyInstaller 已打包完时）
- `--nightly`/`--version X` — 改写版本号构建（临时改写 `src/__init__.py`，完成后恢复）
- `--verify-ffmpeg`/`--verify-helper` — 校验产物内置 ffmpeg/wechat_keyfinder（CI 打包后调用）
- `--msix` — Windows 追加 MSIX 格式（**additive**：`.exe` 始终构建；工具/清单缺失仅告警跳过，不中断）。`build_windows_installers(app_version, filename_version, want_msix)` 统一编排，`--installer-only` 与常规打包流程共用
- Windows 安装包需 InnoSetup 6/7；输出目录 `dist/`；原 Nuitka 构建脚本归档于 `scripts/nuitka/build.py`

### Windows MSIX 打包（Microsoft Store）
- **触发**：`--msix`（三个 workflow 的 windows job 均以 `--installer-only --msix` 调用；nightly 额外带 `--nightly`）
- **工具定位**（缺失仅告警跳过，不中断）：`find_makeappx()` = `MAKEAPPX_DIR`/`MAKEAPPX` → 扫描 `C:\Program Files (x86)\Windows Kits\10\bin\<ver>\x64\makeappx.exe` 取最高版本（runner 预装 Windows SDK）
- **MSIX**（`build_msix`）：`scripts/installer/msix/AppxManifest.xml` 为模板，**已填入本项目 Store 身份**（`Name=TNTXZ.OhMyMeme` / `Publisher=CN=4215BB25-0E28-400F-94EF-AF09583E9BCF` / `PublisherDisplayName=TNTXZ`；公开标识，非密钥，可安全入库）。`build_msix` 用正则取 `<Identity>` 标签内 `Name`/`Publisher`，env `OHMYMEME_MSIX_IDENTITY_NAME`/`OHMYMEME_MSIX_PUBLISHER`/`OHMYMEME_MSIX_PUBLISHER_DISPLAY_NAME` 覆盖后整体重写标签（值仍含 `REPLACE-WITH-STORE` 占位串时告警）；`Version` 由 `_msix_four_part` 转四段式 `X.Y.Z.0`（Revision 必须 0）；`_msix_assets` 由 `src/resources/icon.png`（PIL）生成 `Assets/` 的 `StoreLogo.png`(50)/`Square44x44Logo.png`(44)/`Square150x150Logo.png`(150)；staging = `dist/msix-staging`（整目录拷贝 `dist/OhMyMeme` + 清单 + 资产）→ `makeappx pack /d <staging> /p <out.msix> /o` → 清理 staging；**清单为 full-trust 桌面应用**（`EntryPoint="windows.fullTrustApplication"` + `rescap:Capability Name="runFullTrust"`，`TargetDeviceFamily=Windows.Desktop`）；**不做签名**（Store 上架时重签，本地侧载需开发者模式 `Add-AppxPackage -AllowUnsigned`）；**刻意不禁用 AppData/注册表虚拟化**——`desktop6:FileSystemWriteVirtualization`/`RegistryWriteVirtualization` 需 `unvirtualizedResources` restricted capability 且官方限定「仅部分 Microsoft 及合作方游戏」，商店上架会被拒；代价是 MSIX 版的 `%APPDATA%`/`%LOCALAPPDATA%` 写入被重定向到包容器（与 `.exe` 安装的配置/data 目录不互通）
- 产物命名 `OhMyMeme-<filename_version>.msix`（nightly 即 `OhMyMeme-nightly.msix`）
- **Store 上架配套**：Partner Center「隐私策略 URL」指向仓库内 `docs/privacy.md`（中英双语，逐条列出全部主动联网目标：GitHub 更新检查/镜像、用户自建 FTP/S3/R2/WebDAV、抖音 `douyin.com`、微信 CDN、`googledownloads.cn`、`contributor.starsfire.top`、`uapis.cn`）。**新增任何联网功能时须同步更新该文件**（Store 会核对声明与实际行为是否一致）

### Linux 源码运行依赖 (GTK 后端)
- pywebview Linux 使用 GTK 后端（`WebKit2`），源码/venv/conda 运行需当前 Python 能导入 `gi`：
  - 系统层安装 `python3-gi`（Debian/Ubuntu `apt install python3-gi`；Arch `pacman -S python-gobject`）+ WebKitGTK 运行库（`gir1.2-webkit2-4.1` 或 4.0，Arch 经 `yay -S webkit2gtk`）
  - venv 创建时加 `--system-site-packages`，或运行前设 `PYTHONPATH=/usr/lib/python3/dist-packages`（Arch 为 `/usr/lib/python3.12/site-packages`）
  - conda 环境内可直接 `conda install -c conda-forge pygobject`
- deb/rpm 安装包已内置 `gi` 与 WebKit2/Soup typelib，无需 python3-gi，但依赖系统 `gir1.2-webkit2-*` 运行库
- **构建自动编译 Vue 前端**: `build.py` 的 `ensure_vue_frontend()` 在打包前检查 `src/webui/dist/ohmymeme.js`（被 gitignore，CI 全新检出缺失），缺失时自动 `npm ci`（有 lockfile，否则 `npm install`）→ `npx vite build`，失败则中止构建；构建机需 node/npm（GitHub Actions runner 预装），dist 已存在时直接跳过
- **C++ 测试 job（check.yml `keyfinder-tests`）**: 独立于 `check` job 且在 `windows-latest` 运行——helper 是 Windows-only（CMakeLists 对非 WIN32 直接 FATAL_ERROR），放进 ubuntu job 会必然失败；放在 check.yml 而非 build.yml 是为了让 PR 阶段即有反馈（build.yml 由 check 通过后的 workflow_run 触发）。
- **构建自动编译 wechat_keyfinder**: `build.py` 的 `build_keyfinder_helper()` 在打包前用 cmake+MSVC 编译 helper 并 `--add-binary` 打入产物（`*.exe` 被 gitignore，CI 全新检出缺失，故必须构建期产出）；无需 OpenSSL。**Windows 构建机需 cmake + MSVC**（GitHub Actions 的 `windows-latest` 预装；本机可用 VS BuildTools），缺失时构建**中止**（`--allow-missing-keyfinder` 仅供本地开发放行）。`build.yml`/`nightly.yml` 的 windows job 在打包后统一调用 `python scripts/build.py --verify-helper`（**单一实现，消除此前两处内联脚本重复导致的 drift 风险**），由 `build.py` 的 `verify_keyfinder_bundle()` 执行三项检查：①helper 存在（按文件名在产物树内查找，**不硬编码 `_internal/...` 布局**，PyInstaller 调整结构后仍有效）；②可执行性——`--help` 退出码为 0（**取代原先的 `size < 20000` 魔数**：既覆盖截断/架构不符/缺依赖，又不会因合法体积变化误报）；③PE 版本资源 `CompanyName == OhMyMeme`（缺元数据会退回 Defender 误报特征）。任一项不过即 exit 1。测试 `tests/test_build_verify.py`
- **构建内置裁剪版 ffmpeg（仅 Windows）**: `build.py` 的 `ensure_ffmpeg()` 在打包前定位裁剪 ffmpeg（`OHMYMEME_FFMPEG` → 工作区 `ffmpeg-win64/`（CI artifact）→ `build/ffmpeg-win64/out/`（本地脚本产物）），缺失默认**中止**（`--allow-missing-ffmpeg` 仅供本地开发放行，运行时回退 PATH 中的系统 ffmpeg），经 `--add-binary` 打入 `_internal/tools/ffmpeg/`，运行时由 `tg_stickers._resolve_ffmpeg()` 经 `_MEIPASS` 解析（解析不到回退 `shutil.which`）。裁剪配方 `scripts/ffmpeg/build_win64.sh`：pinned 源码（ffmpeg 7.1 / libvpx 1.14.1 / libwebp 1.4.0，均 sha256 校验）经 mingw-w64 交叉编译为**静态单文件**——ffmpeg `--disable-everything` 仅留 matroska 解封装、libvpx-vp9 解码、libwebp/libwebp_anim 编码、scale 滤镜、file 协议、vp9_superframe bsf；libvpx 关 VP8 且 `--disable-multithread`（使 ffmpeg configure 的 libvpx 兜底 check_lib——只链 `-lvpx -lm`，mingw 下 `pthreads_extralibs` 被 w32threads 门控恒为空——不依赖 -lpthread 也能通过），并传入 `CROSS=x86_64-w64-mingw32-`（libvpx 的 `setup_gnu_toolchain` 取 `${CROSS}gcc/ar` 而其源码从不设置 CROSS——只给 `--target` 会退化为宿主 gcc/ar、C 对象编成 ELF（仅 nasm 成员是 win64 COFF），mingw ld 打开成员时格式不符被静默跳过；CI 相应补装 `g++-mingw-w64-x86-64`，`[CXX] ratectrl_rtc.cc` 必需），并 `make HAVE_GNU_STRIP=no` 走 cp 分支规避 strip 改写归档索引的已知损坏类问题（grpc#6136）；ffmpeg 两条 libvpx 检查（pkg 与 check_lib）均报 undefined reference（`vpx_codec_vp9_dx`、`vpx_codec_control_`——后者是 vp8dx.h 展开的 ~20 个 static 包装函数各调一次）并静默裁掉解码器；安装后 `x86_64-w64-mingw32-ranlib` 重建归档索引并复刻 check_lib 做**链接自检**（失败输出链接错误与 config.mk 工具行、objdump -f 成员格式统计、nm -s 索引、ld -t trace 诊断后中止），libwebp 关 demux/png/jpeg 等但**必须保留 libwebpmux**（ffmpeg libwebp_anim 编码器依赖 ≥0.4.0）；configure 显式 `--pkg-config=pkg-config` 并预检 `libwebp.pc` 与 `vpx.pc`——交叉前缀默认找 `x86_64-w64-mingw32-pkg-config`（mingw-w64-tools 提供、runner 未装），缺失时 configure 仅把 warn 写进 config.log 不上屏并置 `pkg_config=false`，`require_pkg_config` 精确报 libwebp not found（configure 失败时输出 `ffbuild/config.log` 尾部兜底诊断）；configure 成功后硬断言 `config_components.h`（构建根目录）含 `#define CONFIG_LIBVPX_VP9_DECODER 1`——libvpx 检查失败只 warn+disable 不上屏，断言失败时 dump config.log 的 vpx 线索并中止；产物硬检查：非系统 DLL 依赖（静态链接失败即中止）、20MB 体积预算、组件字符串存在性（`libvpx-vp9`/`libwebp_anim`/`matroska`，任何平台 `grep -aq` 二进制，Linux CI 也能拦截被裁组件）、`-decoders/-encoders` 组件存在性（**执行级检查仅在能运行 PE 的环境**——MSYS2/Cygwin/Windows，Linux/WSL 交叉产物无法直接执行会 `Exec format error`，CI 组件校验由打包 windows job 的 `build.py --verify-ffmpeg` 端到端转换兜底）。CI 打包后跑 `python scripts/build.py --verify-ffmpeg`（`verify_ffmpeg_bundle()`：产物树内按文件名查 ffmpeg.exe + 组件断言 + 用与运行时共用的 `_webm_cmd` 对 `tests/fixtures/tiny_vp9.webm` 端到端转换并校验 RIFF/WEBP 魔数），测试 `tests/test_build_verify.py`
- **Linux 打包（GTK）**: `--linux` 时 `build.py` 自动传 `--additional-hooks-dir scripts/hooks`（收集 WebKit2/Soup typelib）并 `--collect-all gi`，把 PyGObject/GTK 打进产物，脱离系统 python3-gi 运行；构建机需装 `python3-gi gir1.2-webkit2-4.1 libgirepository1.0-dev libgirepository-2.0-dev gobject-introspection` 并 `pip install PyGObject`（对应 `build.yml`/`nightly.yml` build-linux job）；**PyGObject ≥3.52 硬依赖 girepository-2.0**（Ubuntu 24.04 对应 `libgirepository-2.0-dev`），只装 1.0-dev 会在 meson 元数据阶段报 `Dependency 'girepository-2.0' is required but not found`；deb `Depends: python3-gi, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0`

**前端架构**：主窗口与设置窗口共用 Vue 3 构建入口（`vue-src/`，IIFE 单文件）。设置页模板、样式与经典脚本在 `vue-src/settings/`，`webui/settings.html` 只保留挂载与插件注入锚点。修改任一窗口前端后需 `npx vite build` 再运行。

**上游 0.7.0 合并约束**：保留定制版 200 MiB 原件硬上限、可选长边阈值及超过 20 MiB 的三态压缩流程，不并行启用上游重复的超限导入决策；聊天记录媒体沿用自己的归档逻辑。`copy_resize_mode` 默认仍为 1，新增模式 4 仅作为可选项。同步偏好仍可配置且不得在加载时强制复位；`manifest_include_collections` 关闭时，完整重建及增量写入都不输出分组，类别路径和分类标签照常保留。差集上传只读取待上传文件的尺寸，并通过 `_entry_local_path` 与 `guarded_read` 访问原件。设置页迁移必须保留 NapCat 四种发送方式、监听轮询、隐藏分组、删除策略、搜索范围、虚拟盘保护及压缩导入选项。

`make` 命令仅供参考（`make run`/`make test`/`make lint`/`make format`/`make build`），macOS/Linux 下可能不可用，优先使用原生 Python 命令。

## CI (GitHub Actions) — 五个 workflow
- **check.yml**: Ubuntu, lint + test, push 和 PR 到任意分支均触发
- **ffmpeg-win64.yml**: 可复用 workflow（`workflow_call` + `workflow_dispatch`，`ref` 输入可指定缓存 key 分支），交叉编译裁剪版 ffmpeg 一次并 artifact 上传（actions/cache 按 `build_win64.sh` 内容哈希做 key，源码未变直接复用）；被 build.yml/nightly.yml/release.yml 的 windows job 以 `needs` 拉起
- **build.yml**: Windows + Linux + macOS 三平台，仅 `workflow_dispatch` 手动触发（不再随 check/main 自动构建）
  - `build-windows`: InnoSetup 安装包 `dist/OhMyMeme-*-setup.exe` + MSIX `dist/OhMyMeme-*.msix`（`choco install innosetup`，`python scripts/build.py --installer-only --msix` 一次产出；打包前 download-artifact 取 ffmpeg-win64 到工作区 `ffmpeg-win64/`，打包后 `python scripts/build.py --verify-ffmpeg` 校验内置 ffmpeg）
  - `build-linux`: AppImage/deb/rpm（`--linux`）
  - `build-macos`: `.app` + `.dmg`（`--macos`，PyInstaller `--windowed` + iconutil 生成 icns）；矩阵双架构 `arm64`（macos-latest）+ `x86_64`（macos-15-intel），产物 `OhMyMeme-v*-{arch}.dmg`
- **release.yml**: 手动触发的正式版发布——`prepare` job 从 `main` 的 `src/__init__.py` 读 `__version__`（校验格式、tag 不存在），三平台 job 构建（同 build.yml），`publish` job 汇总 artifact、生成三列下载表格正文、`gh release create <version> --target <main HEAD> --title <version>` 创建 Release，随后 `gh release upload <version> artifacts/* --clobber` 上传全部构建产物（tag 与标题均不带 v）；正文仅下载表格，更新内容由维护者随后手动编辑
- **nightly.yml**: Windows + Linux + macOS 三平台每日定时（UTC 20:00）+ `workflow_dispatch`，从 `dev` 分支构建非正式版（`--nightly`，版本号为 `nightly`）并发布为 `nightly` prerelease；`updater.py` 的 `_parse_release` 跳过 prerelease 与含 `nightly` 的 tag，**软件更新绝不会指向 nightly**；windows job 同样依赖 ffmpeg-win64 并跑 `--verify-ffmpeg`
- 上传 `dist/OhMyMeme-*-setup.exe` / `dist/OhMyMeme-v*-x86_64.AppImage` 等作为 artifact

## QQNT 外层宿主句柄兼容

- 若拖拽落点的原生顶层 HWND 与 Electron `BrowserWindow.getNativeWindowHandle()` 不相等，`/current-peer` 只允许在全部 `BrowserWindow` 中恰好一个能读到聊天标题时降级使用。检测到多个聊天渲染窗口时必须拒绝发送，不得回退到可能过期的 `state.current`。
- 第四种自聊原生重建递归组装外层卡片时，`onMsgInfoListUpdate` 可能同时返回作为输入的内层卡片与新生成的外层卡片。`qqNativeMultiForward()` 必须在回调谓词和最终选择中都排除本次 `entries` 的所有输入消息 ID，不得直接取第一张转发卡。新外层消息的首次事件可能尚未回填 `elements`/`peerUid`/`senderUid`，回调谓词只用“非本次输入”的新消息 ID 识别它，再由 `getMsgsByMsgId` 等待卡片落库且送达状态结束，才能作为下一层输入，否则 QQNT 会返回 `invalid status`。
- 第二种“重新读取原消息并重组”不得只使用归档时的 OneBot 短 `message_id`。`get_msg` 因 NapCat 重启返回“消息不存在”时，必须通过 `/recover-message` 用已持久化的来源会话 ID + 真实 `msgSeq` 重定位外层消息，再将解析后正文传入 `build_chat_record_package(source_message=...)`。

## 版本管理
- 版本号唯一来源: `src/__init__.py` → `__version__ = "*.*.*"`
- `scripts/build.py` 用正则从该文件提取版本；`--version`/`--nightly` 会临时改写 `__init__.py` 构建，完成后恢复
