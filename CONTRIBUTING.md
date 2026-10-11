# 参与开发

自有源码与文档采用 GPL-3.0-only。提交内容应由你有权提供，并按项目许可证贡献；第三方代码、字体或图片请说明来源及许可，保留原版权。本项目不要求版权转让。

先阅读 README、SECURITY.md、PRIVACY.md 和开发文档。Windows 使用独立虚拟环境安装 `desktop/requirements-build.txt`；在 `desktop` 执行 `python -m unittest discover -s tests -v`。测试使用合成数据，禁止用真实 Key、真实私人配置或实际锁屏代替测试适配器。Android 的 SDK/JDK 构建与 JVM 检查见 android/README.md。

提交前运行 `python tools/audit_publication.py --history` 和 `git diff --check`。源码公开不包含 `dist`、构建缓存、运行日志、个人 JSON、API Key、配对资料或签名私钥。用真实程序做试验时将数据目录放在仓库之外；开 PR 前检查暂存文件列表。

修改规则、网络、生命周期或锁屏边界时应增加能证明行为的测试。仅改文案无需机械添加镜像测试。PR 说明用户可见行为、验证结果与仍未验证的设备范围。保持当前边界：用户开始/暂停/结束周期，AI 仅提供灰区相关性，故障不算违约，不引入强制驻留、自删除、成果验收或隐蔽采集。

发布二进制时同时提供对应版本源码快照、许可证、第三方声明与 SHA-256；Windows 未商业签名、Android 使用开发签名时必须明示。使用新的 Android 私钥会影响覆盖升级，开发私钥不应进入 Git 或发布包。
