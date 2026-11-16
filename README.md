# 库街区 Auto Sign Job

你可以在每天指定的时间自动运行签到脚本(包含社区签到和奖励签到)，而无需手动操作。

## 如何使用

### 1. Fork 仓库

首先，点击右上角的 `Fork` 按钮，将这个仓库 Fork 到你自己的 GitHub 账户下。

### 2. 设置 GitHub Secrets

为了确保签到脚本能够正常运行，你需要将必要的敏感信息([token](https://blog.tomys.top/2023-07/kuro-token/))存储在 GitHub
Secrets 中。

1. 进入你 Fork 后的仓库页面。
2. 点击 `Settings` 选项卡。
3. 在左侧菜单中选择 `Secrets and variables` > `Actions`。
4. 点击 `New repository secret` 按钮。
5. 按账号数量选择一种写法：

   - **单账号**：`Name` 字段输入 `TOKEN`，`Value` 字段输入你的签到脚本所需的 token。
   - **多账号**：`Name` 字段输入 `TOKENS`，`Value` 字段输入多个 token 的分隔列表。换行、逗号 `,`、分号 `;` 都可以作为分隔符，脚本会自动去重，例如换行分隔：

     ```
     tokenA
     tokenB
     ```

     也可以一行写完：`tokenA,tokenB`。

6. 点击 `Add secret` 保存。

两种写法可以共存：`TOKENS` 中的 token 先签到，旧 `TOKEN` 作为兜底，重复的 token 只会签到一次。

### 3. 多账号签到（可选）

配置了多个 token 时，脚本会依次为每个账号签到：

- 单个账号失败不会中断后续账号，剩下的账号照常签到。
- 所有账号的结果会聚合成**一条**通知推送，Bark / Server酱3 只会收到一条汇总，而不是每个账号一条。
- 汇总格式示例：

  ```
  [1] 昵称A -> 成功: 签到奖励签到成功, 社区签到成功
  [2] 昵称B -> 失败: 签到奖励签到失败, token 已过期
  共 2 个账号：成功 1，失败 1
  ```

- 当天已经签到过时，接口会返回 `code=1511`（`请勿重复签到`）。脚本把它当作**幂等成功**处理，汇总里记为 `checkin 今日已签到` / `sign_in 今日已签到` 并计入「成功」，不会再把 Actions 标红。
- token 失效（`/user/mineV2` 返回 `code=220`，`登录已过期，请重新登录`）仍然算失败，汇总里会写明 `token 已失效（code=220）`，不会和「已签到」混为一谈。
- 退出码语义：全部账号成功为 `0`；只要有任意账号失败就是 `1`。GitHub Actions 会把这次运行标记为失败，但其他账号的签到已经跑完了。
- 本地运行时如果不方便写 `TOKENS`，可以用编号变量 `TOKEN_1`、`TOKEN_2`…，脚本按编号数值顺序读取：

  ```bash
  TOKEN_1="tokenA" TOKEN_2="tokenB" python auto_checkin.py
  ```

### 4. 修改脚本（可选）

如果你需要修改签到脚本的逻辑，可以编辑 [`auto_checkin.py`](auto_checkin.py) 文件。

### 5. 触发工作流

#### 自动触发

工作流已经配置为每天北京时间早上 6 点（UTC 时间 22:00）自动运行。你无需手动操作，只需确保仓库中的代码和 Secrets 配置正确即可。

#### 手动触发

如果你想立即运行工作流，可以手动触发：

1. 进入你 Fork 后的仓库页面。
2. 点击 `Actions` 选项卡。
3. 在左侧菜单中选择 `Auto Sign Job`。
4. 点击右上角的 `Run workflow` 按钮，选择 `Run workflow` 即可手动触发。

### 6. 查看运行结果

每次工作流运行后，你可以在 `Actions` 选项卡中查看运行结果。如果签到成功，你应该能够看到相应的日志输出。签到失败则会报错。

### 7. 第三方结果推送（可选）

将每日运行结果推送到其他APP当中。

#### Server酱3

1. 前往[Server酱3官网](https://sc3.ft07.com/)，微信扫码登入。
2. SendKey-AppKey管理-添加新的AppKey。
3. 将获得的AppKey写入Github Secrets，`Name`字段为`SERVER3_SEND_KEY`。具体可参考本文的`2. 设置 GitHub Secrets`部分。
4. 下载[APP](https://sc3.ft07.com/client)并登入。

## 注意事项

- 确保 `TOKEN` / `TOKENS` 的安全性，不要将其直接写在代码中。
- 多账号场景下，`TOKENS` 中的**每一个** token 都会进入脱敏列表，日志和推送里不会出现 token 原文。
- 如果需要修改定时任务的执行时间，可以编辑 [`.github/workflows/auto_checkin.yaml`](.github/workflows/auto_checkin.yaml) 文件中的
  `cron` 表达式。
- 如需查看更详细的排查日志，可以在 Secrets 中添加 `DEBUG=true`，日志会自动对 TOKEN、Bark、Server酱等敏感值进行脱敏。

## 特别感谢

本项目的签到脚本部分参考了 [TomyJan-API-Collection](https://github.com/TomyJan/Kuro-API-Collection) 的 API 实现。感谢TomyJan
的开源贡献

## 贡献

如果你有任何改进建议或发现问题，欢迎提交 Issue 或 Pull Request.
