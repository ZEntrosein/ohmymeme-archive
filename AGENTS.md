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
- **增改同步** — 增加新功能或创建新文件后，同步修改 `README.md` 和 `AGENTS.md` 中对应描述
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
  bulk_read.py      # 虚拟盘批量原件读取闸门（串行 + 分批暂停）
  gif_stego.py     # GIF 增量隐写（实验性：粘贴表情大小 + 无损还原原图）
  native_drag.py   # Windows 原生文件拖拽 (WinForms DoDragDrop + CF_HDROP, 惰性加载 pythonnet)
  crypto_util.py  # 加密 (Fernet + PBKDF2, 降级 XOR)
  manifest.py     # meme-index.json 构建/加载
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
  wechat_keyfinder/ # 微信密钥提取 C++ 辅助二进制源码（CMake 构建）
  vue-src/       # Vue 3 前端源码（Vite 构建，产物到 webui/dist/ohmymeme.js）
    App.vue      # 根组件：标题栏/搜索/侧边栏/面包屑/体裁与标签栏/统一内容网格/分页
    components/ChatRecordDetails.vue # 聊天记录来源、节点预览与发送命令弹窗
    main.ts      # 入口：挂载 + window.focusSearch 全局（快捷键呼出聚焦搜索）
    style.css    # 主窗口样式（CSS 变量主题，蓝色 #3b82f6）
    types/       # TS 类型定义 (Meme/Collection/Tag)
    utils/       # api 桥接 + esc + renderMarkdown
    composables/ # useMemes 状态 / useDragSort 拖拽 / useContextMenu / useCollectionBuilder
    components/  # Pager/TagEditor/MemeDetails/ImportMenu/NotionImportDialog/ImportProgressOverlay/
                 # SyncOverlay/ContextMenu/CollectionBuilder/CollectionTreeNode/UpdateDialog/SimilarImportDialog
  webui/          # 前端静态文件
    vue.html      # 主窗口入口（Vue），Bottle 优先加载
    dist/ohmymeme.js # Vite 构建产物（gitignored）
    settings.html # 设置窗口 HTML（vanilla，两列布局：左导航+右内容）
    settings.css  # 设置窗口样式
    settings.js   # 设置窗口逻辑（设置项/同步/导入向导）
    index.html/index.css/index.js  # 旧主窗口（已备份至 webui-backup/，不再使用）
config/
  offsets.json    # wechat_keyfinder 易变参数（版本号等，微信升级时只改此文件）
scripts/
  build.py        # PyInstaller + InnoSetup 构建脚本 (i18n zh/en)
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
  fixtures/grid_slot_probe.cjs # Node 网格拖拽槽位回归探针
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
- 惰性导入: `_pystray_ok()` 避免 headless CI (X11 `DisplayNameError`)
- WSL 自动跳过托盘
- macOS 跳过托盘：pystray 在 macOS 需在主线程抢占 NSApplication runloop，与 pywebview 主循环冲突（会导致窗口无法启动或段错误），与 Linux GTK 冲突同理

### 全局快捷键
- 三级降级: `keyboard` → `pynput` → 200ms 轮询 (`keyboard.is_pressed`)
- **运行期失效自愈**：`keyboard` 0.13.5 的 `GenericListener` 处理线程（`processing_thread`）一旦因未捕获异常崩溃即永久失效（进程存活、界面正常但热键无响应，需重启软件才恢复）。`GlobalHotkey` 两道防线：①`_try_keyboard` 用 `make_safe` 把回调包成 `_safe_callback`，吞掉回调异常，防止其杀死 `processing_thread`；②注册后启动 daemon 守护线程（`_start_keyboard_watchdog`，`KEYBOARD_WATCH_INTERVAL`=5s）周期检查 `keyboard._listener` 的 `listening_thread`/`processing_thread` 是否存活，任一死亡即 `_reregister_keyboard`（`remove_hotkey` → 置 `listener.listening=False` → `start_if_necessary()` → 重新 `add_hotkey`）自动重挂；③（Windows）**钩子心跳探针**：WH_KEYBOARD_LL 会被系统静默摘除（睡眠恢复/回调超时/显示切换），此时线程仍在泵消息、存活检查不可见——`_hook_health_check` 每 `KEYBOARD_PROBE_INTERVAL`(30s) 经 `keybd_event` 注入一次无害 F15 探针，`KEYBOARD_PROBE_TIMEOUT`(15s) 内钩子未上报任何键盘事件（`keyboard.hook` 观察者刷新 `_hook_last_seen`，用户自身按键也算）即判定钩子失效，`_restart_keyboard_listener` 以 `PostThreadMessageW(WM_QUIT)` 结束旧监听线程（其钩子随线程退出被系统移除；线程拒不退出则中止重启避免双钩子导致热键触发两次）→ `start_if_necessary()` 重装钩子 → 重挂热键，约 45s 内自愈。**热键事件日志**：`_get_file_logger` 惰性创建独立 logger，把注册/回调异常/线程死亡/自愈重挂等事件追加到 `data_dir/hotkey.log`（`_log_hotkey_event` 同步写文件+控制台，初始化失败降级为常规 logger 不阻塞）。**pytest 下禁用文件日志**（`PYTEST_CURRENT_TEST` 环境变量守卫）——TestHotkeyWatchdog/test_startup 会触发注册/重注册日志，夹具错误（如 inject-fail）曾污染真实 hotkey.log 被误读为运行期自愈失败（真实自愈必先记录「监听/处理线程已退出」，日志中无此行即无真实失效）。**重注册与注销的正确性**：`_reregister_keyboard` 返回 bool 且与 `unregister` 持同一 `_reregister_lock` 串行化——`add_hotkey` 失败时置 `_reregister_pending=True` 并返回 False（不记录成功、守护下轮继续重试），成功才清 pending 并记成功事件；注销已开始（回调已清空或停止事件已置位）时重注册直接返回 False 不重新挂热键。**生命周期代次 token**：`_watchdog_gen` 仅在 `unregister` 递增（`register`/`_try_keyboard` 不递增——同一实例重复 register 时旧 watchdog 仍受 `unregister` 已递增的代次约束），`_start_keyboard_watchdog` 捕获当前代次并由 `_reregister_keyboard(listener, gen)`/`_restart_keyboard_listener(listener, gen)` 在锁内校验 `gen == _watchdog_gen`，旧 watchdog（旧代次）的重注册操作直接返回 False，杜绝注销后立即重新注册时旧线程对新热键的误操作。
- macOS 跳过 `keyboard` 库（darwin 后端需 root 权限，报 `Error 13` 且 root 下会段错误），直接走 `pynput`（CGEventTap，需辅助功能权限）
- WSL 无法捕获全局快捷键
- 配置 `hotkey_show_at_mouse` 默认 `false`；仅 Windows 生效。开启后仅在全局热键将隐藏主面板显示时，按鼠标所在显示器工作区依次尝试 `(cursor_x, cursor_y)`、`(right-width, cursor_y)`、`(cursor_x, bottom-height)`、`(right-width, bottom-height)`，仅使用首个完整容纳窗口的候选位置；出错或没有可用位置时不移动。托盘保持普通切换，热键回调仍为零参数。
- WebUI 维护非持久的快捷键显示会话状态：仅隐藏主窗口被全局快捷键显示后，成功复制或成功原生向外文件拖拽才会自动隐藏；任意 hide、普通/托盘显示、LAN/其他 show、内部排序拖拽及失败交互均不会触发该自动隐藏。

### 窗口
- 主窗口 ~960×640 frameless, 设置窗口 720×560 frameless（每次 `_create_settings_window` 以主窗口当前位置居中创建——`x = 主窗口x + (宽-720)//2`，主窗口坐标不可用时交由系统摆放）
- 设置窗口是独立 webview：`open_settings` 每次销毁重建，`focus_settings_window` 仅做 z-order 提升
- Windows 全局热键显示位置仅在隐藏到显示的转换时计算，使用鼠标所在显示器工作区；不改变托盘激活或其他窗口显示路径
- 自定义 JS 拖拽: 鼠标事件 → `pywebview.api.move_window(dx, dy)`
- 增量回退（Windows/macOS）用 `screenX/screenY`（**勿改 `clientX/clientY`** — clientX 是相对窗口坐标，窗口自身滞后位移会被下一次 mousemove 当作反向增量回传，形成反馈振荡导致高频抖动）；Linux 走合成器原生拖动不经过此路径
- **Linux 拖拽必须走合成器**：`w.move()` 在 Wayland 下无效（合成器不允许客户端自定位），mousedown 时 JS 调 `start_window_drag()` → 后端 `GLib.idle_add(native.begin_move_drag, ...)` 交给合成器交互式拖动；时间戳用 `Gdk.CURRENT_TIME`（GDK 文档允许未知时间时用它，X11 回填最近输入事件时间、Wayland 不参与）
- `#titlebar` 上可拖拽 (排除 `.title-btn` 按钮区域)
- 侧边栏折叠按钮 `.sidebar-toggle` 位于搜索框左侧（`#search-wrap` 内），点击折叠/展开 `#sidebar`；搜索框 `flex:1` 随侧边栏 180px↔48px 动态伸缩

