# 只读导出

需要生成求职台账表格时读本文件。导出包含私人求职资料，只留在本人控制的位置，不提交仓库或公开分享；导出不是外发授权，也不是判定平台尚未联系的依据。

```sh
python3 <技能目录>/scripts/ledger.py --workspace <工作区> export \
  --table events --format csv --out <工作区之外的新文件.csv>
```

参数：
- --table：events / threads / jobs，必填。
- --format：csv / jsonl，必填。
- --since / --until：YYYY-MM-DD，按工作区时区的自然日，两端包含；since 不得晚于 until。
- --platform / --account：可选筛选，账号规则见[账号说明](accounts.md)。
- --out：可选；不传时仍输出一个 JSON，导出文本在 content 中，不把裸 CSV 混进 stdout。

## 表与过滤

| 表 | 行与字段 |
|---|---|
| events | 每条公开事件，含平台、账号、ID、时间、类型、状态、职位/会话、我方文字及结构化字段；不输出读取器内部 _line |
| threads | 当前完整会话派生，含阶段、信息、未决、尾部、各版本决定；字段见[派生说明](derived.md) |
| jobs | 按平台、账号、jobKey 分组；company/title/jobId、lastAt、最近 screen 的 ID/时间/result/reasons/jdFacts/checks/salaryText，以及联系指标 |

events 用 at 过滤；threads / jobs 用 lastAt 过滤。先从完整历史派生实体，再筛选行；不裁掉旧 sent、已处理尾部、事实或决定版本后假装是新状态。最近 screen 按日志追加顺序取最后一条，未筛选的职位其筛选字段为空。职位 ID 不可见时沿用规范的 jobKey 回退，不按同名跨账号合并。

jobs.contacted 表示有 sent、already_contacted 或 adopted 的本地联系证据；hasContactAttempt 表示写过 pending，失败也保留。pending / uncertain 单列 unresolved，不冒充成功。未能归属 jobKey 的动作仍在 events / threads，不猜职位归属。contacted:false 只表示缺乏肯定记录，不能取代 check 或页面依据。

## 格式与原文

- CSV：UTF-8 BOM（utf-8-sig）、CRLF 记录分隔，固定安全列名；字典/数组在单元格中编码为 JSON。未列出的事件字段收进 extra JSON，原来的 extra 字段也保留为其子项。空表只有 BOM 与表头。
- JSONL：无 BOM，每条公开业务对象一行，只按 LF 分隔；字符串原值保持，包括批准文字的空白、Unicode 与换行。空表内容为空，列模式仍由 stdout.columns 给出。
- **CSV 公式安全**：外部文字去除前导空白/控制字符后的探针若以 =、+、-、@ 开头（含兼容字符），展示值前加单引号；只检测探针做 Unicode 规范化，原字符串不改。源台账与 JSONL 不加此前缀。数值字段保持数值。

这是表格安全与逐字交换的取舍：CSV 不是无损回放源，不拿加工后的单元格当本人批准原话。逐字数据选择 JSONL；不要将未防护的 JSONL 值或手工拆出的嵌套文字直接当表格公式导入。本次不调用或执行表格应用。

## 安全文件出口

--out 仅创建**工作区、其他求职工作区和套件目录外的新文件**；父目录须已存在，文件名不能含冒号（防备用数据流）。既有文件、文件符号链接和硬链接别名一律不覆盖；父目录别名先解析实际位置再检查。不能指向配置、台账、备份或恢复事务文件，也不能借恢复锁的只读入口写业务目录。

内容全部生成后，以同目录临时文件写入并 fsync，再用不覆盖的原子链接发布；竞争者先创建目标时失败，不覆盖、不留半输出。不支持原子硬链接发布的文件系统报错，不能退回截断写入；正常失败路径清理临时文件。不支持对恶意并发重命名整个父目录树的文件系统认证。

stdout 始终为 JSON：table、format、columns、count；无 --out 时另有 content，有 --out 时另有规范化 out 路径。版本 warnings 仍是独立字段，不混入 CSV/JSONL。退出码 2 时报告失败，不声称输出已完成，不绕过边界换工具覆盖业务文件。
