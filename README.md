# OhMyMeme

轻量化跨平台表情包管理系统 — 突破表情包上限，快捷键呼出、搜索即复制。

### **QQ交流群：891636253**

![picture](https://raw.githubusercontent.com/OhMyMeme/OhMyMeme/refs/heads/dev/resource/picture.gif)

## 功能

- **系统托盘运行** — 最小化资源占用，后台常驻
- **单实例运行** — 重复启动时自动检测已有实例（Windows 命名 mutex / POSIX 锁文件），Windows 弹窗提示后退出、其他平台静默退出，避免多开引起的数据冲突
- **全局快捷键** — 默认 `Ctrl+Alt+N` 呼出/隐藏主面板；Windows 可选在鼠标所在屏幕显示隐藏的主面板，默认关闭。内置运行期自愈：回调异常不会拖垮键盘监听线程，守护线程周期性检测监听线程存活，Windows 下另以无害 F15 探针（每 30s）验证键盘钩子未被系统静默移除，任一失效自动重启监听并重新注册，无需重启软件；热键相关事件（注册/异常/线程死亡/自愈）会追加到 `data_dir/hotkey.log` 便于排查
- **表情管理** — 导入/搜索/标签分类/收藏/自定义分组与单归属类别
- **一键复制** — 点击表情包自动复制日常副本到剪贴板（GIF 保留动画）；右键「复制原件」可复制未经日常压缩的文件。Windows 可选在全局快捷键呼出后安全尝试粘贴回原窗口。仅由全局快捷键从隐藏状态呼出的主窗口，会在复制成功后自动隐藏，普通窗口、托盘呼出及复制失败时保持可见
- **批量操作** — 多选模式下支持批量加入分组/移动到分组/打标签/删除；批量打标签为合并追加，不清空各表情已有标签
- **原生文件拖拽** — 关闭拖拽排序后可将日常副本拖到外部应用；仅由全局快捷键从隐藏状态呼出的主窗口，会在拖拽成功后自动隐藏，普通窗口、托盘呼出及拖拽失败时保持可见
- **自适应日常副本** — 导入时完整保留原件，同时生成用于预览、普通复制和拖拽的副本：静态图转 WebP，GIF 保持 GIF，动画 WebP 保持 WebP；最长边收敛到 1600px并保留透明通道和动画。超大 JPEG 会在 3 亿像素硬上限内先由 JPEG 解码器降采样，避免完整展开到内存。旧数据或恢复/同步得到的原件会在首次使用时自动补建副本
- **复制处理** — 设置页四种模式（不处理 / WebP 缩放 / 转 GIF / 转 GIF 隐写原图）：以日常副本为输入，对超过设置尺寸（默认 200px）的静态图继续按所选模式处理；右键「复制原件」始终绕过这些处理。导入含隐写的 GIF 会自动解码并只入库还原后的原图，载体 GIF 不入库
- **拖放导入** — 直接拖拽图片到窗口即可导入
- **文件夹导入** — 一键导入整个文件夹的图片，可选自动创建同名分组
- **表情包详情** — 右键选择「查看表情包详情」或长按卡片打开；左侧用日常副本按原始比例大图预览，悬停可用左右按钮浏览全图库，也支持 ←/→ 快捷键；右侧明确显示并可编辑标题、文件名、单归属类别（可选择多级类别）、所属分组（显示完整层级路径，可搜索添加或移除）、标签，以及彼此独立的人工/AI 图片描述和人工/AI 图片文字。搜索不存在的分组或类别名称时可就地创建顶层或子节点并选中；节点创建立即生效，但当前表情的归属仍需点击「保存更改」才会写入，文件名或类别保存时同步移动实际原件（扩展名保持不变）。升级前已有的描述与文字保留在 AI 字段，新增的人工字段默认留空；切图前会尝试保存，关闭、Esc 或点击遮罩会放弃未保存修改，长按触发后不会复制到剪贴板
- **右键菜单** — 查看表情包详情/复制原件/重命名/收藏/打标签/添加分组/加入分组（任意视图可用，子菜单列出全部分组树，顶部可即时搜索完整分组路径）/从分组移除/删除；表情卡悬停显示快捷收藏按钮，侧边栏分组悬停显示「⋯」快速操作入口
- **设置界面** — 表单改动未保存时关闭会提示确认；局域网开关/密钥传输/存储位置等立即生效的选项带「立即生效」标注
- **分类标签、体裁与搜索** — 标签按种类分组展示和筛选，同名标签可存在于不同种类；表情包详情与右键「打标签」均可保留或指定标签种类，详情中的编辑按钮可全局修改现有标签的名称和种类。标签栏最前方另有不可编辑的系统「体裁」筛选，可在全部、图片、GIF、聊天记录之间切换，不会混入普通标签或被重命名。关键词始终匹配文件名和标签，并可在「设置 → 基础设置 → 搜索范围」分别控制图片描述、图片文字是否参与检索；启用后会同时搜索对应的人工与 AI 字段，聊天记录按标题匹配
- **GIF 动图** — 网格内自动播放，可在设置中关闭
- **分组/类别筛选** — 侧边栏以 tabs 切换多对多「分组」和单归属层级「类别」；类别父节点包含所有子类别，类别目录与 `cache_dir` 下原件路径同步，重命名/删除/详情换类会同步移动目录和文件。按收藏夹、自定义分组或类别过滤，点击标签可叠加搜索；名称未手动排序时按自然顺序展示（数字按数值 1,2,10，中文按拼音）
- **未分组** — 自动汇总未加入任何分组的表情，可在设置中开关显示
- **分组/标签栏横向滚动** — 栏内溢出时显示细滚动条，鼠标滚轮横向翻页
- **本地缓存** — 原件按类别层级存放 + 可再生日常副本 + 缩略图三层存储，离线可用
- **缓存扫描** — 启动时自动扫描缓存目录，已有文件无需重复导入
- **导入限制** — 原件硬上限为 200 MiB，最长边仍限制为 2560px；局域网协议帧继续使用更严格的 64 MiB 上限
- **自定义存储位置** — 设置页可更换表情包图片存放目录，切换时可选自动迁移现有文件；可显式开启 CloudDrive2 等虚拟盘兼容模式，并启用批量读取保护（分批、限速读取原件）。批量大小和暂停秒数可调，也可在后台按当前主页/搜索/分组/类别筛选逐渐缓存后续页
- **同步进度条** — 上传/下载实时显示进度、速度、当前文件，支持后台运行
- **本地备份与恢复** — 设置页一键导出全量备份 ZIP（原件 + 数据库，标签/收藏/分组/排序随库保留，不压缩省时间），日常副本不重复入包、恢复后按需重建；可选备份目录与备份列表管理，新设备空库一键恢复，仅 PC 间迁移
- **远程同步** — FTP / S3 / R2 / WebDAV 多端同步
- **局域网互联** — 与同一局域网内的手机版 OhMyMeme 配对，互相同步表情包与配置（UDP 发现 + 密钥握手 + AES-GCM 加密会话）
- **QQ 聊天记录命令** — 连接本机 NapCat Framework，共用当前 QQ 登录态；回复一条合并转发消息并发送 `/omm`，即可保存外层来源、完整消息段和嵌套转发结构。归档会作为「聊天记录」体裁出现在主内容库，点击可查看来源、消息预览和发送命令；在目标 QQ 会话发送 `/omm list` 可列出 ID，发送 `/omm send <ID>` 会按设置选择原生转发、原消息重组或本地归档重组
- **手机导入** — ADB 一键从 Android 手机拉取 QQ 表情包缓存并打包 ZIP（自动识别主存储与外置 TF 卡路径）
- **电脑版 QQ 提取** — 从 PC 版 QQ（QQNT）本地缓存批量提取收藏表情（可复用模块，见下）
- **Telegram 表情导入** — 从 Telegram Desktop 本地缓存解密提取表情包（WebP 直取、WebM 无损转 WebP 且保留透明通道，自动跳过动态表情的静态重复版），支持手动指定 tdata 目录并记忆
- **Notion 数据库导入** — 使用 Internal Integration 只读导入 Files & media 图片，可增减多个标签列映射，每个 Notion 属性分别导入为一种标签，并可映射标题和分组；图片保存到本地缓存，不依赖一小时有效的 Notion 临时链接
- **微信表情导入** — 从微信电脑版缓存提取收藏表情（Windows，需微信正在运行）：C++ 辅助二进制提取进程内存密钥 → AES-CBC 解密表情数据库 → CDN 下载并校验 MD5；支持多账号选择，二进制发布时校验 SHA-256 防篡改（发布版内置真实哈希，未配置时默认拒绝执行）
- **危险操作** — 设置页一键清空本地或云端全部数据（需双重确认）
- **无边框窗口** — 自定义标题栏，鼠标拖拽移动
- **自动更新** — 启动时检测新版本，运行期间每日自动检测，下载安装包后一键升级

## 快速开始

### 下载

从 [Releases](https://github.com/OhMyMeme/OhMyMeme/releases/latest) 下载对应系统的安装包或可执行文件，直接运行。

### 从源码运行

**环境要求**: Python 3.10+

**Linux 额外依赖**:
```bash
# Debian / Ubuntu
sudo apt install python3-gi
# apt install gir1.2-webkit2-4.0  # 按系统版本选择 webkit2gtk 包

# Arch Linux
sudo pacman -S python-gobject
yay -S webkit2gtk  # 依赖 libsoup，通过 yay 安装
```

> **说明（GTK 后端）**: 本项目 Linux 使用 pywebview 的 GTK 后端（`WebKit2`）。
> - **deb / rpm 安装包**已内置 `gi` 与 WebKit2/Soup typelib，无需额外安装 python3-gi；但依赖系统的
>   `gir1.2-webkit2-4.1`（或 4.0）包提供的 WebKitGTK 运行库，`apt install gir1.2-webkit2-4.1` 或安装
>   `libwebkit2gtk-4.1-0` 即可（deb 安装时自动处理依赖）。
> - **源码 / conda / venv 运行**必须让当前 Python 能导入 `gi`：先按上面命令在系统层安装 `python3-gi`
>   （含 `gir1.2-webkit2-*`），再让 venv/conda 环境能看到系统 dist-packages，两种方式任选其一：
>   - 创建 venv 时加 `--system-site-packages`：`python -m venv --system-site-packages .venv`
>   - 运行前设置 `PYTHONPATH=/usr/lib/python3/dist-packages`（Arch 为 `/usr/lib/python3.12/site-packages`）
>   - conda 中可执行 `conda install -c conda-forge pygobject` 直接在环境内装 PyGObject

```bash
git clone https://github.com/OhMyMeme/OhMyMeme.git
cd ohmymeme
pip install -r requirements.txt
python -m src
```

主窗口前端为 **Vue 3**（`src/vue-src/`，Vite 构建 IIFE 单文件 `src/webui/dist/ohmymeme.js`）。**源码运行**时若产物缺失会自动执行一次 `npx vite build`；手动构建方式：

```bash
npm install        # 首次构建前安装依赖
npx vite build     # 构建 Vue 前端 → src/webui/dist/ohmymeme.js
```

设置窗口仍为 vanilla 前端（`src/webui/settings.*`，独立 webview，无需构建）。旧主窗口（`src/webui/index.*`）已备份至 `src/webui-backup/`，不再使用。

主窗口启动时播放 `src/resources/OhMyMeme.mp4` 启动动画（全屏遮罩，视频结束或 6s 兜底后淡出），仅启动时播放一次，快捷键/托盘呼出不重播。设置页「显示启动动画」开关（配置 `show_startup_animation`，默认开）可关闭动画：关闭时不播放视频，降级为 300ms 延时后加载后续内容；开启时动画播放期间即并行加载（无 300ms 延时，动画天然覆盖桥接稳定时间）。

可用调试参数：

| 参数 | 说明 |
|------|------|
| `--debug-update` | 强制弹出更新对话框（测试用） |
| `--debug-startup` | 输出开机自启检测详情（注册表键、启动文件夹） |
| `--debug-adb` | 输出 ADB 检测详情及运行时日志（路径、版本、adb 命令） |
| `--debug` | 输出所有 DEBUG 级别日志 |
| `--silent` | 启动时最小化到托盘（源码模式下需显式传入） |

或使用 conda：

```bash
conda create -n ohmymeme python=3.12
conda activate ohmymeme
pip install -r requirements.txt
python -m src
```

## 使用

### 基本操作

1. **启动** — 运行后系统托盘出现蓝色图标，按 `Ctrl+Alt+M` 呼出主面板
2. **导入** — 点击标题栏「导入」按钮或直接拖拽图片到窗口，支持 png/jpg/gif/webp
3. **复制** — 点击任意表情包复制日常副本到剪贴板（GIF 保留动画）；需要未压缩文件时右键选择「复制原件」
4. **搜索** — 搜索栏输入关键词实时筛选；文件名与标签始终参与匹配，图片描述和图片文字可在基础设置的「搜索范围」中分别启用或关闭，启用后同时匹配人工与 AI 内容；点击标签或分组名叠加过滤
5. **表情包详情** — 长按表情包，或右键选择「查看表情包详情」；可编辑标题、文件名、单归属类别、所属分组、标签，以及独立的人工/AI 图片描述与人工/AI 图片文字。不适用的一侧可以留空，用于明确内容来源；类别以多级路径显示并同步原件目录，分组以完整层级路径显示，可搜索添加已有节点、就地创建顶层或子节点并选中，或移除归属。新节点会立即保留，当前表情的归属只有点击「保存更改」后才生效；也可用图片两侧按钮或 ←/→ 浏览全图库，长按不会触发复制
6. **右键菜单** — 查看表情包详情 / 复制原件 / 打标签 / 重命名 / 收藏 / 添加到分组 / 从分组移除 / 删除

### 收藏与分组

- 右键 →「收藏」将表情加入收藏夹，点击左上角⭐按钮筛选收藏内容
- 右键 →「添加到分组」创建或选择已有分组
- 分组显示在搜索栏下方，点击即可筛选该分组内的表情

### 类别与文件目录

- 侧边栏「类别」tab 用于建立单归属的层级类别；一个表情包只能选择一个类别节点，点击父类别会包含所有子类别
- 类别名称会直接成为 `cache_dir` 下的目录名，例如 `角色 / 爱丽丝` 对应 `cache_dir/角色/爱丽丝/`
- 在详情中换类、重命名类别或删除类别时，原件会同步移动；删除类别会把原件和子类别提升到上一级。多对多的「分组」仍只管理数据库关系，不改变文件路径

### 设置

点击标题栏⚙按钮打开设置窗口：

| 选项 | 说明 |
|------|------|
| **快捷键** | 自定义全局热键，格式如 `Ctrl+Shift+M` |
| **热键在鼠标处显示** | 默认关闭，仅 Windows 生效；全局热键打开隐藏主面板时按鼠标所在显示器的工作区放置，不影响托盘激活 |
| **开机自启** | 系统登录时自动启动，可选静默启动（仅托盘） |
| **GIF 动画** | 关闭后网格中仅显示 GIF 首帧 |
| **复制处理** | 普通复制以日常副本为输入，可继续选择不处理 / WebP 缩放（默认）/ 转 GIF / 转 GIF 隐写；右键「复制原件」绕过该设置。导入含隐写的 GIF 始终自动解码，只入库还原后的原图 |
| **批量读取保护** | 适合 115/CloudDrive2 等虚拟盘；启动扫描、首次建立日常副本、备份、迁移和同步读取大量原件时限制为单文件读取。可设置每批文件数（默认 8）和每批暂停秒数（默认 1 秒）；还可后台预热当前筛选结果的后续页，切换搜索或分组时自动取消旧任务。普通本地磁盘无需开启 |
| **上传进度条** | 上传时显示实时进度弹窗 |
| **上传完毕提示** | 上传完成后弹窗告知结果 |
| **下载进度条** | 下载时显示实时进度弹窗 |
| **下载完毕提示** | 下载完成后弹窗告知结果 |
| **从手机导入** | 通过 ADB 从 Android 手机拉取 QQ 表情包缓存并打包为 ZIP（设置页「导入」列表项，点击即开始） |
| **从电脑导入** | 从 PC 版 QQ（QQNT）本地缓存提取收藏表情（向导式：环境检查/选账号/输出位置/进度汇总） |
| **Telegram 导入** | 从 Telegram Desktop 本地缓存解密提取表情包（WebP/WebM，WebM 需 ffmpeg 无损转 WebP 且保留透明通道，自动跳过与动态表情重复的静态版；未自动检测到目录时可手动指定 tdata 并记忆） |
| **Notion 数据库导入** | 通过 Internal Integration 只读导入数据库的 Files & media 图片；可增减多个 Multi-select/Select/Status/Rich text/Relation 标签列映射，并把 Title 和选择类属性映射为名称及分组 |
| **抖音导入** | 通过抖音网页版 API 下载自定义表情包（需粘贴登录 Cookie，WebP 原格式保存） |
| **微信导入** | 从微信电脑版缓存提取收藏表情（Windows，需微信运行）：辅助二进制提取密钥 → 解密数据库 → CDN 下载校验；支持多账号选择；发布版含二进制 SHA-256 防篡改（未配置真实哈希时拒绝执行） |
| **危险操作** | 一键清空本地/云端全部表情包（需输入 confirm 双重确认） |
| **局域网互联** | 设置端口与连接密钥，开启后同一局域网内手机版可发现并同步表情包/配置（见下） |
| **QQ 聊天记录（NapCat）** | 配置本机 OneBot HTTP/WebSocket、OneBot Access Token，以及用于拖拽目标识别的 NapCat WebUI/API 地址与 Token；开启监听后，在 QQ 中回复一条合并转发消息并发送 `/omm` 即可保存，不扫描最近会话 |
| **远程同步** | 配置 FTP / S3 / R2 / WebDAV 同步（见下） |
| **关于** | 版本号与检查更新、项目贡献者名单（`contributor.starsfire.top` 徽章） |

### QQ 聊天记录命令

1. 在 NapCat 的 WebSocket 服务配置中开启 `reportSelfMessage`（上报自身消息），并在 OneBot 全局配置中开启 `parseMultMsg`（解析合并转发消息）；后者用于保留多层嵌套聊天记录。HTTP 与 WebSocket 服务使用相同的 Access Token。
2. 在 OhMyMeme「设置 → 联网 → QQ 聊天记录（NapCat）」中填写 OneBot 地址/Token，并填写 NapCat WebUI/API 地址（默认 `http://127.0.0.1:6099`）和 `webui.json` 中的 Token。WebUI 配置既用于拖拽识别当前会话，也用于 NapCat 重启后按来源会话与真实消息序号恢复原生转发。勾选命令监听后保存；默认命令为 `/omm`，可以修改。
3. 在 QQ 中回复一条“合并转发聊天记录”，只发送 `/omm`。OhMyMeme 仅接受当前账号自己发出的命令，并在原会话回复保存结果；不会读取最近会话列表。排障时也可发送 `/omm 消息ID`。
4. 保存后可在主窗口「体裁 → 聊天记录」中查看。点击卡片可浏览来源信息和归档消息预览，也可以复制该记录的发送命令。
5. 在设置页选择聊天记录发送方式，再在准备接收记录的群聊或私聊中发送 `/omm list` 查看最近归档，并发送 `/omm send <ID>`。三档方式分别是：原生转发外层原消息、重新读取原消息并重组、仅使用数据库中的本地归档重组；默认使用兼容性最好的原生转发。原生方式会先尝试当前 NapCat 会话的 OneBot 短 ID；短 ID 因 NapCat 重启失效时，桥接插件会用归档中的来源会话和 QQ 真实消息序号重新取得原始 `msgId`，仍执行原生转发。只有 QQ 历史中已找不到外层消息，或旧归档没有 `real_seq` 时才无法恢复。安装桥接插件并配置 WebUI 后，导入还会保存一份不含媒体文件本体的 QQNT 原始长消息负载。模式二会重新读取并写回归档，模式三可直接重放；不改写内部发送者和头像，并按归档原会话选择长消息资源上下文。旧归档没有原始负载时才使用节点重组回退。已知限制：模式二、三生成的卡片在手机 QQ 上可正常查看，但 Windows QQ 仍可能显示“暂不支持该消息类型”；已验证的外层元素克隆和自聊中继也无法解决，因此不再保留这两条实验路径。需要桌面端可靠查看时请使用模式一。原始负载中的媒体引用保持 QQ 原值，本地媒体副本仍独立保留作为归档和后备；当前最多保留两级内层聊天记录（加上最外层卡片共三层）。没有可用 WebUI 桥接时才沿用旧 OneBot 重组路径。
6. Windows 下也可直接把主窗口中的聊天记录卡拖到已经打开目标会话的 QQ 窗口。拖拽本身不会向输入框投递文件、文字或 `/omm` 指令；松手后 OhMyMeme 把落点 QQ 窗口句柄交给桥接插件，插件精确读取同一个 QQ 渲染窗口的顶部聊天标题，再与 NapCat 的群/好友列表唯一匹配，随后按设置中的发送方式调用 OneBot。这条路径不依赖“最近一次切换”状态；插件不可用时才回退到 Windows UI Automation。拖到非 QQ 窗口、目标无法唯一确定或标题不可读时会拒绝发送；按 Esc 可取消。
7. 安装项目内的 `napcat-plugin-ohmymeme-current-peer` 桥接插件，才能使用按落点窗口识别目标、跨 NapCat 重启的原生消息重定位、多层聊天记录恢复，以及重组聊天记录的 Windows QQNT 卡片兼容。可复制插件到 NapCat Framework 的 `plugins` 目录，或在 PowerShell 执行：`.\scripts\install_napcat_current_peer.ps1 -NapCatRoot "E:\Program_Files_tools\NapCat.Framework"`，然后完全重启 NapCat。插件通过受 WebUI 鉴权保护的 `/current-peer`、`/forward-message`、`/recover-forward-resource` 与 `/send-native-forward` 路由，分别提供当前窗口识别、按真实 `msgSeq` 原生转发、多 action 嵌套资源恢复，以及由 QQNT 内核原生重组发送。重组数据经系统临时目录中的随机文件传递，插件只接受指定前缀、位于临时目录且不超过 64 MiB 的文件，读取后立即删除。NapCat Packet 卡片的 `extra` 格式修正规则仍保留给无 WebUI 的旧路径，但仅修外层卡片不足以让 Windows QQ 接受内部伪造消息包。部分 NapCat Framework 构建会把插件 ID 限制在内置白名单，安装脚本会提示这种情况；确认接受对本地 `napcat.mjs` 做可恢复的一行白名单修改后，可追加 `-PatchLocalWhitelist`（脚本会先保存 `.omm-original` 备份）。

归档兼容普通节点的 `message`、NapCat 4.18 的 `node.data.message`，以及 `parseMultMsg` 产生的 `data.content` 内联节点；嵌套转发的 `type: node` 包装也会还原为层级结构。如果 NapCat 最终仍返回没有任何正文的空节点，OhMyMeme 会拒绝保存，避免把未展开、已过期、已撤回或当前版本不支持的内容误存成完整归档。已经保存的旧空节点会在详情和重组发送时显示明确占位。媒体归档会沿资源引用和全部内联节点递归查找；图片在 `downloadRichMedia` 超时时会用原生 `picElement` 重新生成带当前 rkey 的腾讯地址，视频、语音和文件则优先复用原生消息中的本地路径或重新申请当前有效地址，再复制到永久目录。

当前版本把外层消息位置、QQ 真实消息序号 `real_seq`、全部消息段和可获取的嵌套转发资源写入本地数据库；数据库摘要中的旧短序号会在升级启动时从结构包自动修正。对同一条外层消息重复执行命令会更新原记录。保存多层聊天记录时，桥接插件会参考 QQ Chat Exporter 的做法，先按来源会话与 `real_seq` 重新找到外层原消息，再用 `MsgApi.getMultiMsg` 取得带真实 `msgId`/`elementId` 的内层消息：图片会依次尝试 QQNT 缓存、原生下载事件与当前 rkey 地址，视频、语音和文件会从原生元素取得本地路径或刷新 URL。成功取得的全部媒体都会复制到 `cache_dir/chat-record-media/<记录哈希>/`，每份聊天记录独占一个子目录；图片单文件上限 64 MiB，其他媒体单文件上限 512 MiB。结构包只保存相对于 `cache_dir` 的路径，因此媒体会随“表情包存储位置”迁移和本地备份一起移动，删除聊天记录时也会删除对应子目录。升级后会在后台把旧 `data_dir/chat-record-media/` 文件迁入新位置并更新引用。反馈会分别说明已本地保存和仅保留临时引用的媒体数量。升级前已经保存且引用已过期的旧媒体无法只靠 JSON 结构反推出文件本体，需要对一条仍可原生打开的新卡片重新执行 `/omm`。

QQNT 当前不会把合并转发卡片以包含 OneBot 消息 ID/资源 ID 的标准文件或文本拖出，因此“从 QQ 把卡片拖进 OhMyMeme 并完整归档”尚不能可靠实现；现阶段导入仍使用回复外层卡片后发送 `/omm`。不能把 QQ 拖出的普通 `[聊天记录]` 文本误当作可恢复的结构数据。

### 待办：突破重组转发的三层限制

- [ ] 为 NapCat Framework 开发独立桥接插件，暴露“只上传合并转发资源、不立即发送消息”的受限本机接口；OhMyMeme 再从最深层向外逐层上传资源，最后只发送外层卡片。
- 当前 NapCat 4.18.28 的 Packet 转发实现把递归条件硬编码为 `dp < 3`，没有对应配置项；不采用直接修改 `napcat.mjs` 或维护 NapCat fork 的方案。
- 已验证公开 OneBot 接口的临时自聊方案：临时卡片能够发送并返回 `message_id`/`forward_id`，但无论外包一层 `node` 还是直接作为 `node.id` 引用，父卡片重新读取后都只剩空消息节点，因此暂不接入正式发送流程。
- 插件方案实施前，两种非原生重组发送保留“最外层卡片 + 两级内层记录”；更深内容继续在最深可用层展开并明确提示，保证消息不丢失。

### Notion 数据库导入

1. 在 Notion 创建 Internal Integration，并把目标数据库共享给该 Integration；只需读取内容权限。
2. 在主窗口点击「导入表情包 → 从 Notion 数据库导入」，直接在弹窗中填写 Integration Token 和数据库 URL。数据库含多个 data source 时还需填写 Data source ID。
3. 点击「测试连接」自动识别属性，也可以手工填写属性名。在「标签列」区域用「添加标签列」和删除按钮增减映射：左侧填写 OhMyMeme 中的标签种类，右侧选择对应 Notion 属性；种类留空时默认使用该属性标题。支持 Multi-select、Select、Status、Rich text 与 Relation，Relation 会读取关联页面的 Title 作为标签或分组名称。
4. 点击「开始导入」。每个标签列会按自己的种类写入，同名标签在不同种类中保持独立。原件会经过 200 MiB 硬上限、最长边 2560px 限制和 SHA-256 去重，保存后生成日常副本；Notion 数据不会被修改。

Integration Token 在本地配置中加密保存。成功导入后会在本地数据库记录 Data source、Page、图片属性、文件位置和页面修改时间；再次导入时，未修改且本地文件仍存在的 Notion 托管图片会在下载前跳过，并继续以 SHA-256 作为最终内容去重。旧数据首次升级后需要重新下载一次以建立来源映射。Notion 的临时签名 URL 不会被保存；外部媒体 URL 因内容可能原地变化，仍会每次下载并校验，且仅允许公网 HTTPS 地址。

### 远程同步

支持四种后端同步多台设备的表情包库：

- **FTP** — 服务器地址、端口、用户名/密码（留空为匿名登录）
- **S3** — Endpoint、Region、Bucket、Access Key、Secret Key、路径前缀
- **R2** — Account ID、Access Key ID、Secret Access Key、Bucket、路径前缀（Endpoint 自动拼接）
- **WebDAV** — 配置项：`webdav_url`（服务地址）、`webdav_user`（用户名）、`webdav_password`（密码）、`webdav_path`（远程路径前缀，可选）。使用用户名/密码 Basic Auth 认证，密码自动加密保存；基于 Python 标准库 `urllib.request` 实现，无需新增依赖。同一批上传只对 `memes/` 目录创建一次，且已确认存在的目录进程内缓存，避免多线程上传时重复 `MKCOL` 触发远端锁

配置完成后点击「测试连接」验证，然后使用标题栏的⬆上传 / ⬇下载按钮同步。

> ⚠️ 若设置中开启了「同步时删除远程文件」，上传操作会删除远程端已不存在的文件。

### 局域网互联

与同一局域网内的手机版 OhMyMeme 配对，无需公网即可互相同步表情包与配置：

1. **配置** — 设置页「局域网互联」设置端口（默认 17852）与连接密钥（留空表示同局域网内无需密钥，不推荐）
2. **开启** — 勾选「开启互联访问」临时启动服务（重启后默认关闭，不写入配置）
3. **连接** — 手机版扫描局域网发现本机，输入密钥配对；配对成功后电脑弹窗确认设备信息，允许后才开始同步

局域网接收到新表情、或 NapCat 完成聊天记录归档后，主窗口会自动重新查询当前筛选并回到第一页，确保新内容立即可见；短时间内连续入库会合并为一次刷新，不会整页重载或重新扫描整个图库。
4. **安全** — 数据帧使用 AES-GCM 会话加密；设备连接需电脑端确认；配置同步默认剔除 FTP/S3/R2/WebDAV 等密码字段，开启「允许密钥传输」（仅本次会话，弹窗警示）后才会包含密钥；`push_file` 四重校验（文件名、≤64MB、sha256、合法图片），不合法字节绝不落盘

> ⚠️ 服务绑定 `0.0.0.0`，同一局域网内所有设备均可探测到本机（发现响应不含任何密钥信息）。仅在你信任的 Wi-Fi 下开启。

### 电脑版 QQ（QQNT）收藏表情提取

`src/qqnt_extract.py` 提供从 PC 版 QQ（QQNT）本地缓存批量提取收藏表情的可复用模块：

- 自动读取 `C:\Users\Public\Documents\Tencent\QQ\UserDataInfo.ini` 获取用户数据目录（自适应 GBK/UTF-8 编码，支持 BOM）
- 多账号（纯数字子目录）识别，可通过 `uapis.cn` 查询昵称（本地 JSON 缓存 1 小时，可选、依赖网络）
- 表情目录：`<UserDataSavePath>/<QQ号>/nt_qq/nt_data/Emoji/personal_emoji/Ori`
- `os.walk` + `shutil.copy2` 复制，**逐文件容错**（失败跳过继续并通过 `on_error` 上报、清理半成品），进度/日志通过 `on_progress`/`on_log` 回调输出，不依赖任何 UI 框架；支持 `image_only` 仅提取图片、`overwrite` 覆盖已有目录（默认拒绝写入非空目录）
- 环境探测 `get_extract_status()` 可区分「配置缺失」「路径失效」「无可用账号」三种情况
- 已集成到设置页「从电脑导入」向导：环境检查 → 选账号 → 输出位置 → 进度与结果汇总；手动选择的配置文件/用户数据目录会持久化，提取过程支持取消；「选择配置文件/选择用户数据目录」按钮在探测成功后仍显示，指定用户数据目录后完全覆盖 INI 推导路径（应对多用户 Windows 下 `UserDataInfo.ini` 仅记录第一个用户路径的场景）
- 复制后按文件头魔数修正扩展名（QQ 缓存文件常无扩展名或扩展名错误）
- 无弹窗、无 `sys.exit`，失败以返回值/异常表达

> ⚠️ **许可证**：`src/qqnt_extract.py` 改编自 GPL-3.0 项目 [QQFavoriteExtract](https://github.com/VanillaNahida/QQFavoriteExtract)（作者：香草味的纳西妲），按 **GPL-3.0** 协议分发，与项目其余部分的 MIT 许可不同。引入该模块后，整体作品在再分发时需以 GPL-3.0 兼容方式处理，请在使用前确认合规性。

### 路径说明

| 用途 | 路径 |
|------|------|
| 配置文件 | `%APPDATA%/OhMyMeme/config.json` |
| 数据库 | `%LOCALAPPDATA%/OhMyMeme/memes.db` |
| 原件 | `%LOCALAPPDATA%/OhMyMeme/cache/`（可在设置中更换） |
| 日常副本 | `%LOCALAPPDATA%/OhMyMeme/derivatives/`（可再生） |
| 缩略图 | `%LOCALAPPDATA%/OhMyMeme/thumbnails/` |
| 索引文件 | `%LOCALAPPDATA%/OhMyMeme/meme-index.json` |

## 构建

依赖 PyInstaller 6.0+。构建前请确认前端产物已生成：`src/webui/dist/ohmymeme.js`（Vue 构建产物已随仓库提交，改过前端后需 `npx vite build` 重新生成，见[从源码运行](#从源码运行)）。

> **⚠️ 注意**: PyInstaller **不支持交叉编译**。`--windows` 参数只能在 Windows 系统上使用，`--linux` 参数只能在 Linux 系统上使用，`--macos` 参数只能在 macOS 上使用。
> 如需在本地构建其他平台安装包，请使用 [GitHub Actions](#ci-github-actions)（推送到 `main` 分支自动触发，或手动运行 workflow）。

```bash
pip install pyinstaller

# 自动检测当前系统打包
python scripts/build.py

# Windows 目标（仅在 Windows 上运行）
python scripts/build.py --windows

# Linux 目标（仅在 Linux 上运行）
python scripts/build.py --linux

# macOS 目标（仅在 macOS 上运行，产出 .app + .dmg；架构默认按机器自动检测）
python scripts/build.py --macos

# 指定 macOS 架构（arm64 / x86_64）
python scripts/build.py --macos --arch x86_64

# 仅打包，跳过安装包
python scripts/build.py --build-only

# 仅制作安装包（PyInstaller 已打包完时，仅 Windows）
python scripts/build.py --installer-only

# Linux 目标指定包类型：all | appimage | deb | rpm（默认 all）
python scripts/build.py --linux --installer-only --package deb
```

**Windows 安装包**: 需 [InnoSetup 6/7](https://jrsoftware.org/isdl.php)。

**Linux 包**: 支持 .deb / .rpm / AppImage，`--package` 指定包类型（`all` / `appimage` / `deb` / `rpm`，默认 `all`）。构建前需安装 GTK/WebKit 依赖（同上）。

**macOS 包**: `--macos` 产出 `.app`（PyInstaller `--windowed`，自动用 iconutil 从 `src/resources/icon.png` 生成 icns）与 `.dmg`（hdiutil 打包，含 /Applications 快捷方式）；文件名带架构后缀 `OhMyMeme-v{version}-{arch}.dmg`，`--arch` 指定 arm64/x86_64（默认自动检测）。

```bash
# 等价于 bash scripts/installer/linux/build.sh all / deb / rpm / appimage
python scripts/build.py --linux --installer-only --package all
python scripts/build.py --linux --installer-only --package deb
python scripts/build.py --linux --installer-only --package rpm
python scripts/build.py --linux --installer-only --package appimage
```

> 原 Nuitka 构建脚本已移至 `scripts/nuitka/build.py`，待申诉完成后重新启用。

输出目录: `dist/`。

## PR 贡献

欢迎提交 Pull Request。提交前请确保通过以下检查：

```bash
ruff check src/   # lint 检查
black --check src/  # 格式检查（black 26.5.1, line-length 88）
python -m pytest tests/ -v  # 测试
```

CI 会自动运行 lint+test。

网格拖拽槽位回归探针位于 `tests/fixtures/grid_slot_probe.cjs`。

## 贡献者

[![Contributors](https://contributor.starsfire.top/OhMyMeme/OhMyMeme/)](https://github.com/OhMyMeme/OhMyMeme/graphs/contributors)

## AI 辅助开发

本项目包含 `AGENTS.md` 文件，供 AI 编码助手读取以了解项目结构、代码规范和关键实现细节。若通过 AI 修改代码，请确保 AI 读取该文件后再进行操作。

## 架构

```
┌─────────────┐     ┌──────────────────┐
│  系统托盘    │◄────│   全局快捷键      │
│  (pystray)   │     │  (Ctrl+Alt+M)    │
└──────┬──────┘     └──────────────────┘
       │ 呼出
┌──────▼──────┐     ┌──────────────────┐
│  WebView    │────►│   Bottle API     │
│  Vue 3      │     │   (localhost)    │
└──────┬──────┘     └──────────────────┘
       │ API 调用
┌──────▼──────┐     ┌──────────────────┐
│   SQLite    │     │    本地存储      │
│  元数据      │     │ 原件/副本/缩略图 │
└─────────────┘     └──────────────────┘
```

主窗口前端基于 **Vue 3**（`src/vue-src/`，Vite 构建为 IIFE 单文件 `src/webui/dist/ohmymeme.js`），通过 `pywebview.api.*` 桥接调用后端 `JsApi`；设置窗口仍为 vanilla（`src/webui/settings.*`）。

## 实现要点

### 启动时序
启动分两阶段：首先 `get_init_data()` 秒开渲染数据库数据，随后执行 `rescan_cache()` → `run_auto_sync()` → `check_update()`。**动画开启时动画播放期间即并行加载**（动画天然覆盖桥接稳定时间，无 300ms 延时）；**动画关闭时降级 300ms 延时**（桥接稳定需要）。**先 rescan 再 sync**（文件与 DB 一致后再对比远端）。

### 缓存去重
开启设置 `bulk_read_protection` 后，首次建立日常副本、启动扫描、manifest 重建、备份、迁移和同步等批量读取会共用保护闸门：一次只读取一个原件，并按 `bulk_read_batch_size`（1–1000，默认 8）与 `bulk_read_pause_seconds`（0–60 秒，默认 1）分批暂停，以降低虚拟盘短时间内的请求密度。额外开启 `bulk_read_prefetch_pages` 时，每次分页查询会取消旧预热任务，并从当前页之后按相同查询条件逐项补建日常副本和缩略图；任务启动前让当前页请求先行，每项之间主动让出执行机会。

扫描缓存目录时**双重去重**：按文件名查 DB 防止每次启动重复注册，按 SHA-256 哈希查 DB 防止同图不同名重复。导入（拖入/对话框）同样有哈希去重。

### 原件与日常副本
`cache_dir` 中的 `filename`/`file_hash` 始终代表原件；`data_dir/derivatives` 保存可再生的日常副本。静态图片转为质量 84 起步的 WebP 并把最长边限制到 1600px，GIF 保持 GIF，动画 WebP 保持动画 WebP，透明通道、帧时长和循环信息会保留。超大 JPEG 会先有界读取文件头：不超过 3 亿像素时在完整解码前使用 JPEG 原生降采样，超过则继续拒绝，不会全局关闭 Pillow 的安全保护。仅当原件本来就在 1600px、8 MiB 范围内且重新编码反而更大时，副本目录才保存原格式字节副本。网格动图、详情预览、普通复制和原生拖拽使用日常副本，右键「复制原件」直接读取原件。日常副本不进入备份或远程同步，丢失、版本过期、旧库升级、备份恢复或同步拉取后均会在首次使用时从原件自动重建。

缩略图位于 `data_dir/thumbnails`，日常副本与缩略图都会跨重启保留；删除表情时删除该表情对应的两类文件，清空本地数据时整体清理。目前没有按文件数量、访问时间或总容量自动淘汰的逻辑，历史配置项 `cache_max_size_mb` 也尚未用于清理。WebView 还会自行缓存当前页面已请求的图片，但其内存/HTTP 缓存由浏览器内核管理。开启“后台逐渐缓存后续页面”后，后台只持久化日常副本与缩略图，不会把全部后续页图片长期保留在网页 DOM 或应用图片对象中。

### GIF 剪贴板
Windows 上 GIF 复制同时写入三个剪贴板格式：`CF_DIB`（首帧 BMP）、`CF_HDROP`（文件路径，QQ/微信粘贴动图必需）、自定义 `"GIF"` 格式。**移除 CF_HDROP 会导致 QQ/微信粘贴 GIF 变静态图。**

### 复制处理模式
设置页「复制处理」下拉（配置 `copy_resize_mode`：0不处理；1webp缩放，默认；2转gif；3转gif隐写原图）：普通复制先取得日常副本，再对超过 `copy_resize_max`（默认 200px）的静态图按模式处理，动图（GIF/动画 WebP）不受影响。模式 1 继续转 WebP 缩放到限制内；模式 2/3 按日常副本分辨率转 GIF（模式 3 额外隐写日常副本，失败时复制日常副本）。右键「复制原件」完全绕过此流程。处理结果写入系统临时目录并按编码参数和版本复用缓存。

### 加密降级
加密优先使用 `cryptography.fernet.Fernet`，不可用时降级为 `hashlib.pbkdf2_hmac` + XOR + base64。**不能移除 XOR 降级**，否则无 `cryptography` 时系统崩溃。

### Sync 集合合并
`pull` 时远端分组以**并集**方式合并到本地已有分组（不清除本地成员）。远端 manifest 中的 `collections` 用文件名关联（非 ID），跨设备稳定。

### Manifest 自动清理
构建 `meme-index.json` 时，若某分组无成员则自动删除该分组并跳过写入，防止空分组累积到远端。只修改表情标题时会在进程内加锁并原子更新清单中的对应 `name`，不会重新检查整库原件的 `exists/stat`；清单缺失、损坏或找不到对应文件时才回退到完整重建。

### 拖拽排序反馈
`canReorderMemes()` 仅在未搜索、未按标签筛选、当前为 ID 大于 0 的普通分组（含子分组）、且已开启拖拽排序时允许排序。此时普通表情卡最终为 `scale(0.95)`，带 3px `var(--border-light)` 描边和 3px 偏移，并以独立 `rotate` 属性作轻微快速晃动；正在拖拽、FLIP 让位、分页入场和文件夹卡均不晃动，系统减少动态效果偏好会禁用该动画。正在拖拽的卡独立使用最终 `translate(...) scale(0.90)`，保留现有透明度、阴影和 FLIP 效果，变换不得叠加。开启工具栏排序开关时沿用现有入场反馈；仅明确关闭该开关时保留当前卡片，以动画退场。搜索、标签筛选、切换分组或进入虚拟分组导致的资格变化均按普通刷新处理，不播放退场动画。

## 技术栈

| 模块 | 技术 | 理由 |
|------|------|------|
| UI | PyWebView (v6) + Bottle + Vue 3 | 原生 WebView 渲染，Vue 3 组件化前端（Vite 构建） |
| 托盘 | pystray | 最轻跨平台托盘库 |
| 热键 | keyboard → pynput → 轮询回退 | 三级降级保障 |
| 图片 | Pillow | 业界标准 |
| 剪贴板 | ctypes (Win32 API) | 零外部依赖，GIF 动画原始字节 |
| 数据库 | SQLite3 (WAL) | 内置，线程安全 |
| 加密 | cryptography (Fernet) | 轻量对称加密 |
| 窗口 | frameless + JS 拖拽 | 自定义无边框体验 |

## 许可证

GPL-3.0
