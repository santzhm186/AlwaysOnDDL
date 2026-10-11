# 第三方组件说明

IronWill 自有源码和文档按 GPL-3.0-only 授权，见根目录 LICENSE 与 COPYRIGHT；本文件列出的第三方材料保留各自许可证。本工程未修改 Python、cryptography、OpenSSL、Tcl/Tk 或 PyInstaller 的源代码。

Windows 构建使用 Python 3.14.7（包括其 Tcl/Tk 运行组件）、cryptography 50.0.2 和 PyInstaller 6.22.3。随新工程保存了构建环境中对应的许可文本：

- [Python 及所附第三方组件许可](licenses/Python-LICENSE.txt)
- [cryptography 许可说明](licenses/cryptography-LICENSE.txt)、[Apache 2.0](licenses/cryptography-LICENSE.APACHE)、[BSD](licenses/cryptography-LICENSE.BSD)
- [PyInstaller 许可与分发例外](licenses/PyInstaller-COPYING.txt)
- [OpenSSL Apache 2.0 许可](licenses/OpenSSL-LICENSE.txt)。本机 Python TLS 使用 OpenSSL 3.5.7，cryptography wheel 静态包含 OpenSSL 4.0.3；文本取自 OpenSSL 4.0.3，3.5.7 同属 Apache 2.0。

Python-LICENSE.txt 中包含本发行版的 Tcl/Tk、libffi、bzip2 等附加许可段落，不能只保留文件开头的 PSF 段落。cryptography 许可可选择 Apache 2.0 或 BSD，本工程按其 BSD 条款保留声明；其静态组件仍适用各自条款。PyInstaller 的打包例外以所附 COPYING 原文为准。

Windows EXE 内嵌上述全文，“关于与许可”可离线查看；Android APK 内嵌本项目 GPLv3 全文并提供同名入口。源码包保留 LICENSE、COPYRIGHT、本文件与完整 licenses 目录。发布 EXE/APK 时应在同一发布页提供相应版本的完整源码、构建脚本与校验清单；仓库 main 分支以后会变化，不能代替特定二进制版本的源码快照。

cryptography wheel自带CycloneDX SBOM列出39个Rust组件，其中7个内部组件沿用cryptography自身许可，其余32个外部crate的精确版本和SHA-256见[组件清单](licenses/cryptography-rust-components.json)。已从crates.io获取对应归档并逐一校验SBOM哈希，随附[原始版权及许可全文](licenses/Rust-THIRD-PARTY.txt)，包括构建工具和过程宏。双许可证中优先选择Apache-2.0；self_cell选Apache-2.0而非GPL-2.0-only，target-lexicon保留LLVM例外，unicode-ident同时保留Unicode-3.0要求。工具tools/collect_dependency_licenses.py可基于当前已安装wheel重新生成。

Android 当前代码仅使用系统 API，没有将第三方运行库打包进 APK。Android SDK/JDK 与编译工具不包含在交付包中。完整的依赖来源和构建步骤见 README 与构建脚本；本文件记录本次使用的版本，不自动覆盖后续版本的许可文本。

许可来源：[GPLv3 / SPDX](https://github.com/spdx/license-list-data/blob/main/text/GPL-3.0-only.txt)、[PyInstaller](https://pyinstaller.org/en/stable/license.html)、[Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html)、[OpenSSL 4.0.3](https://github.com/openssl/openssl/blob/openssl-4.0.3/LICENSE.txt)。修改构建依赖或 wheel 时应重新核对静态组件及其通知要求；这份清单不构成第三方法律意见。