### 数据库
- 10 表: `memes`, `tags`, `meme_tags`, `collections`, `meme_collections`, `categories`, `favorites`, `recent_uses`, `notion_sources`, `chat_records`
  - `tags`/`meme_tags`：`tags` 以 `(category, name)` 作为不区分大小写的复合唯一标识；旧库启动时重建旧 `name UNIQUE` 表并把已有标签迁入空分类（UI 显示「未分类」），原 tag id 与 `meme_tags` 关联保持不变。DB 同时保留字符串兼容接口，并提供 `get_all_tag_records`/`get_meme_tag_records` 结构化接口；`set_meme_tags`/`add_tags_to_memes`/`rename_tag`/`search` 接受 `{category,name}`，所以不同分类可存在同名标签且独立筛选/改名。主标签栏按分类分组；`MemeDetails` 与 `TagEditor` 保留分类并允许新标签指定分类，`MemeDetails` 的标签编辑入口还能全局修改现有标签的名称与种类，目标已存在时合并关联；`_set_tags` 覆盖式写入；`_prune_orphan_tags` 在 `_set_tags` 与 `delete_meme` 中清理无任何表情使用的孤儿标签
  - `memes.manual_description`/`manual_ocr_text` 与 `memes.ai_description`/`ai_ocr_text`：分别保存人工和 AI 来源的图片描述/图片文字，四项都是普通用户可编辑字段；旧库由 `_migrate` 幂等新增空的人工字段，因此升级前已有值原封不动保留为 AI 内容。`JsApi.get_meme_details` 仅在打开单张详情时按需返回四个独立字段、向旧调用方提供人工优先回退 AI 的中性字段 `description`/`ocr_text`、标题、文件名、直接所属分组（完整层级路径）、全部可选分组、全量标签建议和全图库前后项；新版 `update_meme_details` 独立保存四个字段，旧版中性载荷仍写 AI 字段。详情保存以单事务写入标题、文件名、文字、标签与分组归属。分组更新保留未移除成员关系的 `sort_order`，新加入分组追加到末尾；这里只更新 `meme_collections`，不移动磁盘文件。文件名变化时 JsApi 先校验安全名称/扩展名/冲突，再同步重命名实际文件，DB 写入失败则把文件名回滚，避免加入分页网格载荷
  - `memes.daily_filename`/`daily_file_size`/`daily_mime_type`/`daily_profile`：指向 `data_dir/derivatives` 中的可再生日常副本；旧库由 `_migrate` 幂等补列。`filename`/`file_hash`/`file_size`/`mime_type` 始终描述原件，不能改成副本语义
  - `notion_sources`：本地 Meme 与 Notion 托管文件的多对一来源映射；以 data source/page/property/file index+name 唯一定位，保存 `last_edited_time`，`meme_id` 删除时级联清理；旧库与旧备份由 `_migrate` 自动建表
  - `chat_records`：保存 `/omm` 捕获的完整 JSON 结构包；以 `(account_id, source_message_id)` 唯一，重复归档同一外层消息时更新。摘要列保存标题、节点数、转发资源数和外层会话位置，`package_json` 保存来源消息、命令位置以及所有已获取的嵌套 forward 数据；`list_chat_records`/`count_chat_records(keyword)` 为统一内容库提供按标题搜索和分页，`get_chat_record_entry` 返回摘要+完整包；`get_chat_record_by_source` 用于重复归档时复用已落地媒体。删除记录必须经 `onebot.delete_chat_record_archive` 同步删除其独占媒体子目录；旧库与旧备份由 `_migrate` 自动建表
  - `categories`：与 `collections` 分离的单归属层级类别；`memes.category_id` 只保存一个类别节点，父类别筛选递归包含子类别。类别名称经过目录安全校验，`cache_dir/<类别路径>/<filename>` 是原件的真实存储位置；详情换类、类别重命名/删除会同步移动原件，原有多对多分组不改变路径。
- `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`
- `MemeDB.search()`: 动态 WHERE, 多标签交集用 `HAVING COUNT = len(tags)`；keyword 始终匹配 `filename`/`original_name` 和标签名（`meme_tags` JOIN `tags` 子查询 LIKE），`include_description` 同时控制 `manual_description`/`ai_description`，`include_ocr_text` 同时控制 `manual_ocr_text`/`ai_ocr_text`。`genre=image|gif` 是由 MIME/`.gif` 扩展名推导的不可编辑系统体裁条件，不写入 tags 表。`JsApi.search_memes`/`count_memes` 从配置 `search_description`/`search_image_text` 传入；`count()` 必须同步该逻辑以保证分页总数一致
- `MemeDB.add_tags_to_memes(meme_ids, tags)`: 批量合并追加标签（get-or-create + `INSERT OR IGNORE`，不清空各表情已有标签），返回实际存在的表情数；先过滤出实际存在的 meme id（外键开启时对缺失 id 写 `meme_tags` 会整批失败），批量写包 try/rollback/re-raise；供 JsApi `batch_add_tags` 使用
- `MemeDB.add_memes_to_collection(meme_ids, collection_id)` / `MemeDB.move_memes_to_collection(meme_ids, from_ids, to_id)`: 批量加组/移动的单事务实现（先校验表情 id 与目标分组存在，写入失败 rollback 后抛出）；按 `INSERT OR IGNORE` 实际新增关联计数（重复加入目标不计）；move 仅纳入实际属于 `from_ids` 子树的成员（非成员不受删除或移动影响），从子树删除后再加入目标，提交前级联清理源子树内变空的分组（迭代删除无成员且无子分组的叶子空组直到不动点，支持子组先空父组后空；仅限 from_ids 范围，子树外空分组保留；两处 FK 均 ON DELETE CASCADE，故只删叶子防波及）；供 JsApi `batch_add_to_collection`/`batch_move_to_collection` 使用（JsApi 负责目标解析/创建与「不能移入源分组自身或其子分组」校验——`create_collection` 会复用同名顶层分组，故该校验在目标解析后统一执行；move 成功后无条件重建 manifest——`moved==0` 时成员关系仍可能变化，如成员已在目标、仅从源移除）
- `memes.sort_order`: 自定义排序（拖拽更新），默认 0，查询 `ORDER BY sort_order ASC, updated_at DESC`
- `collections.parent_id`: 多级分组支持（最多 3 层），`NULL` 为顶层分组
- 主窗口侧边栏以 tabs 分开「分组」与「类别」：`CollectionTreeNode` 渲染两棵树，类别支持递归子类别、搜索/详情选择和右键新建/重命名/删除；类别父节点筛选包含后代，切换 tab 时只保留当前树的筛选。
- `meme_collections.sort_order`: 分组内成员自定义排序
- `recent_uses`: `meme_id` + `used_at`，复制时 `INSERT OR REPLACE`，按 `used_at DESC` 取最近使用

### 配置
- `%APPDATA%/OhMyMeme/config.json` (Win), JSON 格式
- 密钥字段 (ftp_password, s3_secret_key 等) 用 Fernet 加密存储
- 全局单例: `get_config()`, `get_db()`
- `hotkey_show_at_mouse` 默认 `false`，控制 Windows 上全局热键显示隐藏主面板时是否按鼠标位置放置
- `notion_token` 属于 `_SECRET_KEYS`，与其他密钥一样加密写入配置；Notion URL/ID、data source ID 及属性映射保存在同一配置文件
- `napcat_token` 属于 `_SECRET_KEYS`；`napcat_enabled`、HTTP/WebSocket 根地址、Token、`napcat_command`（默认 `/omm`）和 `napcat_send_mode`（`original_native`/`original_rebuild`/`archive_rebuild`，默认 `original_native`）由设置页「QQ 聊天记录（NapCat）」保存。接口地址强制为 `127.0.0.1`/`localhost`/`::1` 回环地址，HTTP 与 WebSocket 都拒绝重定向且绕过系统代理，命令必须是以 `/` 开头且不含空白的单词
- `Config.derivative_dir` 固定为 `data_dir/derivatives`，存放可再生日常副本；不跟随自定义 `cache_dir` 迁移，不进入备份或远程同步
- `bulk_read_protection` 默认 `false`；开启后由 `bulk_read.guarded_read()` 对可能读取虚拟盘原件的操作单文件串行化，并按 `bulk_read_batch_size`（后端钳制 1–1000，默认 8）和 `bulk_read_pause_seconds`（0–60 秒，默认 1）分批暂停。已接入启动缓存扫描、pHash/日常副本惰性生成、导入、manifest 重建、存储迁移、备份、同步上传和 LAN 拉取；仅为 115/CloudDrive2 等虚拟盘降低突发请求，不应默认开启在普通本地磁盘上
- `cache_dir`（表情包图片与聊天记录媒体目录）可自定义：配置键 `cache_dir` 非空时 `Config.cache_dir` 返回该路径，否则默认 `data_dir/cache`；`Config.chat_record_media_dir` 固定为其下的保留目录 `chat-record-media`，其中每份聊天记录使用来源账号+外层消息 ID 哈希出的独占子目录，结构包保存 cache 相对路径。表情扫描、文件查找、LAN 与 Notion 来源查找必须跳过这个根级保留目录，避免把归档图片误注册成表情。设置页「存储位置」通过 `SettingsApi.pick_storage_dir`/`apply_storage_dir` 切换，`apply_storage_dir` 可选把旧目录文件递归迁移（跳过 `thumbnails`）；**切换后旧文件不再可见**，故未迁移时必须确保文件已存在于新目录；`_storage_dir_validation` 拒绝相对/相同/上下级目录以及 `data_dir`/`thumbnail_dir` 及其上下级（受保护路径）；DB/缩略图/manifest 仍留在 `data_dir`，数据库只存表情文件名，聊天记录媒体保存 cache 相对路径；`reset_settings` 恢复默认时保留 `cache_dir`。迁移为**后台三阶段幂等**设计（避免跨盘长拷贝时进程被杀导致分裂状态）：①复制阶段源只读（`O_EXCL` 排他写入，dst 已存在且大小一致视为已复制跳过——幂等；失败/取消仅清理本次新副本，源完好无分裂，不回滚）；②写配置（唯一切换点，此后新目录已完整）；③删源（失败仅残留旧目录冗余，不阻断）。迁移开始写 `data_dir/storage_migration.json` 清单、完成后删除；`main.py` 启动时若检测到未完成清单则后台幂等续跑（强杀/断电后重启自愈）。取消由 move 回滚改为删新副本，消除回滚自身失败风险

