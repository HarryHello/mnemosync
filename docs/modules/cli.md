# 命令行环境

> **系统版本**: v0.0.0  
> **文档状态**: 初稿  
> **创建时间**: 2026-03-25  
> **最后更新**: 2026-03-26  
> **作者**: HarryHelloo  
> **最后更新**: HarryHelloo

## 目的
为了方便无GUI环境的运行以及开发测试, 我们选择提供属于 Mnemosync 的命令行应用.

## 运行示例
理想情况下, 命令行运行应当类似如下流程:  
### 初始化
```bash
$ docker compose build    # 通过 docker 部署 Mnemosync
$ mnemosync init          # 初始化
```
输出: 
```terminaloutput
Mnemosync initializing...
Success!
Use `mnemosync login` to start the cli environment, 
or use `mnemosync help` to get more information.
```

### 登入
```bash
$ mnemosync login
```
输出: 
```terminaloutput
╭───────────────────────────────────────────────────────────────╮
│                                                               │
│  │  ╲╱  ││ \ │ ││  ___│  ╲╱  │  _  ╱  ___\ ╲ ╱ / ╲ │ /  __ ╲  │
│  │ .  . ││  \│ ││ │__ │ .  . │ │ │ ╲ `──. \ V /│  ╲│ │ /  ╲╱  │
│  │ │╲╱│ ││ . ` ││  __││ │╲╱│ │ │ │ │`──. ╲ ╲ / │ . ` │ │      │
│  │ │  │ ││ │\  ││ │___│ │  │ │ \_/ ╱╲__╱ ╱ │ │ │ │╲  │ \__╱╲  │
│  \_│  │_╱╲_│ ╲_╱╲____╱╲_│  │_╱╲___╱╲____╱  \_/ ╲_│ ╲_╱╲____╱  │
│                                                               │
│                         Mnemosync                             │
│                         v0.0.0                                │
│                                                               │
╰───────────────────────────────────────────────────────────────╯

Welcome to Mnemosync!
Please login with accout and password.
The default account and password are all 'mnemosync'.
Account: Mnemosync
Password:*********                 # 密码输入不显示
```

#### 首次登入要求修改密码
```terminaloutput
Please change your account and password.
New account: [user_account]
New Password: **********
```

### 进入 MnemosyncCLI
```terminaloutput
Login Successfully!
Use `help` to get commands information.
```

### 常见命令
#### HELP
在 CLI 中使用 `help` 以显示可用指令
```bash
Mnemosync > help
```
```terminaloutput
Usage: COMMAND [OPTIONS]

Common Commands:
  help        Show this page
  logout      Exit this CLI environment
  stop        Stop the Mnemosync server
  
API-Key Commands:
  ls-keys                  List existing api-keys
  show-key [key_id]        Show the specific key
  generate-key             Generate a new api-key
  
LLM Service Commands:
  ls-service               List existing llm service provider
  ad-service               Add a new llm service provider
  rm-service [srv_id]      Remove a llm service provider
  show-service             Show the information
  ls-models [srv_id]       List available models
  
Models Commands:
  set-main-model [srv_id] [model]      Set the main model for Mnemosync
  set-assist-model [srv_id] [model]    Set the assist model for Mnemosync
  test-model [srv_id] [model]          Test if able to connet to a model
```

#### 登出
在 CLI 中使用 `logout` 以退出 CLI, 并保持 Mnemosync 在后台运行
```bash
Mnemosync > logout
```

```terminaloutput
Logout Mnemosync CLI.
```

#### 结束进程
在 CLI 中使用 `stop` 以结束 Mnemosync
```shell
Mnemosync > stop
```

```terminaloutput
Stopping Mnemosync server...
```


### Mnemosync 对外 API 服务
该部分是指 Mnemosync 将自身模拟为模型提供商, 使用 api-key 提供服务

#### 列出当前已生成的 api key
在 CLI 中使用 `ls-keys` 以列出所有已生成的 key 以及 对应 id 和注释
```shell
Mnemosync > ls-keys
```

```terminaloutput
key            key-id    annotation
sk-*****abcd   1         AstrBot
sk-*****qwer   2         Airi
```

#### 显示特定 key
在 CLI 中使用 `show-key` 显示特定 key 的具体内容
```shell
Mnemosync > show-key 1
```

```terminaloutput

sk-abcdabcdabcdabcdabcdabcdabcd

Annotation: AstrBot

Do not let others get your keys!
```

#### 生成新 key
在 CLI 中使用 `generate-key` 以生成新的 key
```shell
Mnemosync > generate-key
```

```terminaloutput
It is recommanded to map one key to one platform.
Please enter the annotation for the new key:
Test

Your new api-key is:
sk-qwertyuiopasdfghjklzxcvbnm

Do not let others get your keys!
```

### 具体模型提供商配置
#### 列出当前提供商
```shell
Mnemosync > ls-service
```

```terminaloutput
service-id       base-url                     api-key
openai           https://api.openai.com/v1    sk-********enai
```
#### 新增模型提供商
```shell
Mnemosync > ad-service
```

由用户填入新提供商的具体信息
```terminaloutput
Add new llm service provider:
Custom service id: openai
This id has been already used!

Custom service id: siliconflow
base URL: https://api.siliconflow.cn/v1
API key: *******************
```

#### 移除模型提供商
```shell
Mnemosync > rm-service openai
```

```terminaloutput
LLM service provider openai has been removed!
```

#### 列出特定提供商可用模型
```shell
Mnemosync > ls-models siliconflow
```

```terminaloutput
Pro/MiniMaxAI/MiniMax-M2.5
Pro/zai-org/GLM-5
Pro/moonshotai/Kimi-K2.5
Qwen/Qwen3.5-397B-A17B
...
```

### Mnemosync 模型配置
这里是指配置 Mnemosync 回应时使用的模型.  
主模型是产生回答的模型, 推荐使用参数比较大的模型; 辅助模型是用于判断情绪、清洗提示词等辅助工作的模型, 使用较小模型可以加快提高回应速度且降低 token 使用量.  

#### 设置主模型
示例: 将主模型设置为 siliconflow 的 Qwen/Qwen3.5-397B-A17B
```shell
Mnemosync > set-main-model siliconflow Qwen/Qwen3.5-397B-A17B
```

```terminaloutput
Change main model to Qwen/Qwen3.5-397B-A17B from siliconflow successfully!
```

#### 设置辅助模型
*假设添加了另一个服务, id 自定义为 siliconflow1*
```shell
Mnemosync > set-assist-model siliconflow1 Qwen/Qwen3-8B
```

```terminaloutput
Change assist model to Qwen/Qwen3-8B from siliconflow1 successfully!
```