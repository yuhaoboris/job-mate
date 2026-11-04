# 尾部读取与处理

读取、比较、复核或收口会话尾部时读本文件；事件入口见[台账](ledger.md)。

先登记完整会话身份，使用返回的 threadKey；公司、招聘者从页面读取，不反推 key。tail 须持有效普通锁，read-at 位于最近读取与当前时间之间。

输入：
```json
{"anchor":{"speaker":"us","type":"text","text":"我方最后一条","label":"今天 10:00"},"messages":[{"type":"text","text":"HR尾部","label":"今天 10:32"}],"complete":true,"anchorUnique":true}
```
- anchor 为我方最后一条或已读到会话开头时的 `"start"`；messages 为之后全部 HR 消息，按页面顺序。complete / anchorUnique 缺省 false；本地已有 sent 却声明 start 转核对。
- type 为 text / card，卡片 text 含卡片类型和按钮文字；稳定平台 ID 已开启时可带 id，否则按发言方、类型、NFKC 去首尾空白并折叠空白后的内容摘要、绝对日期和分钟构成指纹。无日期上下文 HH:MM 不猜今天，时间缺失不是通配符。
- 日期按平台 timeLabels：今天 / 昨天 / 前天，星期取读取日前最近一次，月日取不晚于读取日的最近年份；明确年月日原样换算。只存 SHA-256 前 16 位及时间。
- 输出 tailId、baseTailId、result、newCount、reason、settled；每次读取唯一 tailId。默认比较最近 handled；处理后显式 --base 指向本次处理快照。待处理期间，旧 base 仅在与最新尾部等同时可复用，否则留下核对状态；不能跳过已观察内容。extended 处理后以其 tailId 复核。
- same / extended 要求可确认身份逐项相同 / 严格前缀；new_anchor 为无基准或锚点变化，整段分诊；mismatch 包括条数减少、内容/顺序冲突、不完整与时间/锚点歧义。无法确认数量时 newCount:null。
- 自己发送改变锚点时提交从基准锚点到末尾的完整 bridge（逐项 speaker）与 bridgeComplete:true。基准锚点唯一，HR 恰为已处理序列；新增我方活动的 attemptId 须对应同目标、本轮 sent、相同内容类型，发送时间在两次读取间且落在页面分钟区间前后 2 分钟。resume 卡片须匹配 sent 的简历版本文字，否则核对；编号本身不是证据。
- 显式基准 same 或完整 bridge 无遗漏的 new_anchor 才可能 settled:true。缺桥、陌生活动、pending / uncertain、发送前新 HR 等不自动 handled。核对状态只能本人带依据收口，换 base 得到 same 不能清除。
- 脚本内部生成 baseExplicit 与 bridgeEvidence：逐项 speaker，新增我方项再记 attemptId、type、digest、带时区分钟 pageAt，不存原文。validate 按历史前缀重算比较、bridge 和未决语义，不信任结果布尔。JSONL 仅按 LF 分行，批准文字逐字符保留。
- append 禁写 observe/tail。handled 带 tailId、baseTailId，只引用最新完整、唯一定位快照；须 settled:true 或 skill:user 加非空 evidence。脚本复制指纹。可选 listMark 为 `{label,digest,unread}`：列表时间标签、16 位摘要、非负未读数，不存摘要原文。
- 中断后先核对待处理快照及历史外发，以待处理快照复核，不从新锚点重开或重复外发。
