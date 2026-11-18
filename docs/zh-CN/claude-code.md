# 在 Claude Code 里使用 privfill

两步：让 Claude 知道规则，再让 Claude Code 在每次访问你的信息之前询问你。

## 1. 规则

安装 skill：

```
./install.sh --skill
```

它会把 `skills/privfill/SKILL.md` 和程序（`privfill.py` 与 `privfill/` 包）一起复制到 `~/.claude/skills/privfill/`。任务涉及个人信息时 Claude 会自动加载这个 skill。安装后新开一个会话。

skill 的说明是英文的，因为它是写给 Claude 读的，不是给你读的；表单界面会按系统语言显示中文。

## 2. 权限

在 `~/.claude/settings.json` 的 `permissions` 里加入 `ask` 规则：

```json
{
  "permissions": {
    "ask": [
      "Read(~/.privfill/**)",
      "Edit(~/.privfill/**)",
      "Bash(*privfill*)"
    ]
  }
}
```

| 规则 | 作用 |
|---|---|
| `Read(~/.privfill/**)` | Claude 用读文件工具读数据目录前要你批准。 |
| `Edit(~/.privfill/**)` | Claude 修改这个目录里的文件前要你批准。 |
| `Bash(*privfill*)` | 任何提到 privfill 的命令（包括每次调用程序）执行前要你批准。 |

已有 `permissions` 时把 `ask` 数组合并进去，不要整个替换。重启 Claude Code，然后让 Claude 运行一次 `privfill keys`，确认会弹出询问。

批准时看一眼命令：正常情况下是 `privfill.py` 加子命令。如果是 `cat`、`grep` 或读文件工具指向数据目录，拒绝它。

## 3. MCP 服务（可选）

除了通过 shell 运行程序，Claude 也可以把它当作工具调用。注册一次即可：

```
claude mcp add --scope user privfill -- python3 ~/.privfill/privfill.py mcp
```

之后 Claude 能看到四个工具：`privfill_keys`、`privfill_form`、`privfill_render` 和 `privfill_pack`。它们的输入结构里带着录入协议，Claude 不需要额外文档就能调用。每个工具返回的 JSON 对象和对应命令加 `--json` 时相同：只有项目名、次数、状态和退出码（退出码不为 0 时 `isError` 为 true）。没有任何工具接收值、返回值或运行命令。第一次调用工具时才打开保险库，口令对话框也在那时出现。

若希望每次调用前都询问你，把这些工具加进 `ask` 规则：

```json
"ask": ["mcp__privfill__privfill_form", "mcp__privfill__privfill_render", "mcp__privfill__privfill_pack"]
```

**与 guard 的关系。** guard 放行这个服务自己的工具（无论注册时用什么服务名），就像放行命令行上的 `privfill.py` 一样，所以 `privfill_pack` 可以打包已填好的输出文件。其他所有工具，包括其他 MCP 服务的工具，都按输入判断：只要某个输入指向数据目录或已填好的输出文件，就会被拒绝。服务和 guard 使用同一个数据目录和同一份输出记录，所以无论通过哪条路径生成的输出都受同样保护。

## 这层保护的边界

`ask` 规则由 Claude Code 执行，对命令行按文本匹配，不是沙箱。它的价值是让每一次访问都经过你的眼睛。想要更硬的限制，可以再禁止直接读取保险库（其中项目名是明文，内容是密文）：

```json
"deny": ["Read(~/.privfill/vault.json)"]
```

privfill 自己不受影响：读文件的是程序，不是 Claude 的读文件工具。