### NapCat / OneBot 连接
- `websocket-client` 连接 NapCat 正向 WebSocket 服务；NapCat 必须为该服务开启 `reportSelfMessage`。`OneBotCommandListener` 仅处理当前账号自己的 `message`/`message_sent` 事件，不调用 `get_recent_contact`。命令包括：回复记录后发 `/omm` 保存、`/omm <message_id>` 排障保存、`/omm list` 列最近十份归档、在目标会话发 `/omm send <ID>` 取用
- 命令事件经单 worker 串行处理，按命令消息 ID 去重，WebSocket 断开后每 3 秒重连；启动、设置保存/立即应用、恢复默认和应用退出分别负责监听器的启动/重载/停止。成功或失败通过 `send_group_msg`/`send_private_msg` 回到命令所在会话
- `build_chat_record_package()` 先用 `get_msg` 获取外层消息，再递归调用 `get_forward_msg`；NapCat 的 OneBot 全局配置必须开启 `parseMultMsg` 才能展开多层嵌套记录。节点正文兼容 `node.message`、NapCat 4.18 的 `node.data.message` 与 `node.data.content`，并把消息段中的 `type: node` 嵌套包装统一视为内联 forward 层级。归档上限为 100 份 forward 资源、12 层、20000 节点和 64 MiB JSON，任何上限、嵌套读取失败或最终仍无正文的空节点都会拒绝写入不完整数据；空节点也可能来自过期、撤回或未支持类型，不能一律归因于 `parseMultMsg`。`archive_chat_record()` 随后沿实际聊天节点树递归查找 image/record/video/file（同时覆盖 forward 资源与 `parseMultMsg` 内联内容），依次尝试桥接插件回填的 QQNT 本地路径或刷新 URL、标准消息段 URL，以及类型专用内部标识，并原子复制到 `cache_dir/chat-record-media/<来源哈希>/`（图片单文件上限 64 MiB，其他媒体 512 MiB），在媒体段写入 cache 相对 `local_path`，摘要以 `media_localized`/`media_localized_count` 标识结果；重复保存先复用既有本地媒体并清理同记录目录中的过期文件。虚拟盘清理必须按归档结构中声明的安全相对路径作词法保留，不能因刚写入文件暂时无法 `is_file`/`resolve` 而误删。模式二、三发送前逐段把媒体解析为真实本地文件，单段失败只降级该段，不能直接把可能过期的接收态 URL 交给 NapCat，也不能因一个媒体失败而降级整份记录。`migrate_legacy_chat_record_media` 在启动后台把旧 `data_dir/chat-record-media` 文件逐记录复制、落库后才删旧文件
- NapCat 的 OneBot `message_id` 是当前进程内 `MessageUnique` LRU 生成的短 ID，重启或淘汰后不能作为永久索引；归档必须同时保存来源会话、QQ `real_seq`、完整消息段和节点内容，不能只保存 `message_id` 或 forward id。数据库迁移会用 `package_json.source.real_seq` 修正早期误存短 ID 的 `source_message_seq` 摘要列
- `send_chat_record` 严格按 `napcat_send_mode` 选择单条路径，测试失败时不会跨模式自动回退：`original_native` 先用外层 OneBot `message_id` 调 `forward_group_single_msg`/`forward_friend_single_msg`；短 ID 失效时改由 WebUI 鉴权插件 `/forward-message` 按来源会话+`real_seq` 获取当前进程可用的 QQ 原始 `msgId`，验证时间/发送者/合并转发类型后调用 `MsgApi.forwardMsg`，这仍是同一种原生发送方式。QQ 历史已不可取或旧包没有 `real_seq` 时明确失败，不自动改用重组；`original_rebuild` 调 `build_chat_record_package` 重新读取原消息节点，并按媒体顺序合并旧归档中仍存在的 `local_path`，取得原始长消息负载后还要 `save_chat_record` 写回同一归档；`archive_rebuild` 只读取本地 `package_json`。两种重组路径先把聊天记录树递归转换成 OneBot `node`；配置 WebUI 时 `_send_qqnt_native_rebuilt_forward` 通过系统临时目录中的随机 JSON 文件调用插件 `/send-native-forward`。存在 `omm_native_payload_b64` 时插件只原样上传该负载并替换外层卡片资源 ID，返回 `qqnt_native_replay`；不得修改原生负载内部节点。旧归档没有负载时才逐叶暂存到自己的 QQ 会话，让 `multiForwardMsgWithComment` 生成模板，再在 wire bytes 中重建节点；这条兼容回退不能保证 Windows QQ 接受。不得将原生 action 先解码成 NapCat 对象再用 `UploadForwardMsgV2` 重编码，该路径会丢失/改写 QQNT 未公开字段。新导入节点会保存 `omm_sender_uid`；旧归档可用 `user_id` 转换 UID，转换失败必须报错，不得默认成自己。临时文件限制 64 MiB，插件仅接受系统临时目录内 `ohmymeme-native-forward-` 前缀，读取后立即删除。没有 WebUI 地址时才沿用 OneBot `send_*_forward_msg`。接收态 image/record/video/file 不能原样回传：`_media_send_source` 按 `local_path → path → url → file` 选择 HTTP(S)/file/base64 或确实存在的绝对路径；没有归档副本时，图片内部文件 ID 再经 `download_file_image_stream`（兼容回退 `get_image`）尝试还原，统一写为发送态 `{file: source}`。旧 OneBot 路径遇媒体下载失败时仍会转文字占位重试；QQNT 原生路径失败则明确报错，不盲目重发，避免结果已发出但响应异常时产生重复卡片
- 深层重组仍列入 README 待办：`_forward_nodes` 当前保留最多两级内层记录（连同最外层卡片共三层），更深内容在最深可用层加说明后展开，保证不丢消息。`/send-native-forward` 已具备自内向外的 QQNT 原生构造能力，但在完成更深层兼容矩阵前不得擅自移除应用侧层级限制；不直接修改 `napcat.mjs`，也不维护 NapCat fork。
- **聊天记录拖到 QQ 直接发送（Windows）**：聊天记录卡 pointer 位移超过 8px 后调用 `JsApi.start_chat_record_drag`；`native_drag.start_chat_record_target_drag` 用私有 `OhMyMeme.ChatRecord` OLE 格式启动仅用于选目标的 `DoDragDrop`，不向 QQ 投递文件、文本或命令，并用 `QueryContinueDrag` 区分松手与 Esc。松手后按光标位置取得顶层窗口，严格要求进程名为 QQ；`qq_drag.resolve_qq_target` 先向 NapCat WebUI 的当前会话桥接插件读取真实 peer ID（先以 WebUI 初始 Token 换取短期 Credential），插件未安装、Token 未配置或没有可用状态时才通过 Windows UI Automation 对聊天区顶部固定 15 个坐标做点命中，只读 Button/Text 名称（禁止 `FindAll(Descendants)` 遍历整个 QQNT 消息树，也不要恢复上百次跨进程点查询），再与 `get_group_list`/`get_friend_list` 唯一匹配成真实 ID。匹配同时使用好友昵称和备注；允许 QQNT 在唯一别名两侧附加状态文字，但只有全部包含匹配均指向同一个目标时才发送。歧义或不可读时拒绝发送并返回实际采到的控件名称。成功后直接按 `napcat_send_mode` 调 `send_chat_record`，群内只出现最终记录卡片。QQNT 不提供带消息/资源 ID 的标准拖出数据，所以反向拖入 QQ 卡片暂不支持，导入仍用回复卡片后 `/omm`。
- **NapCat 当前会话与持久消息桥接插件**：`napcat-plugin-ohmymeme-current-peer/index.mjs` 注册受 WebUI 鉴权保护的 `/current-peer` 路由。拖拽发送的主路径必须把落点 QQ 顶层 HWND 传给插件；插件用 Electron `BrowserWindow.getNativeWindowHandle()` 精确匹配同一个渲染窗口，只读取 `.chat-header .panel-header__title`，应用再用 `get_group_list`/`get_friend_list` 唯一映射成 OneBot ID。这个 `confidence=renderer_title` 路径不依赖“最近一次切换”，因此多聊天窗口也不会串到前一个会话。旧事件路径只作无 HWND 调用兼容：`onUserTabStatusChanged` 一到即先清空旧目标，`onMsgBoxChanged` 的唯一候选可以更新；`onMsgInfoListUpdate`、`onMsgInfoListAdd`、`onInputStatusPush` 都可能来自后台会话，绝不能注册成当前会话更新源。插件清理时只删除自己安装在复用 listener 上的回调。`/forward-message` 把持久来源 UIN 转为内部 UID，以 `MsgApi.getMsgsBySeqAndCount`（并用查询 API 回退）精确匹配 `msgSeq`，再直接调用 `MsgApi.forwardMsg`。`/recover-forward-resource` 用 `PacketApi.pkt.operation.FetchForwardMsgRaw` 同时读取主 `MultiMsg` 与附属 action，从 `richMsg.m_fileName` 重建内联 forward 树；`src/onebot.py` 对每个顶层 forward 资源都调用它，并把外层来源会话、`real_seq`、时间与发送者一并交给插件，因此节点正文已完整时也会补全媒体；只有存在空节点时插件失败才属于结构恢复错误，否则静默继续后备媒体链路。NapCat 4.18.28 的 Packet 伪造转发把外层及嵌套 `com.tencent.multimsg` ARK 的 `extra` 生成为对象，而 Windows QQNT 原生卡片要求它是末尾带换行的 JSON 字符串；插件分别包装 `MsgApi.sendMsg` 与 Packet 转换器的 `rawMsgWithSendMsgToPacketMsg`/`rawMsgToPacketMsg`，只把该应用且对象形式的 `extra` 规范化为 `JSON.stringify(extra) + "\n"`。已经是字符串的原生转发及其他 ARK 不修改，清理插件时仅在包装函数仍属于本插件时恢复原方法；`/debug-state.forward_card_compat` 返回钩子状态与累计改写数。媒体归档优先复用 QQ Chat Exporter 的路径：按持久来源重新取得外层原消息，再递归调用 `MsgApi.getMultiMsg(peer, msgId, msgId)` 获取带真实 `msgId`/`elementId` 的内层 RawMessage。图片先尝试 `FileApi.downloadMedia`，超时时再用原生 `picElement` 经 `FileApi.getImageUrl` 生成带当前 rkey 的地址并下载；视频、语音和文件分别复用 Native RawMessage 的路径或 `getVideoUrl`/`getVideoUrlPacket`、`getPttUrl`、`getFileUrl` 刷新地址，再按同类型顺序回填到多 action 协议树。若真实消息树不可取，图片才从 `MsgInfo.index` 回填 NapCat 4.18 `PacketMsgPicElement` 丢失的 `fileUuid`、MD5 和大小，依次尝试现有 QQNT 缓存、协议节点自身身份、visit/UUID 内核接口与签名 URL。图片签名地址下载严格限制为腾讯 `qq.com`/`qq.com.cn`/`qpic.cn` HTTPS 域名、拒绝任意外部主机和 64 MiB 上限，校验图片格式及可用时的 MD5 后原子写入 QQNT 标准媒体缓存，再交给 OhMyMeme 复制到永久归档。受 WebUI 鉴权保护的 `/media-status` 仅保留最近一次恢复的逐媒体阶段、布尔值和 HTTP 状态码，不记录消息 ID、元素 ID、URL、Token、本地绝对路径或媒体内容；`/media-capabilities` 只列出 QQ 内核媒体服务的方法名，用于定位版本特有的拖拽导出能力。`src/onebot.py` 先调用 `/api/auth/login` 用 WebUI 初始 Token 换取短期 Credential，再访问插件路由；插件/渲染标题不可用时才使用 UIA 标题降级。`scripts/install_napcat_current_peer.ps1` 默认只复制插件入口与清单；对包含“只接受内置白名单 ID”限制的 NapCat Framework，可显式追加 `-PatchLocalWhitelist`，脚本会备份并仅在精确锚点存在时修改 `napcat.mjs`。插件内部 API 属于 NapCat 高级 API，升级 QQNT/NapCat 后需复验 Electron DOM 选择器与消息查询签名。
- **Windows QQNT 重组兼容**：导入时 `/recover-forward-resource` 在不超过 5 MiB 时附带 `native_payload_b64`，`src/onebot.py` 存为根记录的 `omm_native_payload_b64`。模式二重新读取后必须写回同一归档；模式二、三调用 `/send-native-forward` 时传入原始负载、归档卡片及来源会话类型/ID。插件通过 `SsoSendLongMsg` 原样重传，上传资源 envelope 必须按原会话选择：原群聊用 type 3、原群号作为 owner/groupUin，原私聊用 type 1、当前账号 UID 作为 owner。不得改写任一内部节点，因而能保留发送者名称和头像；本地媒体副本也不注入原始负载。旧归档没有原始负载时才沿用节点重组回退。已验证仅规范化外层 ARK `extra`、克隆原始外层元素、以及先发送到自聊再 `MsgApi.forwardMsg` 中继均不能使重组卡片在 Windows QQ 打开，所以外层克隆、自聊中继和专用诊断路由不得恢复。模式二、三当前只保证手机 QQ 可查看；Windows 桌面端要求可靠时必须使用模式一。外层 ARK 规范化钩子仅作为旧 OneBot 路径兼容保留。
- 合并转发节点里的 `group_id` 等会话字段可能来自兼容回退值，不作为来源依据；会话类型、会话 ID、外层消息 ID/序号必须从用户选择的外层原消息单独保存

