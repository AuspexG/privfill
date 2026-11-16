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

## 这层保护的边界

`ask` 规则由 Claude Code 执行，对命令行按文本匹配，不是沙箱。它的价值是让每一次访问都经过你的眼睛。想要更硬的限制，可以再禁止直接读取数据文件：

```json
"deny": ["Read(~/.privfill/profile.md)"]
```

privfill 自己不受影响：读文件的是程序，不是 Claude 的读文件工具。
