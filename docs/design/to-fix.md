# 面板中发现的部分问题

## 配置上游的placeholder不好
baseUrl不应该使用阿里的dashscope作为placeholder。要么不要任何公司的，要么用openai

## 配置上游时缺少测试
配置上游时缺少测试按钮，用户无法及时测试baseUrl是否正确

## 配置上游后缺少自动刷新
配置上游api后在模型配置页面需要手动刷新一次才能得到模型

## 检索插件缺少代理设置
没法使用代理来检索和下载插件（如https://gh-proxy.org/）

## 缺少一键重启服务
无法便捷地重启服务

进一步地，可以分离面板和实际业务，这样可以允许用户始终启动较轻量的面板并直接管理服务

同时vite在编译时警告：

```plaintext
[plugin builtin:vite-reporter] 
(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rolldownOptions.output.codeSplitting to improve chunking: https://rolldown.rs/reference/OutputOptions.codeSplitting
- Adjust chunk size limit for this warning via build.chunkSizeWarningLimit.
```

## 无法导入导出人格
如题，无法导入导出人格。之前实现的酒馆人物卡导入也不在面板中有相应接口。

# 后台/CLI的问题
## 缺少重启指令
缺少`mnemosync restart`指令

## --daemon / -d 启动时没有提示端口
和终端启动不同，后台启动时没有告知用户目前使用的端口

# 接口问题
## 模型元数据缺失
使用mnemosync的/model端口查询模型时缺少上下文、能力、模态等元数据，不便于前台自动配置
