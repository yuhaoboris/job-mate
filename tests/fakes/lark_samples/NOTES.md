# lark-cli 1.0.96 脱敏实测样本

采集日期：2026-11-04 UTC（采集程序记录）；版本输出为 `lark-cli version 1.0.96`。采样由父任务按本人授权执行；本批开发仅离线脱敏，没有重跑采集或外发。原始 stdout/stderr、退出码、请求参数和目标核验材料只在私有目录，不提交。

## 十份样本与通道

| 文件 | 实际采样 | 输出/退出码 |
|---|---|---|
| send.json | bot 给已核验本人发送一次文本，固定幂等键 | stdout / 0 |
| send-dup.json | 同一个幂等键重复发送 | stdout / 0；与 send 相同 message_id |
| mget.json | `im +messages-mget` 查询该消息 | stdout / 0 |
| list.json | `im +chat-messages-list`，含本人引用回复 | stdout / 0 |
| send-reject.json | 格式合法但不存在的全零 open_id 拒绝探针 | **stderr / 1**，stdout 为空 |
| native-get.json | `api GET /open-apis/im/v1/messages/<message_id>`，按 ID 获取指定消息，不称为原生 mget | stdout / 0 |
| native-list-page1.json | `api GET /open-apis/im/v1/messages --params <JSON>`，page_size:1 的第一页 | stdout / 0，has_more:true，非空 page_token |
| native-list-page2.json | 用上一页 token 读取第二页，含本人引用回复 | stdout / 0，has_more:false，空字符串 token |
| profile-list.json | 补采 `profile list`，只读 | stdout / 0，**顶层数组** |
| auth-status.json | 指定配置档 `auth status --json --verify`，只读 | stdout / 0，**顶层诊断对象**，没有 IM 的 ok/data 信封 |

JSON 文件只保存所述通道的脱敏响应树，不添加私有 metadata、argv 或本机路径。profile 中实际返回的配置档名也替换为稳定合成值。字段集合、数组顺序、缺失字段、空字符串、布尔和数值类型保留；不把未采到的字段补成 null。

## 实测字段路径

- 成功信封为 ok:true、identity:bot、data；拒绝为 ok:false、identity:bot、error。错误码位于 error.code，实测 99992351；error.type/subtype、message、log_id、troubleshooter 保留结构。诊断 URL 中公共 API method_id 和错误 code 是公共方法标识，不是个人 ID。
- send：data.message_id、data.chat_id、data.create_time。create_time 是 `YYYY-MM-DD HH:MM:SS` **无时区字符串**。
- shortcut mget：data.messages[]、data.total；shortcut list：data.messages[]、has_more、page_token、total，以及 meta.pagination.complete/pages/items。shortcut 消息 content 是纯字符串，create_time 是 `YYYY-MM-DD HH:MM`，**秒已丢失且无时区**。list 的引用是 reply_to，不是 parent_id。
- sender 是对象：机器人 sender.id 是 app_id（cli_ 家族），id_type:app_id、sender_type:app；shortcut 还出现 open_bot_id、name、sender_i18n_names。本人 sender.id 是 open_id（ou_ 家族）、id_type:open_id、sender_type:user；不能把 bot app_id 当成本人。mget 缺 is_agent，list 有该布尔字段；原生 get/list 的 sender 字段集合也各自保留。
- message_app_link 中 openChatId 与 chat_id 对应，position 与 message_position 对应。
- 原生 get 为 data.items[]；原生 list 为 data.items[]、has_more、page_token。原生 create_time/update_time 是 **epoch 毫秒十进制字符串**；文字是 body.content 中的 **JSON 字符串**，解析后取 text。本人普通引用回复有 parent_id/root_id，两者均指向发送消息；没有偷换成 shortcut 的 reply_to。
- 原生 list 请求使用 container_id_type:chat、container_id、start_time/end_time（epoch 秒十进制字符串）、sort_type:ByCreateTimeAsc、page_size:1；第二页请求 token 来自第一页响应。原 token 是不透明私人标识，已稳定替换，不能解码它推断游标。原生未用 shortcut 自动分页或把 meta.pagination 当成原生协议。

父任务已依据官方[获取指定消息](https://open.feishu.cn/document/server-docs/im-v1/message/get.md)与[会话历史](https://open.feishu.cn/document/server-docs/im-v1/message/list.md)核对原生路径；这里的字段事实仍以本次实测响应为准。

补采 profile/auth 仍由父任务按本人授权执行；本批只读取已落盘文件，没有执行采集器、真实 CLI 或网络。profile 数组保留 active/effective/effectiveSource 的实际结构，不凭 active 猜本轮选定应用。auth 的 appId 与选定 profile 对应；机器人证据是 identities.bot.available/verified。顶层 identity/verified 可能对应 user，不作为机器人验证依据；bot.openId **不是** ownerId。

## 脱敏与时间边界

个人、会话、消息、app、bot、tenant、log ID、分页 token、姓名/i18n、正文、message_position 及 URL 内嵌值均替换为固定合成值，不使用原 ID 的散列或保留其私有前后缀。相同原值只映射到同一合成值，ID 家族、长度/字符类型和关系保留。诊断文本中的 ID/示例掩码也已替换。公共 URL 的协议、域名、路径与方法标识不伪装成私人信息。

日期统一按整天平移到合成日期；原生 epoch 数字字符串增加同一个整天毫秒偏移，create/update 相对时差不变。两套时间**不互相转换**，不推断 shortcut 的时区，不补秒或伪造毫秒。适配器未来应使用原生 GET 补查可信时间，不能把发送成功后的时间补查失败当成重新发送的理由；这不是本批实现的适配器兼容证明。

隐私检查在私有 raw 目录存在时提取结构化 ID、未知前缀 tenant/log、URL 内嵌 ID 和分页 token；同时扫描姓名、应用名及 profile 名；另用完全合成单测验证检查器能抓泄漏且不误报公共错误码。认证提示、scope 字符串替换为合成文字，不反映真实授权范围；含偏移的认证时效字符串也按同一整天偏移平移，保留原始表示而不作为消息游标。公开仓库没有 raw 时不依赖私有数据，仍检查十份样本的类型、精度和关系。

## 未采样与辅助测试契约

contact 查询、`im +messages-reply` **没有独立实测响应**。替身的 profile/auth 现在使用补采形状，contact 仍只返回调用者明确提供的合成响应，缺省拒绝；reply 复用已测 send 形状构造并标注推演，不冒充独立实测。参数语法子集、动态分页/幂等状态和注入故障也只是替身测试契约，见[替身说明](../README.md#飞书替身t63纯离线)。

未覆盖其他 API 路径/POST、消息编辑/删除、非文本、群聊、话题回复、权限错误分类大全、所有分页边界、生产幂等时效及跨时区展示语义。不调用真实 CLI 来补猜这些空白；适配器的支持边界以规范化单测和已采字段为限，不代表这些未采能力已经核验。
