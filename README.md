# astrbot_plugin_group_mention

> 让机器人在**任意会话**里，向**指定群**发一条真 `@` 消息。

AstrBot 插件 · 仅 aiocqhttp（OneBot v11）· 无第三方依赖

---

## 它解决什么问题

AstrBot 的 LLM 工具默认只在**当前会话**里生效：

- 群内 `@` 需要知道对方 QQ 号，而 `get_group_members` 这类工具只能查**当前群**成员；
- 私聊里没有群上下文，`[at:QQ]` 也没有地方可以发出去。

于是「我在私聊里说：去群里 @ 我一下」这种需求做不了——模型会说找不到群，也找不到人。

这个插件补的正是这一段：**跨会话定位群、定位人、发一条带提醒的真 @**。

## 提供的能力

### `list_groups()`

列出机器人所在的群：群号 / 群名 / 人数。结果缓存 60 秒，避免每次对话都打协议端。

### `send_group_mention(group, who, text)`

向指定群发送一条 `@某人 + 文本` 的消息。

| 参数 | 说明 |
| --- | --- |
| `group` | 群号（纯数字）或群名的一部分。**完全同名优先**；前缀命中多个群会要求改用群号 |
| `who` | 要 @ 的人：QQ 号 / 群名片 / 昵称的一部分；填 `我` 表示当前说话的人 |
| `text` | `@` 之后要说的内容，可留空（只 @ 不说话），上限 120 字 |

匹配到多个人时不会瞎猜，会把候选（名片 + QQ）列出来让模型确认后再发。

## 安装

1. 把 `astrbot_plugin_group_mention` 整个目录放进 `AstrBot/data/plugins/`；
2. 在 WebUI 里重载插件（或重启 AstrBot）；
3. 确认日志出现 `Added llm tool: list_groups` 与 `Added llm tool: send_group_mention`。

## 配置

配置项在 `AstrBot/data/config/astrbot_plugin_group_mention_config.json`（面板里也能改）：

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enable` | bool | `true` | 总开关 |
| `allowed_groups` | list | `[]` | 只允许向这些群号发送；留空表示所有已加入的群 |
| `cooldown_seconds` | int | `15` | 同一个群两次发送的最短间隔，防刷屏 |
| `max_text_chars` | int | `120` | `@` 后面文本的长度上限 |

## 兼容性

- **平台**：仅 `aiocqhttp`（OneBot v11）。协议端需要实现 `get_group_list`、`get_group_member_list`、`send_group_msg`——SnowLuma、NapCat 等均支持。
- **AstrBot**：已在 4.26+ 实测。该版本会把 LLM 工具的入参包成 `ContextWrapper`，插件内部用 `resolve_event()` 取回真实事件，无需额外配置。
- **依赖**：无。



## 使用示例

私聊：

> **用户**：去「小鲸鱼的家」群里 @ 澄，让他晚上来开会
>
> **模型**：`list_groups()` → 找到 `824927256 / 小鲸鱼的家`
> `send_group_mention(group="小鲸鱼的家", who="澄", text="晚上来开会")`
>
> **群内实际收到**：`@澄 晚上来开会`

群名或人对上多个时：

> **模型**：`send_group_mention(group="测试", who="老王")`
> **工具返回**：「测试」匹配到多个群：测试群(111222333)、测试群二号(444555666)。请用群号指定。

## 已知限制

- 只能 @ **群成员**，不支持 @全体（需要的话配合 AtTool 的权限体系）；
- 目标群必须是机器人已经加入的群；
- 目前只发文本，不支持图片 / 语音；
- 私聊场景下 `who="我"` 取的是私聊对方，若对方不在该群会返回「群里没找到」。

## License

[MIT](LICENSE)