### 同步
- manifest 文件: `meme-index.json`
- SHA-256 差异对比, `push(delete_remote)`/`pull(remove_local)`
- 远端路径: `{root}/memes/`, `{root}/meme-index.json`
- 同步进度: `_sync_state` 全局变量追踪进度，`get_sync_progress()` 供 JS 轮询
- `push()`/`pull()` 内循环中更新 files_done/bytes_done/current_file 等字段
- 前端 300ms 轮询 `get_sync_progress()` 显示进度条 + 实时速度
- 设置页 4 个开关控制进度/完成弹窗是否显示
- **多线程传输**: `push()`/`pull()` 使用 `ThreadPoolExecutor`，每个线程创建独立后端连接
- 并发数: 配置项 `sync_threads`（默认 3，范围 1-8），通过 `config.json` 或 `SettingsApi` 修改
- `_push_worker`/`_pull_worker`: 接收文件子列表，操作独立后端连接，原子递增 `_sync_state`
- **WebDAV 目录创建去重**: `_push_worker` 同一批次所有文件都上传到 `memes/`，只在循环前 `ensure_remote_dir` 一次；`_WebDAVBackend.ensure_remote_dir` 对**每个目录 URL**在 `_dav_dirs_lock` 内原子执行「缓存检查 + MKCOL + 缓存写入」，成功/405/复核命中均写 `_dav_dirs` 缓存，多 worker 并发对同一目录也只发一次 MKCOL——避免重复 MKCOL 触发远端锁。**`push()` 每次在拿到 `_sync_run_lock` 后先清空 `_dav_dirs`**，避免命中上一次同步缓存、跳过已被删除远端目录的 MKCOL。
- `_sync_lock` (`threading.Lock`) 保护 `_sync_state` 写操作；`_increment_sync_progress()` 提供原子递增
- `_chunk_list(lst, n)` 将文件列表均匀切分给各线程
- **push 动态 manifest 维护**: `push()` 上传过程中每 `_HEARTBEAT_INTERVAL`（5s）用「远端已有 + 本次已确认上传」快照增量更新远端 manifest（`_build_push_manifest` + `_upload_manifest_data`，失败仅告警不中断）；部分失败中断前也上传该快照（避免远端有文件却无有效 manifest）；成功路径在合并远端独有项后补入已上传但不在本地清单的项（去重 guard），保本地被清空等边角。manifest 只列确认上传成功的文件，不产生幻影条目
- **孤儿清理互斥与进度**: `cleanup_remote_orphans(delete=True)` 删除前非阻塞获取 `_sync_run_lock`（被 push/pull 占用时返回「同步正在进行中」，绝不并发删除）；删除循环复用 `_sync_state`（`direction="delete"`，更新 current_file/files_done/files_total/progress，状态 deleting→done），前端复用 `#sync-progress-overlay` 轮询展示；扫描（delete=False）不互斥
- **远端文件名校验**: `_safe_remote_fname()` 拒绝路径穿越/绝对路径/隐藏名；`_fetch_remote_memes` 解析远端 manifest 时过滤不安全文件名（含非 dict 条目），`_pull_worker` 下载前二次校验
- **S3 后端 OSS 兼容**: boto3 客户端固定 `signature_version='s3'`（V2 签名，boto3 的 V4 与 chunked encoding 强耦合，OSS 不支持）；寻址方式由 `s3_addressing_style` 配置控制（默认 `"virtual"`，可选 `"path"`），映射到 `BotoConfig(s3={"addressing_style": ...})`；阿里云 OSS 仅支持 virtual-hosted style（bucket 作子域名），path-style 请求被拒绝；设置页 S3 表单「寻址方式」下拉框切换

### 更新
- GitHub API 查询: `/releases/latest` → `/releases?per_page=5` 回退
- **仅检查稳定版**：`_parse_release` 跳过 prerelease 与含 `nightly` 的 tag（保证软件更新绝不指向非正式版）；`_parse_version` 跳过非数字段（如 `0.6.0-nightly`）
- **非阻塞检查**：`check_latest_cached(force=False)` 是唯一入口——**`_ensure_check_started` 先查 `_check_running`（在跑则一律返回 `pending`，含 force 刷新未完成时，绝不命中旧 `_check_result`），再对非 force + 新鲜缓存（`_CHECK_TTL`=24h）直接返回**；无缓存/缓存过期/`force=True` 触发后台 daemon 线程跑 `check_latest()` 填 `_check_result`+`_check_result_at`（`_check_lock` 保护，幂等只启动一次），立即返回 `pending: true`（永不阻塞网络 3.8s~20s+）。**generation token**：`reset_check_cache()` 推进 `_check_generation`，后台 `_task` 完成时仅当代号匹配才写结果，防在途旧任务覆盖 reset 后新状态。`webui.py` 的 `JsApi.check_update`/`SettingsApi.check_update` 支持 `(debug, force)` 透传；前端 `App.vue` 的 `checkUpdateAndPrompt`（onMounted/24h 定时）首发 `force=true`、pending 时转 `checkUpdateResult` 非 force 轮询，`settings.js` 的 `checkUpdate` 首发 force、while 轮询暂取非 force——避免完成后再次 force 触发新检查造成永久 pending。**这解决了启动期间 `check_update` 同步阻塞曾导致的界面交互卡顿，及缓存不失效时 24h 定时器形同虚设的问题**
- 镜像并发: `_urlopen_mirror` / `_urlretrieve_mirror` 用 `ThreadPoolExecutor` + `as_completed`
- 镜像列表: `github.dpik.top` → `gh.dpik.top` → `gh-proxy.org` → 自建镜像（仅用于版本查询）→ 直连 GitHub
- 下载进度: `start_download()` → 后台线程 → JS 每 500ms 轮询 `get_download_progress()`
- Linux 更新: `_pick_asset_url` 选取 `.AppImage` 资产；`run_installer` Linux 分支 chmod +x 后直接 `Popen`（AppImage 是 ELF 非 shell 脚本），无 `/dev/fuse` 时追加 `--appimage-extract-and-run` 回退（`_needs_appimage_fallback`）；下载默认文件名走 `_default_asset_name()`（Linux 为 `OhMyMeme-v{version}-x86_64.AppImage`）
- macOS 更新: `_pick_asset_url` 按当前架构选取 `.dmg` 资产（arm64/x86_64）；`run_installer` 走 `_install_dmg_macos`（`hdiutil attach` → `ditto` 复制 `.app` 到 `/Applications` → 打开应用程序目录）；`_default_asset_name()` 为 `OhMyMeme-v{version}-{arch}.dmg`

### 局域网互联 (lan.py)
- **入口**: `lan.start(port, secret)` / `lan.stop()`，`get_status()` 供设置页轮询；`set_allow_secret_config()` 控制是否允许密钥传输（仅内存生效），`set_confirm_callback()` 注入设备确认回调（WebUI 提供）
- **UDP 发现**: 绑定 `0.0.0.0:port`，收到 `{"t":"discover"}` → 单播回 `{"t":"hello","name","os","ver","need_secret"}`（**不含任何密钥信息**）；启用 `IP_PKTINFO`（Linux/Windows）后用 `recvmsg` 取广播到达接口（Linux `ipi_spec_dst` 得接口 IP、Windows 8 字节 `in_pktinfo` 只有接口索引无 spec_dst），`sendmsg` 把回包源地址钉在该接口（Windows 用 `IP_UNICAST_IF`+`connect`+`getsockname` 由索引反查接口 IP，发送时 `ipi_addr` 字段填源地址），虚拟网卡/多网卡环境回包不会走错接口或带上虚拟适配器 IP；`recvmsg` 不可用或非 Linux/Windows 退化 `recvfrom`/`sendto`
- **TCP 握手（明文帧）**: `[4B 长度][JSON]`；服务端发 `challenge{nonce}` → 客户端回 `proof{HMAC-SHA256(secret, nonce)}` → 验 `ok`/`no`（3 次错误断开）；无密钥时直接放行
- **数据帧（加密）**: `[4B 长度][12B IV][AES-GCM 密文+16B tag]`；密钥由 PBKDF2(secret, 100000) 派生；JSON 载荷，命令由手机（客户端）发起
- **设备确认（连接前置）**: 客户端握手后发 `device_info` 帧（`{name,model,os,ver}`，手机 Build.MODEL/MANUFACTURER/versionName）；桌面端 `_cmd_device_info` 弹窗展示设备信息，用户允许/拒绝后回 `{ok, approved, allow_secret_config}`；**未确认期间其他命令挂起**（`confirmed` Event，等待超时 60s 后拒），无确认回调（测试/无 UI）默认放行；`confirm_device()` 由 JS 回传批准结果（`pending_confirm` 记录 + `threading.Event`）；WebUI 主窗口 `showLanDeviceConfirm()` 弹窗 → `JsApi.lan_confirm_device` 回传
- **命令**: `pull_manifest` / `push_manifest`（复用 sync 的 `_apply_remote_order`/`_apply_remote_collections`）/ `pull_file` / `push_file`（base64 传输）/ `get_config` / `send_config` / `device_info` / `ping`
- **配置同步（双向，电脑为权威源）**: `get_config` 由手机拉取、`send_config` 由手机推送；两端均剔除 `_SECRET_KEYS`（FTP/S3/R2/WebDAV 密码等）；设置页「允许密钥传输」开关（`lan_set_allow_secret_config`，仅内存生效）开启后配置同步才包含密钥字段，开启前弹窗警示「请勿在公共网络或不信任的网络进行此操作！」；`device_info` 确认响应携带 `allow_secret_config` bool，手机端据此动态显示密钥拉取/推送按钮
- **文件安全（与手机端对称）**: `_safe_fname` 拒绝路径穿越/绝对路径；`push_file` 四重校验：文件名安全 → 字节 ≤`MAX_FILE_SIZE`（64MB）→ 可选 `sha256` 与本地计算一致 → `_import_bytes` 先解码校验宽高>0 合法才写盘；**不合法字节绝不落盘**（杜绝孤儿缓存文件）；合法图片按 SHA-256 去重后存 `cache_dir/{hash[:16]}{ext}` 并入库
- **生命周期**: 设置页开关临时启动（重启默认关，不写入 config）；`lan_port`/`lan_secret` 持久化（`lan_secret` 加密存储）；`main.py` `shutdown()` 兜底 `lan.stop()`

