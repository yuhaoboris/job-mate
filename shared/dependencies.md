# 安装依赖与运行就绪

安装 job-mate、setup 首次设置或修复缺失环境时读本文。**本人请求安装或设置，即授权 Agent 安装完成该功能所必需的软件依赖，无需逐项确认。** 先简要告知准备做什么，然后执行；不要只丢安装链接让本人自行研究。apply/followup 运行中不安装或升级，先按执行契约收尾，再交 setup 修复。

依赖安装不授予平台外发、键盘鼠标操作或飞书应用权限。系统授权、管理员认证、登录、扫码由本人完成；不要收集密码、自动点击同意或关闭系统安全保护。不安装后台服务，不改无关 Agent 配置。没有网络、安装权限或图形会话时报告具体阻断，不假报成功。

## 1. 检测与安装位置

1. 确认命令实际运行在哪台电脑、哪个系统/架构；远程容器能运行脚本不等于能操作本人的桌面。不能访问本机可见窗口时，先说明只能安装数据技能，平台操作受阻。
2. 检查 Python 3.9+：macOS/Linux 用 `python3 --version`，Windows 用 `py -3 --version`。已有合格解释器就复用。缺失时用已有系统包管理器或 [Python 官方安装包](https://www.python.org/downloads/) 安装受支持的 Python 3；不替换系统自带解释器。Windows 安装 Python launcher，确保 `py -3` 可用。
3. 软件先复用兼容版本。需要补装时优先用户级目录；遇版本冲突不卸载、不降级已有工具，安装隔离副本。下文 `<依赖根>` 固定为 macOS/Linux 的 `${XDG_DATA_HOME:-$HOME/.local/share}/job-mate/deps`，Windows 的 `%LOCALAPPDATA%\job-mate\deps`（不存在该变量时用用户目录下 `AppData\Local`）。解析为绝对路径，路径含空格按当前 shell 引用。不放临时目录、源码或求职工作区，避免安装后被清理。
4. npm 路径需要 Node：先检查 `node --version`、`npm --version`，缺失或不满足所装包 engines 时，用已有包管理器或 [Node 官方下载](https://nodejs.org/en/download) 安装当前受支持的 LTS。桌面包要求 Node >=18，飞书包要求 >=16；优先同一受支持 LTS，不为不同包重复安装。无 npm 时也可走下述原生发行包，不为原生 CLI 强装 Node。
5. 软件安装后刷新当前命令环境并检查实际可执行路径；新终端也必须能找到解释器。用户级 CLI 不依赖临时 PATH：使用下文确定的绝对入口。需要永久 PATH 调整时只添加本次管理的目录，不覆盖原值；需重开 Agent 时明确提示并复查。

已有包管理器时，缺项可按下表安装（只执行缺项，不无条件重装）：

| 环境 | Python | Node（需要 npm 时） |
|---|---|---|
| macOS / Homebrew | `brew install python` | `brew install node`，核对版本受支持 |
| Windows / winget | `winget install --id Python.Python.3.13 --exact --scope user` | `winget install --id OpenJS.NodeJS.LTS --exact` |
| Debian / Ubuntu | `apt-get install python3`（需管理员权限） | 优先 Node 官方 LTS 发行包；系统源版本须满足 engines |

没有这些管理器时走上面的官方安装包，不为装一个依赖额外部署整套工具链。管理员认证弹窗交本人操作；无权限时先尝试用户级安装，仍不可行则保留已完成项和一个明确恢复步骤，不反复提权。

## 2. 桌面依赖（macOS）

本项目的 agent-desktop 规则仅覆盖 macOS。Linux/Windows 检查 Agent 已有的非 CDP 原生桌面工具，按[执行契约](execution.md#1-加锁前能力自检)验证能力；没有就明确报告平台操作受阻，不因上游宣称跨系统支持而放宽本项目范围。

### 安装 CLI 与浏览器

按顺序检查 `<依赖根>/desktop/bin/agent-desktop`、PATH 中的 CLI；复用符合[工具规则](browser/agent-desktop.md)的版本。新装固定参考基线，不用 latest：

```sh
npm install --global --prefix "<依赖根>/desktop" agent-desktop@1.0.2
```

npm 包包含原生 CLI 与 helper。没有 npm 且不需飞书时，也可从 [v1.0.2 官方发行页](https://github.com/vercel-labs/agent-desktop/releases/tag/v1.0.2) 下载对应 macOS/架构的归档，按该版本说明核验来源与校验和，把 CLI 和同版 helper 一起解压到 `<依赖根>/desktop/bin`；不混装版本、不从第三方镜像拼凑 helper。发行包不可获取时报告阻断，不静默换 latest。

Chrome 已安装就复用，不新建浏览器配置、不清理已有资料；缺失时从 [Chrome 官方下载](https://www.google.com/chrome/) 安装。用系统打开方式准备日常配置下的可见空白窗口（about:blank），保留原有标签页；不能安全打开时请本人打开。账号由本人在实际使用前登录，安装流程不读取招聘页面、不进行平台测试。非 Chrome 浏览器不冒充本规则已支持。

### 加载 skill 与只读自检

先读[工具规则](browser/agent-desktop.md#加载上游操作-skill)，用刚解析的 CLI 绝对路径加载其内嵌 skill。这是上游支持的 skill 加载方式，不必另装一套可能不同版本的全局 skill；CLI 安装成功不等于 skill 已读取。

核对版本、格式、辅助功能与屏幕录制权限、Chrome 应用和窗口清单。权限缺失时，按该版本帮助提供系统设置入口并带本人完成授权，再重新检查；不代点授权、不仅凭本人说“开了”就判成功。固定已确认的空白窗口身份，按该版本帮助取得最小辅助功能快照，确认窗口可读且目标区域完整；仅有窗口清单不算通过，不读取其他窗口或招聘页面，不输入、不点击、不发送。截屏能力按权限状态及所装版本文档核对，不在安装时截取私人页面。实际运行每一步仍需验证可观察性。

## 3. 飞书依赖（仅选择飞书时）

未选择飞书则跳过，不安装其 CLI、不要求创建应用，不阻断本地求职功能。选择“现在配置”即授权补齐其软件依赖；应用授权、本人身份确认与测试发送不包含在安装授权中。

先检查 `<依赖根>/lark` 的入口，再检查 PATH。必须是 **1.0.96**，其他版本保留，使用隔离安装：

```sh
npm install --global --prefix "<依赖根>/lark" @larksuite/cli@1.0.96
```

macOS/Linux 入口为 `<依赖根>/lark/bin/lark-cli`；Windows npm 入口是 `.cmd`，业务调用不能执行它，改用绝对 Node 路径及 `<依赖根>/lark/node_modules/@larksuite/cli/scripts/run.js`。没有 Node 时，也可按 [1.0.96 官方说明](https://github.com/larksuite/cli/tree/v1.0.96) 获取对应原生发行包并核验来源，使用绝对可执行路径。不要运行上游的 latest 一键安装或批量安装所有飞书技能。

以下 `lark-cli` 均替换为实际入口（Windows 为 Node 与固定 JS 的独立参数）。先读[飞书适配器](im/lark.md)，核对本机 `--help`、`config init --help`、`profile --help`，再执行：

1. 已有应用时只读列出配置档供本人选择，保留其他配置；没有时按 `profile --help` 创建独立命名的 job-mate 配置档，按 1.0.96 的 `config init --new` 向导引导本人在授权页面创建/绑定应用。配置命令也显式指定目标档，不覆盖默认档。命令有等待时使用有界后台调用，及时把授权 URL 原样交给本人并按 `auth qrcode --help` 展示二维码；不在展示前阻塞等待，不自建常驻服务，不把凭据写进对话或工作区。
2. 在飞书开放平台启用机器人；由本人授权、发布应用并确认本人在可用范围内。机器人需 `im:message:send_as_bot`（[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create.md)）及 `im:message:readonly`（[读取私聊历史](https://open.feishu.cn/document/server-docs/im-v1/message/list.md)），已有等效权限则不重复申请；不申请所有业务域或群消息权限。权限不足给出具体缺项；用户登录不能替代机器人权限。
3. 按[本人身份与配置档](im/lark.md#本人身份与配置档)核验同应用 ownerId。只有确需用户身份查询时才引导本人完成所需通讯录授权，不默认全域登录。应用或身份不明不填写假值。
4. 隔离安装的入口由 Agent 直接解析并展示，无需让本人手填技术路径。setup 将 `larkCommand` 数组与其余 IM 配置一并确认、事务提交；Windows 用 `["<node.exe绝对路径>","<run.js绝对路径>"]`，POSIX 用 `["<CLI绝对路径>"]`。不改兄弟授权或其他配置档。
5. 提交后运行 `im_lark.py doctor`，通过后按 setup 流程另行征得测试内容同意，再验证通知投递与本人引用回复；没有测试授权就报告“软件与机器人检查通过，收发未测试”，不擅自试发。

## 4. 完成判据

安装结束逐项报告，不合并成一句模糊的“装好了”：

| 项目 | 通过判据 |
|---|---|
| 技能包 | 三个技能完整、版本一致，当前 Agent 能发现；需刷新时先完成刷新或报告待刷新 |
| 脚本环境 | 当前会话可运行合格 Python，已安装包的核心脚本 `--help` 正常退出 |
| 桌面环境 | 合格 CLI/原生工具、上游 skill 已读、所需权限与空白窗口完整读取通过 |
| 飞书 | 未启用，或分别报告软件、机器人检查与真实收发测试结果 |
| 本人动作 | 系统授权、刷新 Agent、打开浏览器、登录或应用授权的具体待办 |

技能包与必要运行环境均通过，才说“安装完成，可以开始求职设置”。有阻断说“技能包已安装，运行准备尚未完成”，列出原因与下一步；本人选择稍后处理时保留现状。不要靠放宽版本、换控制方式或假造检查结果完成安装。

这些是当前环境的只读检查，不代表真实 BOSS 兼容或发送已验证。安装不创建求职工作区、不触发外发；不保存截图、聊天、窗口全文或凭据。setup 的配置检查与运行环境检查分开汇报，不写验收状态。
