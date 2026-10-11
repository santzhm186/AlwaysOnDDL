# 第三方组件说明

Windows 构建使用 Python 3.14.7（包括其 Tcl/Tk 运行组件）、cryptography 50.0.2 和 PyInstaller 6.22.3。随新工程保存了构建环境中对应的许可文本：

- [Python 及所附第三方组件许可](licenses/Python-LICENSE.txt)
- [cryptography 许可说明](licenses/cryptography-LICENSE.txt)、[Apache 2.0](licenses/cryptography-LICENSE.APACHE)、[BSD](licenses/cryptography-LICENSE.BSD)
- [PyInstaller 许可与分发例外](licenses/PyInstaller-COPYING.txt)

Android 当前代码仅使用系统 API，没有将第三方运行库打包进 APK。Android SDK/JDK 与编译工具不包含在交付包中。完整的依赖来源和构建步骤见 README 与构建脚本；本文件记录本次使用的版本，不自动覆盖后续版本的许可文本。