### 本地 HTTP 安全加固
- Bottle 只绑 `127.0.0.1` 随机端口；`before_request` 校验 `Host` 必须为本机回环（`_host_allowed`），POST 额外校验 `Origin` 同源且 `Sec-Fetch-Site` 非 `cross-site`，拒绝则 403（阻断 DNS rebinding / 跨站注入）
- **多线程服务器**：`bottle.run` 通过 `server_class=_ThreadedWSGIServer`（`ThreadingMixIn + WSGIServer`，`daemon_threads=True`）启用多线程——Bottle 默认 wsgiref 单线程串行处理，慢请求（`/api/contributors` 外网抓取、`/api/thumb` 现场生成缩略图）会阻塞其他路由投递，导致设置页 `settings.js`/`settings.css` 排队、JS 监听未注册期间窗口可见但拖动/点击全部无效
- `/api/contributors` 结果带 1h TTL 缓存（模块级 `_CONTRIBUTORS_CACHE`，刷新在 `_CONTRIBUTORS_LOCK` 内单飞：**取锁后刷新时间并重查退避期**，等待中的并发请求不再重复抓取；刷新失败一律退避 60s 重试——有旧缓存回退旧缓存，无缓存冷启动失败返回 502，不再每个请求都反复触发 10s 慢抓取），设置页该图片加 `loading="lazy"`（位于默认隐藏的「关于」section，仅切换到该分组时才发起请求）；缩略图生成写盘为先写临时文件再 `os.replace` 原子替换（多线程下并发请求同一未缓存缩略图不再交错写产生永久损坏的缓存文件）
- `after_request` 统一加 `X-Content-Type-Options: nosniff` / `Referrer-Policy: no-referrer` / `X-Frame-Options: DENY`，`/api/` 路由 `Cache-Control: no-store`
- 文件名安全：`_safe_serve_filename`（webui）与 `_safe_remote_fname`（sync）拒绝含 `/` `\`、以 `.` `/` `\` `~` `..` 开头的名字；`_find_meme_file` 入口校验，远端 manifest 文件名在 `_fetch_remote_memes` 过滤 + `_pull_worker` 写盘前再防御
- 前端 XSS：`utils/api.ts` 的 `esc()`/`renderMarkdown()` 转义所有拼入 innerHTML 的外部/动态数据（远端分组名、GitHub 版本号、QQ 昵称、输出目录、弹窗标题/正文等）；设置窗口 `settings.js` 同理
- **键盘无障碍（主窗口）**：全局 `:focus-visible` 焦点环（2px `var(--primary)`）；meme 卡/文件夹卡 `role="button" tabindex="0"` + Enter/Space 复制或打开分组，标签栏 span 同理（`aria-pressed`）；标题栏图标按钮全部带 `aria-label`；弹窗焦点管理：`utils/api.ts` 的 `rememberFocus()`/`restoreFocus()`/`trapTabFocus()`（模块级 `_focusTarget` 记录打开前焦点、关闭归还、Tab 在弹窗内循环），InputDialog/TagEditor 打开聚焦输入框、ConfirmDialog 默认聚焦「取消」（危险操作需显式点「确定」）、CollectionBuilder 聚焦 `#cb-name`；`#meme-grid.sort-enabled .meme-card:focus-visible` 为 2px 实线焦点环
- **键盘无障碍（设置窗口）**：`settings.css` 全局 `:focus-visible` 焦点环（2px `var(--accent)`）；`.btn/.import-row/.nav-item/.title-btn:focus-visible` 用 box-shadow 双环、`.nav-item.danger:focus-visible` 红色环、`.check-row input[type="checkbox"]:focus-visible`；危险按钮统一 `.btn-danger-outline` 类（hover 变红 + 红色 focus 环，取代内联 `border-color:#ef4444`）；覆盖层焦点管理：`settings.js` 的 `rememberSettingsFocus()`/`restoreSettingsFocus()`/`trapSettingsFocus()`/`visibleSettingsOverlay()`（模块级 `_settingsFocusTarget` + `_SETTINGS_OVERLAY_IDS` 列表），各覆盖层（danger/sync 进度与完成/QQ/QQNT/TG/抖音/微信/上传确认/更新弹窗）打开时记住焦点、打开后聚焦首元素、关闭归还；全局 keydown 先对可见覆盖层做 Tab 循环陷阱，再按 Escape 依次关 danger→dy→tg→wechat→qq→qqnt→sync-progress→sync-done→关设置窗口；静态覆盖层 `role="dialog" aria-modal="true"`，`#toast` 加 `role="status" aria-live="polite"`（主窗口 `#toast` 同理）
- **设置窗口 UX**：保存模型—表单控件改动经 `initDirtyTracking()` 置 `_settingsDirty`，`closeSettings()` 变 async，脏时弹「有未保存的更改」确认再关（取消/×/Esc 均触发）；`saveSettings`/`getSettings`/`resetSettings` 成功后清脏；真正立即生效的控件（LAN 开关、密钥传输、存储位置「应用更改」）带 `.immediate-hint`「立即生效」标注；状态色收敛为 token：`--success: #22c55e`/`--danger: #ef4444`，JS 用 `setStatusColor()` 切 `.status-ok/.status-error` 类（不再写死 `#4caf50/#f44336`），HTML/CSS 内联色改 `var(--danger)`；复制处理下拉用 `.select-row`（label span + select），不再包 `.check-row`
- **关于页**：设置窗口左侧导航末项「关于」（`data-group="about"`）收纳从基础设置迁出的版本更新区块（`s-ver-current`/`btn-check-update`/`s-update-status`，逻辑不变）；页面含大号 OhMyMeme logo（`.about-logo`，span 用 `--accent`，复刻主窗口标题栏效果）、版本号+检查更新，及贡献者名单（`.about-contributors` 深色卡片）。贡献者头像不走外部直连：`/api/contributors` 路由（webui.py Bottle）用 urllib 抓取 `contributor.starsfire.top/TNTXZ/OhMyMeme` 的 SVG（该服务忽略 `?bg=` 参数且无 CORS 头，浏览器直接 fetch 会失败），用 `svg.replace` 剥离白色背景 `<rect>` 后以 `image/svg+xml` 返回，使圆形头像直接落在深色页面上；`onerror` 时隐藏图片并显示「贡献者名单加载失败」回退文案
- **对比度（H2）**：`--muted: #8a94a8`（在 bg/surface 上 ≥5:1）；实心主按钮/激活态文字/选中态 outline 用 `--primary-strong: #1d4ed8`（白色文字 6.7:1、`--primary-light` 背景文字 5.49:1）；`--primary #3b82f6` 仅用于 hover 高亮，不作小字/浅底文字色

### 环境检测
- WSL 检测: `/proc/version` 包含 "microsoft"
- WSL 时设置 `MESA_LOADER_DRIVER_OVERRIDE=llvmpipe`, `LIBGL_ALWAYS_SOFTWARE=1` 等软渲染环境变量

