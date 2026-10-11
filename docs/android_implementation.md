# IronWill Android 0.1 实施规格与交付边界

核查日期：2026-10-09。本文描述当前源码与实际构建；另存的 `roadmap_spec.md` 是扩展方案，不能当作已经落地的功能清单。

## 1. 实际技术栈

原生 Java 8 语言级别、系统 Activity/View、SharedPreferences 本地持久化、Android 通知、UsageStats、可选 AccessibilityService。运行时不依赖第三方库，不调用云模型。构建使用 JDK + Android SDK 自带 aapt/javac/d8/zipalign/apksigner，已生成 APK 并验证签名。附有 Gradle 工程文件供 IDE 导入，但本次没有用 Gradle 完成另一轮构建。

`minSdk=28`（Android 9）、`compileSdk=35`、`targetSdk=35`。这是个人自行安装版本；截至核查日，Play 手机新应用目标 API 要求为 36，Wear 新应用为 35。商店发布须升级目标、重测行为并完成用途/权限审核。[Play 目标 API 要求](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en)

没有要求 Device Owner、Profile Owner、root、ADB 授权或管理员权限。不读取旧工程的敏感设置进行预填。

## 2. 已实现产品语义

| 能力 | 0.1 实现 |
|---|---|
| 用户承诺 | 原文输入、本地保存，不由程序评价真实性 |
| 现实处境/拖延后果 | 可选原文输入；提醒引用原文，不编造；不上传 relay |
| 当前主线 | 用户填写；电脑导入须单独确认 |
| 执行周期 | 开始/继续、暂停、结束；无固定作息或午夜清零 |
| 红线判断 | 仅用户选择的应用包名；不把所有离开工作软件都判为错误 |
| 触发 | 活动屏幕上持续观察到同一红线应用约 60 秒 |
| 回应 | 返回主线暂缓 2 分钟；值得/喘息各暂缓 15 分钟 |
| 未回应 | 不自动记作违约；同一提醒 5 分钟内不重复生成新提醒 |
| 断网 | 本地监测和普通通知继续；不升档，不终局 |
| 穿戴 | 普通通知供系统桥接；没有独立手表应用或已读证明 |
| 系统锁屏 | 仅应用内经明确确认的一次测试；日常提醒无自动锁屏 |
| 强约束/终局 | 未启用；没有自动复锁、自删除、修改密码或删外部文件代码 |
| AI/正文相关性 | 未实现；界面明确仅应用级判断 |

2 分钟返回冷却、约 60 秒停留及 5 分钟未回应去重是第一版可修改工程参数，不是对最终三档设计的永久裁定。原始强约束与终局策略保留在设计待决策范围，原型自毁保险关闭。

## 3. 模块与责任

| 类/文件 | 职责 |
|---|---|
| `MainActivity` | 输入、周期命令、应用选择、权限说明、配对、任务导入、远程选择、记录与单次锁屏测试 |
| `Store` | 本地字段、周期 ID、暂缓时间、提醒 ID/所属周期、回答去重与最近 200 条事件 |
| `MonitorService` | 用户启动的 FGS；约 2.5 秒一次本地观察；约 15 秒尽力同步；生成通知 |
| `ReminderGate` | 无 Android 依赖的本地提醒门控：停留、切换、暂停、睡眠、冷却、待处理提醒 |
| `PackageObserverService` | 可选无障碍，只从窗口状态事件读取 packageName |
| `ActionReceiver` | 处理不可变、显式通知 PendingIntent；验证当前提醒 ID 与周期后记录选择 |
| `SyncClient` | 固定证书指纹 HTTPS、token、状态、设备心跳、claim、远程选择 |
| `RelayHttpPolicy` | 2xx 状态接受逻辑，含动作的 202 排队成功 |
| `build.ps1` | SDK 直接构建、开发签名和验证 |
| `test.ps1` | 纯 JVM 核心断言 |

主 UI 与服务在单一应用进程。第一版没有 Room、DataStore、完整事件溯源或事务 outbox；不宣称可同步全局惩罚计数。未来扩展路线见 `roadmap_spec.md`。

## 4. 权限与能力真实状态

