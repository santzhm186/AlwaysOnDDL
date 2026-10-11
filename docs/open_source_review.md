# 开源前审查 · 2026-10-11

对象：AlwaysOnDDL / IronWill 源码、已有 Git 提交、Windows 发布脚本及 Android 工程。依据为当前代码、合成数据测试、本机打包验证和附带许可；不是对所有平台的渗透测试、外部模型服务认证或法律权属鉴定。

## 结论

自有代码和文档采用用户选择的 GPLv3，明确为 `GPL-3.0-only`。当前文件与可达 Git 历史的定向扫描未发现真实 API Key、证书私钥、私人契约配置或运行日志；唯一带凭据 URL 命中是 `.invalid` 域名的故意拒绝用例。不能把未命中模式扫描等同于“绝不可能泄露”。

历史中的普通邮箱经用户确认保留；原有提交不改写，后续提交使用 GitHub 隐私邮箱。拉取远程新增的 README 快速开始，整合后正常更新 main，避免覆盖他人变更。公开仓库前仍应核对 GitHub 可见性、Security 私密报告入口和二进制发布内容。

## 发现与处理

| 项目 | 发现 | 处理 |
|---|---|---|
| 项目许可证 | 原仓库只有第三方许可，没有自身许可 | 新增完整 GPLv3、COPYRIGHT、README 授权说明、自有 Python/Java 文件版权与 SPDX 标识；第三方材料保持原许可 |
| 二进制许可随附 | 原 EXE 未嵌入完整许可文本，Android 没有许可入口 | Windows 内嵌 LICENSE、第三方声明及 licenses 目录；两端新增离线许可入口，Android APK 包含 GPL 全文 |
| 静态组件声明 | cryptography 与 Python 使用不同 OpenSSL 版本，wheel含Rust组件 | 增补OpenSSL许可与实际版本；使用wheel SBOM核对32个外部Rust crate的归档哈希并保存原始版权/许可，7个内部crate沿用cryptography许可；保留Python完整附加段落 |
| Git / 源码包排除 | 原规则未覆盖 `.env`、个人 JSON、配对目录及部分私钥后缀 | 增加忽略与打包排除，发布审查脚本检查当前文件、历史、许可证一致性和忽略规则 |
| 局域网资源上限 | TLS/HTTP 每连接超时，但线程并发无上限 | 限制 16 个并发；满额连接关闭，释放后可恢复；仍不适合公网暴露 |
| 异常动作参数 | action 为数组/对象会抛 TypeError | 先校验字符串，返回 400 且不入队；异常连接不输出包含对端信息的追踪 |
| 发布文档 | README 指向 Git 忽略的 dist 下载文件 | 改为 Releases 页面及本机构建说明，不宣称尚未上传的附件可下载 |
| 隐私与反馈 | 缺少独立数据说明与漏洞报告约定 | 新增 PRIVACY、SECURITY、CONTRIBUTING；强调日志明文、历史隐私规则非追溯、分享前检查 |

## 审查确认的代码边界

- 首次打开不开始监督、AI 或局域网监听；重开周期保持暂停。AI 每次启动需主动启用，Key 的持久化需勾选。
- 本地隐私规则优先，进程/标题规则先于 AI；缓存隔离周期、目标、规则、服务、模型和提示版本。新标题需稳定停留 3 秒，自动请求至少间隔 30 秒。
- AI 超时、无效输出、unknown 与实际偏离分开，云端结果不独立推动三档或锁屏。网络工作在后台，旧周期/设置结果不作用于当前周期。
- Key 按服务绑定并通过当前 Windows 账户 DPAPI 可选保存；AI 与 Android 同步均禁止重定向，远程地址要求 HTTPS。
- relay 使用证书指纹与令牌、有限字段、动作白名单、周期检查、幂等和请求体上限，不执行模型命令、任意 shell、远程锁屏或删除。
- Android 无障碍声明不允许取窗口内容，服务/接收器导出范围受限，监测显式启动且可停止；开发签名私钥只在本机忽略目录。
- 图标为工程内简单矢量资源，没有外部商标、字体或图片文件。原型和个人实例配置不进入本仓库源码包。

## 保留的限制

静态依赖检查以已安装cryptography 50.0.2 wheel自带的CycloneDX SBOM为准，32个外部crate归档均通过SBOM SHA-256校验，原始许可/版权、选择的双许可分支和清单哈希保存在licenses目录。包含构建工具和过程宏以保守保留通知；self_cell选择Apache分支，unicode-ident保留Unicode要求，target-lexicon保留LLVM例外。未将第三方源码或wheel直接当作本项目GPL代码重新授权。更换wheel或构建工具链后应重做此检查，SBOM本身不能证明所有上游权属声明绝对正确。

2026-10-11查询OSV，精确版本cryptography 50.0.2与PyInstaller 6.22.3未返回匹配漏洞。这不覆盖Python/Windows/Android系统组件、零日问题或未来数据库更新，不构成“无漏洞”证明。

Windows 单实例互斥、正式安装器、商业代码签名、长期托盘与多 DPI 验收未完成；并行启动多个实例可能覆盖同一数据目录。日志、缓存理由、目标、配对资料是本地明文；DPAPI 不能防同账户恶意程序。配对令牌没有按设备撤权，局域网拒绝服务仍可能发生。外部模型的数据政策、实际兼容与费用不由本审查保证。

Android 本次只验证许可相关源码能编译、APK 随附许可和开发签名；手机通知、ROM 后台、无障碍撤权、手表转发仍需设备验收。当前 targetSdk 35 不作为应用商店发布合规证明。独立 Gradle 构建尚未验证。

许可条款核对来源：[GPLv3 全文 / SPDX](https://github.com/spdx/license-list-data/blob/main/text/GPL-3.0-only.txt)、[PyInstaller 打包例外](https://pyinstaller.org/en/stable/license.html)、[Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html)、[OpenSSL 4.0.3](https://github.com/openssl/openssl/blob/openssl-4.0.3/LICENSE.txt)。GPLv3 不禁止商用；发布衍生二进制需按许可提供对应源码。第三方静态依赖变化后应重新检查通知要求，首次公开发布前不应把本记录当作权属担保。

最终执行结果见 [verification.md](verification.md)。