### 启动流程 (关键时序)
- **单实例互斥（防多开）**：`main()` 在 logging 配置后、`OhMyMemeApp()` 创建前调 `platform_util.acquire_single_instance()`——Windows 用 `CreateMutexW("OhMyMeme_SingleInstance")`（`GetLastError()==ERROR_ALREADY_EXISTS` 即已有实例，句柄存模块级 `_single_instance_handle` 防 GC，进程退出内核自动释放，崩溃安全），POSIX（Linux/macOS/WSL）对 `tempdir/ohmymeme-<uid>.lock` `fcntl.flock(LOCK_EX|LOCK_NB)`（fd 保持打开）；已有实例运行时 Windows 弹 MessageBoxW「OhMyMeme 已在运行」后 `sys.exit(0)`，其余平台仅日志退出；互斥机制自身异常一律返回 True 不阻塞正常启动。Bottle 端口随机分配不构成冲突防线，故必须显式互斥
- **源码运行自动编译前端**：`main.py` 启动时 `_ensure_vue_frontend()` 检查 `src/webui/dist/ohmymeme.js`，缺失（打包 `frozen` 或已有产物时跳过）则用 `npx.cmd`(Windows)/`npx`(其他) 跑 `vite build` 一次，失败仅告警不阻断启动
- **启动动画**：`App.vue` 挂载时播放 `src/resources/OhMyMeme.mp4`（通过 Bottle 路由 `/resources/<filepath:path>` 提供，`webui.py` 的 `RESOURCES_DIR`，basename 校验防路径穿越，路由须在兜底 `/` 之前注册；PyInstaller 以 `--add-data src/resources` 打包）；`onMounted` 设置 6s 兜底定时器 + `<video>` `@ended` 移除遮罩，`#startup-anim` 全屏遮罩 z-index 2000，`.startup-fade` 0.4s 淡出。**仅启动时播放**：快捷键/托盘仅 toggle 窗口显隐不重载页面，故不会重复播放。设置页「显示启动动画」开关（配置键 `show_startup_animation`，默认开，`useMemes` state 同步）控制：开启时 `loadInitData` 后立即 `startupVideoReady=true` 挂载视频并**并行加载**（无 300ms 延时，动画天然覆盖桥接稳定时间）；关闭时 `dismissStartupAnim()` + `setTimeout(..., 300)` 降级为 300ms 延时。`get_init_data`/`reset_settings`/`get_settings` 均透传该键。**遮罩背景贴合视频边框**：OhMyMeme.mp4 边框为纯黑，`webui.py` 写死 `_STARTUP_BG_COLOR = "#000000"`（不做运行时 ffmpeg 采样，避免影响启动速度），经 `get_init_data` 的 `startup_bg_color` 传给前端，`App.vue` 把该色同时应用到 `#startup-anim` 与 html/body 背景。**可跳过**：点击遮罩立即 `dismissStartupAnim()`；系统 `prefers-reduced-motion: reduce` 时直接跳过动画走 300ms 降级路径（`window.matchMedia` 检测）
- Vue `App.vue` 挂载后:
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
- **感知哈希相似去重**（`download_original_image` 单图导入路径）：`memes.perceptual_hash` 列（TEXT 存 16 进制，旧库自动 ALTER 迁移）持久化每张图的 64 位感知哈希（`_perceptual_hash`，8x8 可分离 DCT pHash，比均值哈希对浅色/低信息图判别力更强）。导入时哈希未命中则 `_find_similar_candidates` **只算新图 phash + 从 DB 读存量 phash 比对**（整数 XOR，微秒级），`perceptual_hash` 为空的旧库行惰性回填：缺失 ≤`_PHASH_SYNC_BACKFILL_MAX`(5) 同步回填，超过则丢后台线程（`_PHASH_BACKFILLING` 防重入，start 异常复位），本次只比对已填的。`_build_cache_index` 一次性构建文件索引避免逐行 walk（仅在有缺失时执行）。汉明距离 `_PHASH_SIMILAR_DIST<=12` 视为近似，**全库比对无截断漏检**。命中候选时将文件复制到独立临时文件登记 `_PENDING_SIMILAR`（token 随机、TTL 300s 过期时在 pop/next-register 时删除临时文件），返回 `similar_pending`，前端 `SimilarImportDialog` 弹窗让用户选：保留新图 / 保留旧图 / 跳过（discard）/ 都保留（keep_both），经 `JsApi.resolve_similar_import(token, action)` 决定导入或放弃。哈希精确命中返回 `duplicate` 提示「已存在」。`_do_import`/`scan_cache` 新建时写入 `perceptual_hash`（`add_meme` 内部转 hex，规避 64 位溢出 SQLite INTEGER）。两条单图交互路径都启用：`download_original_image`（URL 拖放/下载原图）与 `/api/upload/`（File 拖放，单张时走 `_import_with_similar_decision`）；批量路径（多文件拖放/文件夹/同步 pull/LAN）不做感知去重（多文件走 `_do_import` 避免逐个打断）。`_do_import` 去重关键区（`get_by_hash`检查→copy2→`add_meme`→回查）由模块级 `_IMPORT_LOCK` 串行化：并发拖入完全相同字节的图也不产生重复记录（测试 `test_import_concurrency.py`）
- **原件与日常副本**：`config.py` 的 `_IMPORT_MAX_BYTES=200MiB` 是原件硬上限，`_IMPORT_MAX_PX=2560` 仍限制最长边；LAN 还受协议 `MAX_FILE_SIZE=64MiB` 限制。`media_derivative.py` 的 `create_daily_copy()` 在导入时生成可再生副本：静态图→WebP（最长边 1600、质量 84 起步并按 8MiB 软目标递降），GIF→GIF，动画 WebP→动画 WebP；保留透明通道、帧时长和循环。超大 JPEG 先由 `_jpeg_dimensions` 有界读取 SOF，像素数不超过 `LARGE_JPEG_MAX_PIXELS=300_000_000` 时临时调高 Pillow 阈值并立即恢复，然后在解码前用 `draft()` 原生降采样；超限仍拒绝，其他格式不放宽。仅当原件已在 1600px/8MiB 范围内且重编码更大时，副本目录才复制原格式字节；超出任一日常限制时始终采用重编码结果。生成和校验成功后原子落盘，再与原件路径一起写 DB；失败回滚本次原件/副本。旧数据、扫描/同步/恢复数据由 `WebUI._find_daily_file` 首次使用时惰性生成，`daily_profile` 控制编码版本失效。网格动画、`/api/original` 详情预览、普通复制、`get_meme_path(s)` 与原生拖拽使用副本；右键「复制原件」调用 `copy_meme_original` 绕过副本和 `copy_resize_mode`。缩略图和日常副本均持久化且没有容量/LRU 自动淘汰（`cache_max_size_mb` 当前未用于清理）；删除表情会清理对应缓存，清空本地数据会整体清理。原件改名不改哈希命名的副本
- **虚拟盘后续页预热**：`bulk_read_prefetch_pages` 仅在 `bulk_read_protection` 开启时生效；`useMemes.search/loadInitData` 每次把当前关键词、结构化标签、分组、类别、类型、当前 offset/page size/total 传给 `JsApi.start_page_cache_warmup`。新调用递增 generation 取消旧查询任务；worker 跳过当前页，从后续 meme offset 分块调用无图片探测的 `_search_meme_rows`，逐项 `_get_thumbnail_path`，从而按需生成持久化日常副本与缩略图。启动延迟约 1 秒并在每项后短暂 yield，减少与当前页请求竞争；不创建前端 Image/DOM 缓存，不长期占用整库解码内存
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

### 剪贴板 (GIF/WebP 直接传送)
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
- **复制处理模式** — config `copy_resize_mode`（0不处理；1webp缩放，默认；2转gif；3转gif隐写原图），仅设置页「复制处理」下拉选择（无主窗口开关）。复制超过 `copy_resize_max`（默认 200px）的静态图时按模式处理（动图 GIF/动画 WebP 不受影响）：`convert_image_mode_1` → `_resize_static_to_webp` 转 WebP 并**缩放到限制内**（唯一缩放原图的模式）；`convert_image_mode_2` → `_static_to_gif` 按**原分辨率**转普通 GIF（不隐写、不缩放）；`convert_image_mode_3` → `_make_stego_gif` 按原分辨率转隐写 GIF（失败原样复制原图，不回退缩放）。处理结果存系统临时目录（**不删除**，CF_HDROP 需在 QQ 粘贴时仍可读取）：`ohmm_resize_<md5>_<max>_q<质量>_v<版本>.webp` / `ohmm_gif_<md5>_v<版本>.gif` / `ohmm_stego_<md5>_v1.gif`；缓存键含编码参数与版本号，改编码逻辑后旧缓存自动失效，命中时校验完整性，同一表情重复复制复用。旧配置迁移：`experimental_stego=true` → mode 3，`copy_resize_enabled=false` → mode 0（仅当旧配置无 `copy_resize_mode` 键时）
- **GIF 隐写（复制模式 3 + 导入自动解码）** — ①复制输出：`copy_resize_mode=3` 时 `_make_stego_gif` **懒加载** `gif_stego.make_stego_gif` 生成携带无损原图的隐写 GIF（与原图同分辨率）再复制，失败原样复制原图；缓存 `ohmm_stego_<md5>_v1.gif`（不删除，CF_HDROP 需存在）。②导入含 `STG3` 的 GIF 时**无论模式与否**都会自动解码，且**只入库还原的原图**（`_try_decode_stego` 解码到临时文件 → 原图正常入库，`from_stego=1`，载体 GIF 不入库、不进缓存目录）。③隐写缓存：复制原图时通过临时缓存 `ohmm_stego_<md5>_v1.gif` 复用（命中即校验，不再重新编码）；`memes.stego_of_hash` 字段与 `get_by_stego_of` 保留用于兼容旧库中已入库的载体行。④前端展示：隐写载体在查询层隐藏（`search`/`count`/`get_recent` 统一加 `stego_of_hash IS NULL` 过滤，仅对旧库残留载体行生效），网格只显示还原后的原图；原图行 `from_stego=1`（`memes.from_stego` 列），卡片正常渲染图像并叠加琥珀色「隐写导入」徽标。⑤本地生成（复制路径）的隐写文件不写入 DB/不同步。`src/gif_stego.py` 支持 `encode`/`decode`/`make_stego_gif`/CLI，`quiet=True` 供应用调用

### 加密降级 (crypto_util)
- 优先 `cryptography.fernet.Fernet` (AES-128-CBC + HMAC)
- 降级: `hashlib.pbkdf2_hmac` 派生密钥 + XOR + base64（防意外泄露，不防专业破解）
- **不能移除 XOR 降级** — 无 `cryptography` 时系统崩溃

### Sync pull 集合合并
- `_apply_remote_collections` 以**并集**方式合并远端分组，不清除本地已有成员
- 远端 manifest 中的 `collections` 用文件名关联（非 ID），跨设备稳定
- `categories` 以名称树和每个 meme 的 `category_path` 同步，pull 会创建空类别，并把下载/已有原件移动到类别目录；远端图片文件仍保持 `memes/<filename>` 平铺以兼容旧后端。
- `_apply_remote_order` 按远端 manifest 的 `memes` 顺序重排本地 `sort_order`（`reorder_memes`），确保 pull 后本地显示顺序与云端一致，再次 push 不致覆盖云端排序

### 排序同步闭环
- 排序相关的 `reorder_memes`/`reorder_collections`/`reorder_collection_members` 更新 DB 后即调 `build_manifest()`，本地 `meme-index.json` 保持最新
- push：末尾 `build_manifest()` 按 DB 当前 `sort_order` 重建并上传，云端 manifest 顺序反映本地排序
- pull：`_apply_remote_order` 按云端 manifest 顺序回写本地 `sort_order`，实现双向闭环

### Manifest
- `build()` 递归遍历嵌套分组树，空分组自动 `delete_collection`
- 远端 manifest 中的 `collections` 以嵌套格式存储（`name`/`filenames`/`children`），version 2 旧格式启动时自动转换
- version 4 额外保存 `categories` 名称树、`category_path` 与本地 `storage_path`；旧 version 3 清单仍可读取，缺省类别按根目录处理。

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
- **拖拽到外部应用**：关闭拖拽排序后 meme 卡**不用 HTML5 拖拽**（WebView2 http 源的 `text/uri-list`/`DownloadURL` 不生成 CF_HDROP，QQ/微信会报"图片拖拽失败"或资源管理器无反应）；改用 **WinForms 原生文件拖拽**（`native_drag.py`）：`pointerdown` 记录起点 → `pointermove` 位移 >8px 时 `JsApi.start_native_drag(id)` → 后端用 `webview.windows[0].native`（主 Form）`Invoke` 在 UI 线程执行 `DoDragDrop`（`DataObject` + `DataFormats.FileDrop` → CF_HDROP）→ 拖到 QQ/微信/桌面是真实本地文件；`DoDragDrop` 返回 `DragDropEffects.None`（拖回取消）时 `start_native_drag` 返回 False，不触发 `schedule_hide`；`native_drag.py` 懒加载 pythonnet/WinForms，非 Windows 或无 .NET 时返回 False，JS 端 toast 提示；**原生拖拽进行中回拖到窗口**用全局 `nativeDragActive` 标志抑制 drop 导入处理器（dragenter/dragover/dragleave/drop 均忽略，视为取消，不弹导入浮层）；`nativeDragActive` 在 `pointermove` 位移 >8px 触发原生拖拽**前**置 true，`start_native_drag` Promise `.then/.catch` 中重置，拖拽期间（后端 `DoDragDrop` 阻塞 UI 线程）保持 true，确保拖回窗口不会误触发导入
- 排序拖拽与原生拖拽共用 `onCardPointerDown`/`onDocPointerMove` pointer 事件：`onCardPointerDown` 按 `sortEnabled && canReorder()` 决定走 `drag.onPointerDown`（排序）还是记录 `nativeDragStart`（原生拖拽），`onDocPointerMove` 按 `drag.dragState.memeId` 是否存在分支，`onDocPointerUp`/`onDocPointerCancel` 对原生拖拽仅清 `nativeDragStart` 跳过排序回滚
- `search()` 带 `collection_id` 时按 `meme_collections.sort_order ASC, m.updated_at DESC` 排序（子查询取该 meme 在目标分组内的 sort_order）
- 拖拽后通过 `ignoreClick` 抑制误触发的 `click`（防止误复制），下一次 `pointerdown` 时重置