Manifest 声明：INTERNET、ACCESS_NETWORK_STATE、VIBRATE、POST_NOTIFICATIONS、PACKAGE_USAGE_STATS、FOREGROUND_SERVICE、FOREGROUND_SERVICE_SPECIAL_USE。无障碍服务用 `BIND_ACCESSIBILITY_SERVICE` 保护绑定入口。

用户首次安装不会自动开启监测。点击开始前要求承诺、主线、至少一个红线应用、可用通知及至少一条观察路径。授权由系统页面和用户操作完成。权限未开启时显示真实缺失，不记录成违约。

- 通知：Android 13+ 请求 POST_NOTIFICATIONS，同时检查全应用通知状态和 challenge 渠道。用户可以关闭渠道；应用不新建渠道规避。
- 使用情况访问：检查 AppOps 状态，引导到使用情况访问设置。不把普通 Manifest 声明当作已经获准。
- 无障碍：仅引导系统设置；`connected` 实际服务回调才表示连接。配置 `canRetrieveWindowContent=false`。
- 没有 `SYSTEM_ALERT_WINDOW`，0.1 没有覆盖层。没有申请摄像头、麦克风、健康、位置、通知读取、全盘文件、短信或联系人。
- FGS `specialUse` 说明为用户主动启动的持续本地提醒周期；这说明实际用途，不保证应用商店审核通过。

