# 发送准备

每次可能外发前、处理本人本轮限制/追加授权、执行决定或系统回执时读本文件。这里只规定台账门禁；页面、输入框和成功证据仍按[执行规则](execution.md)核对。

## 输入与结果

```sh
python3 <技能目录>/scripts/ledger.py --workspace <工作区> prepare --run <runId> --json '<意图>'
```

check 接收同一份完整 JSON 意图及 run，只读调用同一判定路径，不写 pending、不消费次数。prepare 不传 --json 时从 stdin 读 JSON。意图字段：kind、platform、可选 account、company、title、location、recruiter、jobUrl、text 或 resume、reason、targetRole、jdFacts、checks、asks、tags、decision、step、pageState。不可见页面身份为 null，不能猜；jobId 从可读 URL 提取。文字动作与简历动作分开。

例如回复意图：
```json
{"kind":"reply","platform":"boss","company":"合成公司","title":"开发","recruiter":"合成招聘者(HR)","text":"合成回复","reason":"依据QA条目"}
```

- 允许：退出0，allowed:true、attemptId、eventId、authorizedBy。记录脚本生成的 pending 后才可进行后续操作；每次可能外发分别 guard。
- 拒绝：退出1，allowed:false、reasons:[{code,message}]；没有新增 pending。interval 另给 retryAfterSeconds，等够后重新准备。
- 参数、版本或损坏数据：退出2，停止外发并报告。若写入后续期失败，保留原 pending 对账，不能盲目重试准备。

status、runId、skill、at、各ID、派生键、authorizedBy、decisionVersion、dedupGrant、profileRev、styleRev、内部绑定均由脚本生成。prepare 不收这些字段；append 终态只带原 attemptId 与已见事实，冻结字段由原 pending 继承，不能自行覆盖。本人批准原文含空白、换行、全角字符时逐字输入，不 trim、不规范化。

## 冻结身份与回填

首次 pending 非空身份不可改，原 null 字段只可补全一次；省略已知字段由脚本继承，不等于可以显式清空。原始 platform/account/jobId 不做 NFKC 合并；人类页面身份分别规范化，键保持精确比较，非空键不可借补字段重新定向。原 jobKey 为 fallback 且已非空时，新增不同 ID 不能改绑原尝试，须本人核对。归并保留首个身份表示及合法补齐值，不用最后一次页面字段覆盖原目标。低层旧错绑历史仍保留 sent 事实与身份冲突供审计，不能当新准备授权；原始实际 sent 的目标也继续参与去重。

## 点击前复查