### 多级分组（最多 3 层）
- `collections.parent_id` 自引用实现嵌套
- `create_subcollection(name, parent_id)` 自动检查深度（`get_collection_depth`），超出 2 层拒绝
- **分组名自然排序回退**：`MemeDB.get_collections`/`get_child_collections` 仅按 `sort_order ASC` 查询后用 Python `_name_sort_key` 稳定排序——`sort_order` 相同（未拖拽）时数字段按整数（1,2,10 而非 1,10,2）、中文按拼音（`pypinyin` 惰性导入，缺失时退回原名小写码点序），替代原 SQL `ORDER BY name` 的二进制码点序
- 顶层分组在 `#colbar` 渲染为 tab，选中后展开子分组
- `#tagbar`/`#colbar` 横向溢出：细滚动条可见（`scrollbar-width: thin` + 5px webkit 样式），`initHScroll(barId)` 把滚轮竖向增量转成 `scrollLeft`（按 `deltaMode` 归一化），`DOMContentLoaded` 时对两个栏各绑定一次
- 分组内右键空白区域 → 新建子分组
- 右键表情包 → 加入分组 → 弹窗列出当前大分组下的子分组

### 主窗口 UI/UX
- **统一内容库与体裁**：`useMemes` 通过 `JsApi.search_library` 统一分页，状态分存 `memes` 与 `chatRecords`；`activeGenre` 为 `all|image|gif|chat_record`。`#tagbar` 首行「体裁」是不可编辑系统筛选（不进入 tags/meme_tags、TagEditor 或 MemeDetails），普通分组/类别/标签筛选会自然排除聊天记录；切到聊天记录时主动清空这些图片专属筛选。`all` 分页中聊天记录按更新时间置前，剩余名额再接图片。聊天记录卡点击打开 `ChatRecordDetails`，显示来源/统计/扁平消息预览，并可复制 `/omm send <ID>`；聊天记录参与网格时禁用图片拖拽排序
- **后台入库即时刷新**：`library_events` 用弱引用保存当前 `WebUI` 回调；NapCat 归档与 LAN 推送完成后发布内容变化事件。`WebUI` 在 120ms 内合并连续通知并调用前端 `window.refreshLibrary`，只重新执行当前筛选并回到第一页，不整页 reload、不重新扫描图库
- **表情包详情**：右键菜单「查看表情包详情」或在非整理/非多选模式下长按卡片 500ms 打开 `MemeDetails`；长按期间移动超过 8px 即取消并继续原生拖拽，成功触发后屏蔽该次 click/contextmenu，绝不复制到剪贴板。弹窗使用 `/api/original`（历史路由名，实际返回日常副本）在左侧按原始比例 `object-fit: contain` 大图展示；图片框 hover 显示前后按钮，非输入控件焦点下 ←/→ 按全图库稳定的 `sort_order ASC, id DESC` 顺序切图（详情编辑更新 `updated_at` 时不会跳序），首尾禁用。右侧明确标注并可编辑标题和文件名（文件名同步磁盘原件，禁止改扩展名/路径/保留名并检查冲突）；所属分组显示所有直接归属的完整层级路径，可移除或通过项目风格的搜索下拉框添加已有分组，支持多分组且不移动磁盘文件。搜索名称没有精确命中时，详情可调用 `create_collection`/`create_category` 就地创建顶层或子节点并立即选中；分组只允许顶层加一层小分组，类别可选择任意既有类别作为父节点。结构节点创建后立即持久化并刷新侧边栏，但当前表情的分组关系或类别归属仍只在显式保存/切图保存时由 `update_meme_details` 写入。详情也可添加/移除/全局编辑标签，并分别编辑人工/AI 图片描述及人工/AI 图片文字，不适用的一侧可留空以明确来源。已选标签与自绘建议下拉层均提供编辑入口，可同时修改名称和种类，修改影响所有表情，目标标签已存在时自动合并。标签及分组建议样式与项目分组选择器一致（不用系统 `datalist`），支持搜索、鼠标选择及 ↑/↓/Enter。显式保存与切图前保存走 `update_meme_details`，失败则保留当前页面；纯标题变化调用 `manifest.update_meme_name` 原子更新本地清单，不访问原件，只有文件名/类别/分组结构变化或增量更新无法定位条目时才完整重建 manifest。关闭、遮罩点击与 Esc 直接放弃未保存修改。支持 Ctrl/Cmd+S 保存、Tab 焦点循环及关闭后焦点归还
- **折叠侧边栏分组可辨识**：`CollectionTreeNode.vue` 在折叠态（`collapsed`）以 `.tree-avatar`（26px 圆角块，取分组名首 1-2 字符，`avatarText` computed）替代统一文件夹图标，active 行高亮；展开态保持原图标
- **功能发现性**：meme 卡左上 `.fav-btn` 心形快捷收藏（hover/active/focus-visible 显示，selectMode/sortEnabled 时隐藏，`@click.stop`+`@pointerdown.stop`，调 `JsApi.toggle_favorite` 并本地翻转 `meme.favorited` 后 `refreshCollections`）；侧边栏树行 hover 显示 `.tree-more`「⋯」按钮（展开态，`@click.stop` 发 folder-context 复用右键菜单）
- **空状态**：`#empty` 从 kaomoji 改为 SVG 插画（`.empty-svg`）+「导入表情包」按钮（`showImportMenu`）
- **搜索清除**：搜索框右侧 `.search-clear`「×」按钮（`v-if="state.searchQuery"`，点击 `clearSearch()` 清空并 `search()`），输入框 `padding-right:32px` 防文字被按钮遮挡
- **垂直空间压缩**：titlebar 38px、`#search-wrap` padding 6px 12px、`#search` padding 7px 12px、`#tagbar` padding 4px 12px + max-height 52px、`#breadcrumb` padding 2px 12px、`#grid-wrap` padding 10px、pager padding 5px 10px
- **标题栏统一**：主/设置窗口关闭按钮均为 `×`；拖拽排序图标为上下箭头（非汉堡线）
- **右键子菜单点击展开**：`ContextMenu.vue` 的「加入分组/新建子分组」从 hover 触发改为 click 切换（`onItemClick`，展开时锚定点击项右缘 `getBoundingClientRect().right + 4`，再次点击 `hide-submenu` 收起），保留越界 clamp，`has-submenu` 项显示 `▸` 指示符
- **加入分组任意视图可用**：表情右键「加入分组」不再要求处于分组视图，`onShowSubmenu` 列出全部分组树（顶层直显、子分组带「父/子」路径扁平列表，子菜单仅一层故扁平化）；只要存在可选分组，`ContextMenu` 就在子菜单顶部显示固定搜索框，按完整路径即时过滤，Enter 选择首个结果，Esc 先清空再关闭，选项区独立滚动。顶部保留「新建分组/新建小分组」（分组视图内在当前分组下创建，其他视图创建顶层分组）；文件夹右键分支仅保留「新建子分组」单项，在**右键的分组**（`trigger.folderId`）下创建（不再依赖 `t.memeId` 的 `subgroup-*` 项与 `activeCollection` 目标）
- **CollectionBuilder 分组下拉搜索**：`collectionOptions` 由分组树扁平化（含子分组，label 带「父/子」路径），`filteredCollectionOptions` computed 按 `#cb-name` 输入词过滤（匹配组名或父路径），无匹配显示「无匹配分组」；`createNew` 点击「创建新分组」后保留已输入名称不再清空；`selectCollection` 的 `selectedName` 与输入框显示值保持一致避免 watch 误判手动改名

