# 执行本人决定

### 2.2 执行决定

1. 本人选通知选项就发送其附带原文；本人给原话就原样发送；仅给结论（如接受并改期）则先起草clarify等确认。**本人批准文字不得trim、改写、替换空白或Unicode**。
2. 结论先写inbox，再登记 `observe/decision`：同一D连续version、steps，每步唯一step及kind/text或resume。只续做effectiveVersion（[决定字段](derived.md#决定)）；新决定才增加版本，步骤名跨版本不复用。旧已sent不撤回、不重发；旧未决仍阻断，不能标废止来掩盖。
3. manual会话不执行旧决定；本地动作在前、外发在后。改资料需本人明确要求、先备份；不要为了让事项完成编一条HR回复。
4. `ledger decision --id <ID> --step <step>`：sent跳过；superseded不做；pending/uncertain/needs_review只核对原尝试，不建新尝试；failed_no_send本轮不重试。not_started才按 followup 第 7 节，用本人原文与decision/step发送，冻结版本不改绑。
5. 全部有效动作完成且没有任何版本未决，最后写同D的unpaused；面试另记interview_decided。保存实际结果正文到inbox，再排队该结果版本done、标consumed。相同结果补发复用冻结正文，不拿本人决定原话当执行结果。
6. 本轮禁发/全局暂停保留ready不消费。暂时环境受阻按执行契约保留；人工接管、uncertain、failed_no_send或needs_review阻断时说明原因后consumed（note），不承诺恢复即自动外发，决定状态仍由ledger保留。

### 2.3 只收尾 ready

已done的决定只补保存的结果通知并consumed，不重发HR、不重做步骤。待处理事项本人已办，可记录已处理、发该结果版本done；结论是先不回/无需动作则记录并consumed，保持暂停，**不发done、不发summary**。只补通知或消费ready不算本轮实际执行动作；全局暂停/本轮禁发仍保留ready。