guard 只复核原 pending：当前有效锁/账号、本轮限制、原授权来源及绑定、全局/会话暂停、接管、关闭、停止名单、其他未决、决定有效版本或 ack 的同目标邀约与去重。自身 pending 不算其他未决，额度包含自身但不二次扣减；已用等于上限仍可执行该 pending。原来源失效不另选 grant 或长期授权；被新版废止的 pending 保留待核对，不抹去。dedupGrant 也须仍存在、绑定匹配且恰已占用一次。拒绝返回相同的 reasons/code/message 结构、退出1；参数或损坏数据退出2。通过才续锁并立即操作；正常等待后重读页面重新 guard，工具异常则按[停止范围](execution.md#结果不明时的停止范围)收尾，不自动续发。

## 本轮限制先于授权

取锁前先问清动作、目标及数量。只看不发用 --no-send；明确收窄要求用单次 --restrict：
```json
{"kinds":["reply"],"scope":{"companies":["甲公司"]},"max":1,"quote":"这次只回复甲一条"}
```

kinds 是 greet / intro / reply / resume / nudge 的非空不重复子集；max 为非负整数或 unlimited。没有限制时对应写全部动作、all:true、unlimited，不推断更宽权限。restriction 缺省 null，不是授权。no-send 优先全部；restriction 依次核对动作、目标、跨全部来源的总次数，之后才选发送授权。长期权限、grant 和本人决定都不能绕过限制。一个对象表达不清的指令先澄清。

本轮内限制、例外与冻结绑定不改写；续锁不清零。计数按本 run 的平台/账号/attemptId 首次 pending，失败或结果不明均不退款。不能靠换轮、换 grant 编号、换决定版本扩大本人仍有效的限制。新目标未登记时，先在禁发锁中记录 screen/观察身份，再释放重取具体范围的锁；未发送不占次数，已有发送用量不能重置。

## 发送授权与例外

- greet / intro：长期 authorization.apply.send 为 true，缺省 false；否则需 apply_send grant。
- reply / resume：长期 authorization.followup.reply 为 L2，缺省 L2；L1 只起草，发送需覆盖的 reply grant 或有效本人决定步骤。
- followup.nudge 是独立布尔，缺省 true；不再使用 level/L3。**当前 nudge 策略尚未启用，prepare 拒绝 nudge**，不得根据旧 canFollowup 字段外发。
- 非 ack 的本人决定必须明确 decision + step，按有效版本匹配，不能失败后回退为长期授权。

追加授权用可重复 --grant，原话仅审计，不解析原话授予权限：
```json
{"item":"reply","scope":{"companies":["甲公司"]},"max":1,"quote":"本轮明确授权回复甲一条"}
```

item 为 apply_send / reply / nudge / hours / dedup_once。scope 必须且仅为 all:true、threads 列表、companies 列表或 jobs 列表之一；max 为正整数或 unlimited，hours 不带 max。全部字段严格校验，不接收编号/绑定/用量。no-send 与 grant 共存报错。编号由锁生成 g1、g2 等；正常优先长期权限，再取编号最小且仍可用的发送 grant。hours 仅越过范围内首次招呼的时段，不是发送权限。

具体 threads / jobs scope 在取锁协调区内从已校验事件解析唯一原始平台/账号/目标。职位 ID 原样比较；fallback 公司、职位及平台会话分量分别规范化，不靠转义后键判断原始身份。未登记、身份不足、多目标/多账号时取锁失败且不写锁。后来出现同形键或当前账号变化不能重绑扩大权限；status 展示 scopeBindings、restriction 及 grant 用量。

### 逐个去重放行

仅 dedup_once 可豁免 greet 的本地 already_contacted / reposted_suspect：scope 必须是 jobs 的单个已唯一绑定职位，max 必须为整数1。同轮同一原始目标重复登记报错。它不提供发送授权，仍须 long_term 或 apply_send，且所有其他门禁通过。

实际遇去重命中且准备成功时，pending 冻结 dedupGrant 编号；独立按同 run 唯一 pending 计数，失败/不明不退款。没有命中、其他门禁失败或只读 check 均不消费。未决外发、公司拒绝/风险、停止名单、暂停/接管、时段、间隔与配额一概不豁免。页面已经联系必须记 already_contacted，不点首次沟通；greet 的可选 pageState.contacted 是采集到的布尔页面事实，true 一律拒绝。BOSS 的「继续沟通」按此处理。

## 本人决定与系统回执

本人决定先 observe/decision 登记 version、steps 及原文/简历版本，再 prepare。有效版本以外步骤拒绝；任一版本存在 pending/uncertain 时先核对；同 decision + step 已 sent 不重发。成功准备后冻结 decisionVersion 与 authorizedBy=decision:<ID>@v<版本>。本人批准文字免自拟禁用词/字数检查，但不免其他安全门禁，不免本轮限制。

系统 ack 必须 reply + decision:D + step:ack，并有同一原始平台/账号/会话的 interview_invited(D)。先登记邀约及 paused(D)、写待决定与通知，再准备中性回执；ack 只认 L2 或 reply grant，不是本人决定，不带决定版本。文字不含数字、点/号/周/星期/今天/明天/后天/上午/下午/晚上。同事项只有一次 ack 尝试。它仅可越过 paused(D)，不解除暂停；其他事项、无ID暂停及会话任何未决仍阻断。准备/回执失败不影响已持久化上报。