### 未分组（虚拟分组）
- **`collection_id = -4`** 标识「未分组」虚拟分组：展示未加入任何分组的表情包（`meme_collections` 无记录），**不写入 DB/manifest，动态生成**
- `MemeDB.search()/count()` 新增 `uncategorized_only` 参数（`NOT EXISTS` 于 `meme_collections`），`search_memes` 中 `collection_id == -4` 路由到该参数，同时过滤隐写载体
- `get_init_data`/`get_collections` 按配置 `show_uncategorized`（默认开）决定是否追加 `-4` 条目（`get_collections` 中放于 `-2`/`-3` 之后）；设置页「分组显示 → 显示未分组分组」开关（`s-show-uncategorized`），保存到 `save_settings` 的 `show_uncategorized`
- 前端走通用集合渲染路径：计数为 0 时自动隐藏（`renderCollections` 的 `count === 0` 过滤）；可拖拽排序（复用全局 `sort_order`，走 `reorder_memes` 持久化，`canReorderMemes` 对 -4 返回 true）；无特殊右键菜单
- 未分组集合内的删除/加入分组等操作经 `refreshCollections` 后计数自动刷新，全部归类后 `-4` 从标签栏消失

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
- **webm 转换** (`convert_webm_to_webp`): 默认开启，ffmpeg 将 webm 转 animated webp，**有损 q80**（`-lossless 0 -quality 80`，无损编码实测 11-35s/个过慢改用有损，贴纸场景质量几乎无损），`-loop 1` 循环播放，保持宽高比（最长边 512，不放大），删除原 webm；转换前 `_check_ffmpeg()` 预检，缺失时报 `error_code="no_ffmpeg"` 中止；**单个转换失败的文件跳过不导入**（`convert_failed` 计数并在完成消息提示）。**并行转换**：`_tg_worker` 内用 `ThreadPoolExecutor(max_workers=min(os.cpu_count(), 4))` 受控并发，实测单张 ~1.7s → 4 路约 2.9x 加速（千张 30 分 → ~11 分）；进度 `done/convert_failed` 在 `_TG_LOCK` 内原子累加，非 webm（webp）直接透传并计入进度；进度条按 `done/total`（total=全部待处理含 webp）平滑递增。**取消/回收**：`convert_webm_to_webp` 用 `Popen`+有界 `communicate(timeout)`，受 `_TG_ACTIVE_PROC` 集合（`_TG_LOCK` 保护）跟踪活动进程；`cancel_tg_import()` / `_reset_state()` 对所有运行中进程 `terminate()`；`_reap_proc()` 统一回收——kill 后无条件 `wait`（纠 zombie），wait 超时二次 SIGKILL 兜底，**仅进程实际退出（`poll()` 非 None）才从 `_TG_ACTIVE_PROC` 移除**；转换循环 `finally` 用 `executor.shutdown(wait=True, cancel_futures=True)` 等待已启动 future 退出，避免与 temp_dir 清理/下次导入交错。**ETA 用 `time.monotonic()`**（`_TG_T0` 起点与计算同单调时钟）。**防双 worker**：`start_tg_import` 在 `_TG_LOCK` 内检查后立即 `_update_tg(status="scanning")` 占位运行态，线程在锁外创建，并发第二次调用在锁内见 scanning 即返回 False
- **静态版去重** (`dedup_static_against_animated`): Telegram 对同一动态贴纸缓存两份（512 webm 动画 + 320 webp 静态版），入库前用 PIL 识别动画 webp（`n_frames>1`），将每个静态 webp 与所有动画 webp 首帧做**归一化灰度差分**（白底合成 32×32，阈值 diff<0.02，实测匹配组 0.002-0.005 / 非匹配组 >0.1 间隔安全），内容一致的静态版跳过只保留动画版；无动画或 PIL 缺失时原样返回；`.webm` 文件（未转换时）不受影响。实测 127 静态中 45 个被判重跳过，0 误杀 0 漏跳，全量比较耗时 ~2s
- **进度状态** (`_TG_STATE`): `idle` → `scanning` → `loading_key` → `decrypting` → `converting` → `importing` → `done`/`error`/`cancelled`，含 `error_code` 字段，前端 300ms 轮询 `get_tg_import_progress()`。**ETA 展示**：`_TG_STATE["elapsed_s"]` 由 worker 起点 `_TG_T0` 计算，`_refresh_tg_elapsed()` 在 `_update_tg`/`get_tg_progress` 内按运行中状态推进（idle/结束不推进）；前端 `settings.js` 的 `updateTgEta()` 据 `elapsed_s` + `progress` 线性外推「已用 X · 预计剩余 Y」（`formatDuration()` 折算分钟/秒），`tg-import-eta` 元素随轮询刷新、overlay 打开时清空
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
- **架构**: 独立 C++ 二进制 `wechat_keyfinder` 处理 Windows 进程内存取证（读取微信进程内存提取密钥），Python 侧通过 subprocess + JSON 协议协调完成 DB 解密/SQLite 查询/CDN 下载/入库；仅 Windows
- **目录层级**: 微信文件目录（root，默认 `%USERPROFILE%\Documents\xwechat_files` 或 `\WeChat Files`）→ 账号目录（root 下 `wxid_*` 文件夹，每个微信账号一个）→ `db_storage/emoticon/emoticon.db`（表情库，加密）+ `db_storage/favorite/favorite.db`（收藏库）
- **环境检测** (`inspect_wechat_environment`): 传入路径 basename 以 `wxid_` 开头或 `_find_emoticon_db` 命中则视为单账号，否则扫描子目录收集 `wxid_*` 前缀或含表情库的账号目录；每账号 `_inspect_account` 检查 DB 是否存在且为 SQLite header（否则 `encrypted_index`）；返回 `{status, reason, root, root_exists, account_directory_count, accounts: [{id, path, status, reason, db_path}]}`
- **账号选择**: `_pick_account` 未指定且多账号时返回 None，调用方报 `multiple_accounts` 引导前端选择；`list_wechat_stickers`/`start_wechat_import`/`_wechat_worker` 支持 `account_path` 参数指定账号
- **密钥提取**: 二进制扫描微信进程内存，通过特征码定位密钥对象（RVA 偏移在 `config/offsets.json` 配置），XOR 解码 + salt 比对 + HMAC-SHA512 校验。`offsets.json` 由 `build.py` `--add-data` 打包进产物 `config/` 目录（冻结后 `_offsets_path` 解析到 `_internal/config/offsets.json`，缺失时 helper 报 `config_invalid`）。**掩码恢复为主路径**（`find_wechat_key_masked`）：利用 DB 前 16 字节 salt 反推 32 字节 XOR 掩码，按 `x'<96hex>'` 格式识别被掩码的 99 字节密钥缓冲，**无需 RVA**，微信升级不易失效；旧 RVA 特征码扫描仅作回退；`--key <hex64>` 可注入已验证密钥绕开取证。**多进程**：未指定 `--pid` 时枚举所有 `Weixin.exe` 逐个尝试，掩码恢复天然命中运行目标账号的进程（`key_not_found` 表示均未命中）
- **账号目录识别不依赖 `wxid_` 前缀**：`_find_account_dirs` 收集「`wxid_` 前缀子目录 ∪ `_find_emoticon_db` 命中（`db_storage/emoticon/emoticon.db` 或 `Msg/emoticon.db` 存在）的子目录」；`_find_emoticon_db` 两个固定候选未命中时在 `db_storage` 一层内兜底查 `emoticon.db`（`db_storage/emoticon.db` 或 `db_storage/*/emoticon.db`，覆盖布局差异）；`inspect_wechat_environment` 对用户所选目录本身同理放宽（前缀或含表情库即视为单账号目录）。`_inspect_account` 在 db 缺失时附带 `db_files`（`db_storage` 内实际存在的 `.db` 清单，有界 30 条），前端 `no_database` 时翻译为中文提示并展示该清单用于诊断真实布局；检测到微信 3.x 旧版布局（账号目录含 `Msg/Multi` 或 `Msg/MicroMsg.db`，表情库为 `Msg/Emotion.db`）时返回 `unsupported_version`/`wechat_3x_unsupported`，前端引导升级微信 4.x（Emotion.db 的 `CustomEmotion` 表、3.x SQLCipher 页布局与密钥内存格式均与 4.x 不同，整条链路不支持）。测试 `tests/test_wechat_env.py`
- **DB 解密** (`_decrypt_database`): AES-256-CBC 逐页解密（每页 4096 字节，页 1 带 16 字节偏移，IV 取页尾 80 字节偏移处），首页替换为 "SQLite format 3" header；**合并 WAL**（`_apply_wal`）：微信运行中表结构与记录在 `emoticon.db-wal` 里，按 WAL 帧（24B 头 + 4096B 加密页）解密并回写到对应页，主文件旧快照 + WAL 帧 = 完整数据
- **元数据查询** (`_query_sticker_metadata`): SQLite 查询 `kNonStoreEmoticonTable`（type/md5/aes_key/cdn_url/encrypt_url/extern_url），返回 md5+url+aes_key 列表
- **下载校验** (`_download_sticker`): urllib 下载 → 魔数识别扩展名（PNG/JPG/GIF/WebP/BMP）→ 带 `aes_key` 时 AES-128-CBC 解密（IV=key）；`_detect_image_ext` 校验合法才返回。**防 SSRF**：仅允许白名单 CDN 主机（`vweixinf.tc.qq.com`/`wxapp.tc.qq.com`），解析后拒绝回环/私网/链路本地地址，重定向逐目标复检
- **完整性校验**: `verify_binary_integrity` 执行前校验二进制 SHA-256，未配置真实哈希（占位符 `PLACEHOLDER_UPDATE_ON_RELEASE`）时**默认拒绝执行**；开发/本地测试可用环境变量 `OHMYMEME_INSECURE_SKIP_HELPER_HASH=1` 跳过。**发布前必须**用 `certutil -hashfile wechat_keyfinder.exe SHA256` 计算真实哈希填入 `_WECHAT_KEYFINDER_SHA256` 并移除占位符
- **前端 UI**: 设置页「导入」分组下 `.import-row` 列表行（硬编码 SVG 图标 + 名称），点击微信行弹出对话框（目录选择 + 环境检测 + 多账号下拉 → 进度覆盖层）

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
python scripts/build.py --lang en  # 指定语言构建
```
- **构建自动编译 Vue 前端**: `build.py` 的 `ensure_vue_frontend()` 在打包前检查 `src/webui/dist/ohmymeme.js`（被 gitignore，CI 全新检出缺失），缺失时自动 `npm ci`（有 lockfile，否则 `npm install`）→ `npx vite build`，失败则中止构建；构建机需 node/npm（GitHub Actions runner 预装），dist 已存在时直接跳过
- **Linux 打包（GTK）**: `--linux` 时 `build.py` 自动传 `--additional-hooks-dir scripts/hooks`（收集 WebKit2/Soup typelib）并 `--collect-all gi`，把 PyGObject/GTK 打进产物，脱离系统 python3-gi 运行；构建机需装 `python3-gi gir1.2-webkit2-4.1 libgirepository1.0-dev libgirepository-2.0-dev gobject-introspection` 并 `pip install PyGObject`（对应 `build.yml`/`nightly.yml` build-linux job）；**PyGObject ≥3.52 硬依赖 girepository-2.0**（Ubuntu 24.04 对应 `libgirepository-2.0-dev`），只装 1.0-dev 会在 meson 元数据阶段报 `Dependency 'girepository-2.0' is required but not found`；deb `Depends: python3-gi, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0`

**前端架构**：主窗口为 Vue 3（`vue-src/`，Vite 构建 IIFE 单文件），设置窗口仍为 vanilla（`webui/settings.*`，独立 webview）。修改主窗口前端后需 `npx vite build` 再运行。

`make` 命令仅供参考（`make run`/`make test`/`make lint`/`make format`/`make build`），macOS/Linux 下可能不可用，优先使用原生 Python 命令。

## CI (GitHub Actions) — 三个独立 workflow
- **check.yml**: Ubuntu, lint + test, push 和 PR 到任意分支均触发
- **build.yml**: Windows + Linux + macOS 三平台，仅在 `check` 通过 main 分支后自动触发，也支持 `workflow_dispatch` 手动触发
  - `build-windows`: InnoSetup 安装包 `dist/OhMyMeme-*-setup.exe`
  - `build-linux`: AppImage/deb/rpm（`--linux`）
  - `build-macos`: `.app` + `.dmg`（`--macos`，PyInstaller `--windowed` + iconutil 生成 icns）；矩阵双架构 `arm64`（macos-latest）+ `x86_64`（macos-15-intel），产物 `OhMyMeme-v*-{arch}.dmg`
- **nightly.yml**: Windows + Linux + macOS 三平台每日定时（UTC 20:00）+ `workflow_dispatch`，从 `dev` 分支构建非正式版（`--nightly`，版本号为 `nightly`）并发布为 `nightly` prerelease；`updater.py` 的 `_parse_release` 跳过 prerelease 与含 `nightly` 的 tag，**软件更新绝不会指向 nightly**
- 上传 `dist/OhMyMeme-*-setup.exe` / `dist/OhMyMeme-v*-x86_64.AppImage` 等作为 artifact

## 版本管理
- 版本号唯一来源: `src/__init__.py` → `__version__ = "*.*.*"`
- `scripts/build.py` 用正则从该文件提取版本；`--version`/`--nightly` 会临时改写 `__init__.py` 构建，完成后恢复
