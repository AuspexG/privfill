# 在 Claude Code 里使用 privfill

两步：让 Claude 知道规则，再让 Claude Code 在每次访问你的信息之前询问你。

## 1. 规则

安装 skill：

```
./install.sh --skill
```

它会把 `skills/privfill/SKILL.md` 和程序（`privfill.py` 与 `privfill/` 包）一起复制到 `~/.claude/skills/privfill/`。任务涉及个人信息时 Claude 会自动加载这个 skill。安装后新开一个会话。

skill 的说明是英文的，因为它是写给 Claude 读的，不是给你读的；表单界面会按系统语言显示中文。

## 2. 防护配置

```
./install.sh --claude            # 或 --claude-sandbox，见下
./install.sh --claude-remove     # 再把它撤掉
```

它把下面这些合并进 `~/.claude/settings.json`，文件里其他内容保持不变（旧版本备份为 `settings.json.privfill-backup`，新增了什么记录在 `settings.json.privfill.json`，撤除时只撤这些）：

| 设置 | 作用 |
|---|---|
| `permissions.deny`：`Read(~/.privfill/**)`、`Edit(~/.privfill/**)` | Claude 的文件工具永远读不到、改不了数据目录。 |
| `hooks.PreToolUse`，matcher `*`：`privfill.py guard` | 每次工具调用先经 guard 判断（见 `docs/guard.md`）：触及数据目录或已填好输出文件的读取、搜索、列目录和 shell 命令都会被拒绝，理由里不出现任何路径。 |
| `permissions.allow`：`Bash(python3 ~/.privfill/privfill.py <命令> *)`，以及 skill 里那份副本的同样规则 | 程序自己的命令不再弹出询问。guard 仍会逐条检查，比如参数里藏的命令替换。 |
| `sandbox`（仅 `--claude-sandbox`） | 打开 Claude Code 的 Bash 沙箱，让拒绝规则在操作系统层面也挡住 shell 读取，同时让程序自己的命令在沙箱外运行以便打开保险库。沙箱作用于所有 shell 命令（包括网络访问），所以单独作为一个选项。 |

旧版本的 `ask` 规则（`Read(~/.privfill/**)`、`Edit(~/.privfill/**)`、`Bash(*privfill*)`）会被移除：改由 guard 确定地判断，不再每次询问。

重启 Claude Code，然后按 [verify-guard.md](../verify-guard.md)（英文）在真实会话里检查一次。

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

- Claude Code 按文本匹配 shell 命令，guard 也只按调用的字面判断，所以两者都不是沙箱。`--claude-sandbox` 为 shell 命令加上操作系统层面的一层。
- 任何以你的身份运行的程序都能改 `~/.claude/settings.json`，所以这些规则只在助手不去改写它时有效。[managed-settings.md](../managed-settings.md)（英文）介绍把它们放进只有 root 能改的托管配置。
- 这些都防不了以你的身份运行的恶意软件。
