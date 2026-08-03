# 重大问题发现

## 上下文混杂
经过测试，将 Mnemosync 同时接入 AstrBot QQ 和 Cherry Studio，系统直接将 QQ 群的上下文组合到了 Cherry Studio 的请求中。

在未绑定用户的情况下，不同用户、不同空间的上下文不应该混合在一起，这直接破坏了 Mnemosync 最基本的安全原则。