官方边界：[通知权限](https://developer.android.com/develop/ui/compose/notifications/notification-permission)、[无障碍生命周期](https://developer.android.com/reference/android/accessibilityservice/AccessibilityService)、[FGS 类型](https://developer.android.com/develop/background-work/services/fgs/service-types)。

## 5. 观察管线与可信度

无障碍仅监听 `TYPE_WINDOW_STATE_CHANGED`，复制包名与本机单调时间；不读取 event.text、source 节点、页面树、标题、contentDescription、键盘、截图。服务连接优先使用最近有效包名；否则在已授权情况下从 UsageStats 查询前台/后台事件。

屏幕不交互或 Keyguard 显示时重置本次停留，并标记恢复后需新鲜观察。未知包名、自身应用、非红线或用户暂缓均重置本次候选。相同候选持续约一分钟才通过门控。网络 claim 返回后再检查周期、暂停状态、屏幕状态及当前候选包名，旧上下文不直接触发提醒。

UsageStats 初次查询最多近一分钟，之后增量查询并保留一秒重叠；屏幕状态边界或墙钟回退使缓存失效。查询仍受系统历史、多窗口和厂商延迟影响，可能漏提醒，不能证明物理触摸。无障碍窗口事件也不能证明真实人手输入或成果。没有未知数据到违约计数的映射。[UsageStatsManager](https://developer.android.com/reference/android/app/usage/UsageStatsManager)、[AccessibilityEvent](https://developer.android.com/reference/android/view/accessibility/AccessibilityEvent)

时间使用 `SystemClock.elapsedRealtime`；墙钟只用于人类可读记录。没有按日期调整档位。进程重启后重新观测，不回放睡眠期间的“惩罚”。第一版未实现完整跨重启 boot ID 时钟账本；手机重启后需用户重新打开并继续周期，开始/继续会清理暂缓和旧提醒。[SystemClock](https://developer.android.com/reference/android/os/SystemClock)

## 6. 生命周期与恢复

用户点击开始时启动前台服务并立即展示运行状态通知。服务内部使用 Handler 做尽力观察，网络使用单线程后台 executor。服务被正常销毁时取消观察、关闭 executor 并清理当前挑战通知；不能假设所有终止都有回调。

服务使用 START_STICKY 请求系统在可行时恢复；不保证系统一定恢复。应用被系统/用户停止后，周期仍可能保存在 active 状态，但主 UI 会显示“监测未运行，请点击继续”。没有 BootReceiver，不声称开机自动运行；没有永久 wake lock、高频精确闹钟或伪装音频。

Android 12+ 限制后台 FGS 启动；Android 13+ 用户可通过系统停止前台服务应用；省电/Doze 影响执行和网络。因此 2.5 秒观察、15 秒心跳只是可运行时的目标节奏。FGS 启动异常记录健康事件，不转为处罚。[后台启动限制](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)、[用户停止服务](https://developer.android.com/develop/background-work/services/fgs/handle-user-stopping)、[Doze](https://developer.android.com/training/monitoring-device-state/doze-standby)

## 7. 通知与回答幂等

两个稳定渠道：低重要性的 `session_v1`，高重要性的 `challenge_v1`。运行状态通知 ongoing + local-only；挑战通知为普通可清除通知，可供手表桥接。挑战采用 private 可见性与通用 publicVersion，系统锁屏公共版本不展示私密主线/后果。

挑战保存随机 intervention ID、当前 cycle ID、创建单调时间。三项通知按钮使用显式 `ActionReceiver` 与 immutable PendingIntent；携带提醒 ID，接收后查本地当前提醒、周期、active 状态和有效期。重复/旧 ID/跨周期/过期选择不生效。开始、暂停、结束均使旧 pending ID 无效；开始时取消旧挑战通知。

本地记录只写“发出提醒（不代表用户已看到）”。普通通知成功提交不等于展示、震动或已读；滑掉通知不写成用户承认失败。不存在基于已读假设的升档。

没有 full-screen Intent。全屏通知权限用途受限制，0.1 不借此强制拉起界面。[全屏 Intent 限制](https://source.android.com/docs/core/permissions/fsi-limits)

## 8. 单次锁屏测试

用户必须在应用内点“手动锁屏能力测试（一次）”，再次确认。只有已连接的无障碍服务可执行 API 28+ `GLOBAL_ACTION_LOCK_SCREEN`；API 30+ 先检查系统动作列表。记录平台返回结果，不记录用户人格/成果。

该动作不是设备管理员路径，不设置密码，不阻止正常解锁，不自动复锁。系统拒绝时显示失败而不无限重试。`DevicePolicyManager.lockNow` 没有被调用；Device Admin/Owner 不是默认前置要求。[AccessibilityService](https://developer.android.com/reference/android/accessibilityservice/AccessibilityService)

## 9. 本地数据与隐私

SharedPreferences 文件为 `ironwill_local_v1`，处于应用私有存储。字段包括 vow、task、reality、consequence、blocked、phase、cycleId、开始时间、暂缓时间与原因、pending ID/所属周期、最近事件数组；启用配对后保存电脑地址、证书指纹、令牌与随机 deviceId。

事件最多保留 200 条，UI 最多展示最近 80 条。事件只记录周期操作、选择、提醒提交及手动测试结果；不记录包级活动全文或页面文本。输入长度限制防止异常巨大通知载荷。

备份关闭，并用旧版 fullBackupContent 与新版 dataExtractionRules 排除 sharedpref/database/file 的云备份和设备迁移。厂商实际行为仍需真机验收。没有额外自研数据库加密；依赖应用沙箱和设备平台加密，不能声称 token 在本地获得独立硬件加密。

“所有数据本地”指默认无外网传输；主动配对后会向用户指定电脑发送设备随机 ID、型号、状态、stage=0、active、操作与去重 ID。手机不上传 vow/task/reality/consequence/包名/页面；电脑 goal/oath 仅经用户主动导入。无遥测与分析 SDK。[备份建议](https://developer.android.com/privacy-and-security/risks/backup-best-practices)

## 10. Relay v1 互通

配对需三个用户输入：`base_url=https://IP:8765`、64 位 `certificate_sha256`、`bearer_token`。只允许 HTTPS；TrustManager 验证叶证书 SHA-256 与固定指纹完全一致并检查证书有效期。主机名检查的替代校验同样核对同一叶证书指纹。禁止 trust-all、HTTP 和自动重定向。请求超时各 3.5 秒，响应最多 64 KiB。

所有接口带 `Authorization: Bearer ...`，只接受成功 2xx；重定向不执行。状态 JSON 必须匹配 protocol_version=1 才保存首次配对。

| 接口 | Android 行为 |
|---|---|
| `GET /v1/state` | 获取桌面当前状态；导入先展示 goal/oath，再确认 |
| `POST /v1/device` | `device_id/device_name/status/stage:0/active`；约 15 秒尽力提交 |
| `POST /v1/claim` | 正式提醒前申请 `{device_id,event_id}`；granted=false 延后，断连则手机轻提醒 |
| `POST /v1/action` | 先 GET 当前 `desktop.id`，再发 `{device_id,command_id,action,session_id}`；不缓存失败动作 |

动作枚举 BACK/WORTH/BREATHE/PAUSE/RESUME。202 表示排队成功，UI 文案为“已提交，电脑将按当前状态处理”，不声称已应用。409 表示周期变化，要求重新选择；旧命令不能排队到下一周期。WORTH 是直接选择，不强制收集理由。

V1 只提供内容复制、明确远程选择与短期提醒去重；没有跨端全局档位账本。网络分区下可能重复轻提醒，不声称全局唯一通知或不可绕过。

## 11. 穿戴

普通 challenge 通知可由 Wear OS 系统桥接；服务 ongoing 通知不用于手表提醒。没有直接读手表状态、健康、位置或佩戴数据。没有应用层已读回执。手机/手表按钮的旧 PendingIntent 仍受同一提醒 ID 和周期校验。[Wear OS 桥接](https://developer.android.com/training/wearables/notifications/bridger)

厂商非 Wear OS 穿戴依赖配套应用的通知转发支持；兼容性必须测试用户实际型号，不能从 Wear 文档推断全部手环可用。

## 12. 构建、签名、版本

`build.ps1` 参数可传 Android SDK、JDK、Build Tools 版本和编译 API；默认可使用环境变量。脚本在自己的 build 目录生成 manifest 副本，加入 package，不修改源 manifest 或 SDK。流程：aapt → javac --release 8 → jar → d8 → APK → zipalign → apksigner sign/verify。

输出为 `build/IronWill-Android-0.1.apk`，开发签名保存在 `build/ironwill-development.jks`。公开开发密码只适用于个人测试，不能当作正式发行密钥。覆盖升级需同一应用 ID 和签名；换证书的迁移另行设计。构建不下载、不安装到手机、不打开手机服务。

使用 target 35 的侧载版不会因 Play 要求而自动符合商店发布资格。无障碍和 FGS 用途声明需独立审查；不冒充辅助残障工具，不做阻止用户卸载的控制。[无障碍分发政策](https://support.google.com/googleplay/android-developer/answer/10964491?hl=en)、[受限制设置](https://support.google.com/android/answer/12623953?hl=en)

## 13. 已验证与未验证

已完成实际资源/Java/DEX 编译、APK 签名验证，以及 26 项纯 JVM 断言。断言覆盖首次观察、60 秒阈值、冷却、包切换、非红线复位、屏幕休息、暂缓、通知不可用、未回应去重、暂停、未知观察、跨任意日期边界、时钟回退及 200/202/301/401/500 响应类别。

未进行手机或模拟器安装与界面验收、Android 后台真机测试、锁屏动作执行、手表转发、手机到 Windows 的实际 Wi-Fi 配对。TLS/状态协议代码审查和桌面测试不能替代 Android 真机互通。当前交付状态是“可构建且已构建的首版安装包”，不是“已在你的设备验证完成”。

首次真机验收按以下顺序：

1. 新安装保持空白与不监测；拒绝权限时能使用设置且没有虚假可用状态。
2. 用户填写信息、选择红线并授权后开始，切入红线应用约一分钟收到通知。
3. 每个回答产生一次记录，Worth/Breathe 暂缓 15 分钟，重复旧按钮不生效。
4. 暂停/结束/重新开始，旧提醒不影响新周期；跨午夜不重置。
5. 锁屏睡眠、回到非红线、断开无障碍、关闭通知，不把未知时间累加成违约。
6. 系统停止服务后重开显示真实状态，继续可恢复；没有开机自动保证。
7. 错误指纹/令牌/HTTP/重定向拒绝配对；正确指纹成功；断开网络仍本地提醒。
8. 桌面 202 命令排队正确提示；周期切换 409 不重放；导入须确认。
9. 手表实际通知转发与旧按钮去重；只在测试设备手动确认一次锁屏能力。

## 14. 后续工作与不冒充完成的部分

优先补真机可靠性与可见性，再决定完整叠窗、三档约束、正式终局、端侧相关性、持久化事件账本、跨设备路由与长期能耗优化。若这些成为下一轮明确范围，采用完整测试与用户策略版本管理；不把它们藏在“第一版已完成”的表述里。
