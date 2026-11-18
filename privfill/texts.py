"""Every text the program shows: the usage, and the form strings in each language."""
import os

USAGE = """privfill - let an AI assistant use your personal details without seeing them.

Personal details live in one local encrypted vault. The assistant writes documents with
placeholders such as {{name}}; this program fills in the real values locally.
New details are entered by you in a local form. Nothing this program prints
ever contains a value.

  privfill.py init                       create the vault (no-op if it exists)
  privfill.py keys                       list stored item names (never values)
  privfill.py protocol                   print a summary of the entry protocol
  privfill.py form '<JSON>' | <file>     show an entry form described by the protocol
  privfill.py add <item>                 shorthand: enter one free-text item
  privfill.py manage                     open the local window to view, edit, delete
                                         and back up items (for you, not the assistant)
  privfill.py mcp                        serve the tools keys, form, render and pack as a
                                         Model Context Protocol server on stdin/stdout
  privfill.py render <master> <output>   fill a copy; the master stays untouched
                                         (the output path may contain placeholders)
  privfill.py fill <file>...             fill files in place
  privfill.py pack <archive> <file>...   bundle files into a zip archive
                                         (every path may contain placeholders)
  privfill.py verify                     check the activity log was not altered
  privfill.py restore [<version>]        list the vault's backups, or put one back
  privfill.py migrate [<old file>]       move a plain-text data file of an earlier
                                         version into the vault
  privfill.py unlock [add|change|remove] [keyring|passphrase]
                                         list or change how the vault is unlocked
  privfill.py guard                      for a tool hook: read a tool call as JSON on
                                         standard input; exit 0 to allow, 2 to deny

Put --json before the subcommand for a single JSON object as output.
Placeholders: {{item}}, {{item|YYYY/MM/DD}}, {{item|last4}}, {{item|first6}}

Exit codes: 0 ok, 1 usage or runtime error, 2 unknown item or bad format,
            3 cancelled by the person, 4 invalid protocol,
            5 wrong passphrase (guard: only 0 allow, 2 deny)

Command output is in English (it is read by the assistant). The form, which is
read by the person, follows the system language (English or Chinese); override
with PRIVFILL_LANG=en|zh.
"""


def pick_lang():
    v = os.environ.get("PRIVFILL_LANG") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "zh" if v.lower().startswith("zh") else "en"



LANG = pick_lang()
HEADER_NAMES = {"项目", "Item"}  # header cells of the data file table; not usable as item names

# Text shown to the person (form, passphrase dialog and the old data file). Everything else is English.
UI = {
    "title": ("Enter personal information", "录入个人信息"),
    "privacy": ("Stored only on this computer, encrypted, in {file}. It is never sent to the AI.\n"
                "Type it however is natural; the program tidies the format.",
                "内容只加密保存在本机的 {file}，不会发送给 AI。\n怎么顺手怎么写，格式由程序整理。"),
    "required": ("Required", "必填"),
    "keep": ("Already stored. Leave it as is to keep it.", "已有内容，不改就保持原样。"),
    "save": ("Save", "保存"),
    "cancel": ("Cancel", "取消"),
    "foot_optional": ("{n} items, all optional", "共 {n} 项，都可以留空"),
    "foot_required": ("{n} items, {r} required", "共 {n} 项，其中 {r} 项必填"),
    "foot_keys": ("Enter saves, Esc cancels", "回车保存，Esc 取消"),
    "bad_required": ("This one is required.", "这一项必填。"),
    "bad_date": ("Could not read this date. Write it with digits, like 2005-03-04 or 2005/3/4.",
                 "没看懂这个日期，请写成类似 2005年3月4日 或 2005-03-04。"),
    "bad_date_invalid": ("This date does not exist. Check the year, month and day.", "这个日期不存在，请核对年月日。"),
    "bad_phone": ("A phone number should contain only digits (7 to 15 of them).", "电话号码里应该只有数字（7 到 15 位），请核对。"),
    "bad_id_shape": ("This ID number should have 18 characters (the last may be X).",
                     "身份证号应为 18 位（最后一位可以是 X），现在位数或字符不对。"),
    "bad_id_check": ("The check digit does not match; one character is probably mistyped.",
                     "身份证号的校验位对不上，可能有一位输错了，请核对。"),
    "bad_email": ("This does not look like an email address (name@example.com).", "邮箱格式不对，应类似 name@example.com。"),
    "bad_number": ("A number is needed here.", "这里需要一个数字。"),
    "bad_choice": ("Pick one of the options in the list.", "请从下拉框里选择一项。"),
    "bad_pattern": ("This does not match the expected format; see the hint.", "格式不符合要求，请看提示。"),
    "pass_title": ("privfill vault", "privfill 保险库"),
    "pass_ok": ("OK", "确定"),
    "pass_new": ("Choose a passphrase for your personal details.\n"
                 "You will type it each time they are used. It cannot be recovered if you forget it.",
                 "为你的个人信息设置一个口令。\n每次使用这些信息时都要输入它；忘记后无法找回。"),
    "pass_again": ("Type the passphrase once more.", "请再输入一次口令。"),
    "pass_empty": ("The passphrase cannot be empty.", "口令不能为空。"),
    "pass_mismatch": ("The two passphrases did not match.", "两次输入的口令不一致。"),
    "pass_unlock": ("Enter your passphrase to unlock your personal details.", "请输入口令以解锁你的个人信息。"),
    "pass_retry": ("That passphrase was not right. Try again.", "口令不对，请再试一次。"),
    "mgr_title": ("privfill: stored details", "privfill：已保存的个人信息"),
    "mgr_privacy": ("Only you can see this window. Values stay hidden until you click Reveal.",
                    "只有你能看到这个窗口。值默认隐藏，点「显示」才会出现。"),
    "mgr_items": ("{n} items", "共 {n} 项"),
    "mgr_none": ("Nothing is stored yet.", "还没有保存任何内容。"),
    "mgr_pick": ("Pick an item on the left.", "请在左侧选择一项。"),
    "mgr_reveal": ("Reveal", "显示"),
    "mgr_hide": ("Hide", "隐藏"),
    "mgr_hides_in": ("Hides itself after {s} seconds.", "{s} 秒后自动隐藏。"),
    "mgr_new_value": ("New value", "新的值"),
    "mgr_edit_hint": ("Type the new value however is natural; it is tidied like in the form.",
                      "怎么顺手怎么写，格式和录入表单一样会被整理。"),
    "mgr_save": ("Save change", "保存修改"),
    "mgr_saved": ("Saved.", "已保存。"),
    "mgr_delete": ("Delete", "删除"),
    "mgr_deleted": ("Deleted.", "已删除。"),
    "mgr_confirm_delete": ("Delete \u201c{name}\u201d? This cannot be undone.", "删除“{name}”？此操作无法撤销。"),
    "mgr_export": ("Export backup\u2026", "导出备份…"),
    "mgr_import": ("Import backup\u2026", "导入备份…"),
    "mgr_exported": ("Backup written with {n} items. Keep its passphrase: it cannot be recovered.",
                     "已导出 {n} 项的备份。请记住备份口令，忘记后无法找回。"),
    "mgr_imported": ("Restored: {a} added, {r} replaced.", "已恢复：新增 {a} 项，替换 {r} 项。"),
    "mgr_confirm_replace": ("These items already exist and would be replaced:\n{names}\n\nReplace them?",
                            "下面这些项已经存在，将被替换：\n{names}\n\n是否替换？"),
    "mgr_save_dlg": ("Save backup as", "备份保存为"),
    "mgr_open_dlg": ("Choose a backup file", "选择备份文件"),
    "mgr_no_qt": ("the manager window needs PyQt6, which is not installed", "管理窗口需要 PyQt6，但它没有安装"),
    "migrate_confirm": ("privfill moved your details into the encrypted vault and checked every one.\n\n"
                        "Delete the old plain-text file now? It is overwritten first, but on SSDs and journaling "
                        "file systems traces may remain.",
                        "privfill 已把你的信息移入加密保险库，并逐项核对无误。\n\n"
                        "现在删除旧的明文文件吗？删除前会先覆盖，但在 SSD 和日志型文件系统上仍可能留下痕迹。"),
    "file_head": ("# Personal details (private)\n\n"
                  "> Managed by privfill. Do not let an AI read this file and do not commit it anywhere.\n\n"
                  "| Item | Value |\n|---|---|\n",
                  "# 个人信息（私密）\n\n> 由 privfill 管理。不要把这个文件交给 AI 读取，也不要提交到任何仓库。\n\n"
                  "| 项目 | 内容 |\n|---|---|\n"),
}


def ui(key, **kw):
    text = UI[key][1 if LANG == "zh" else 0]
    return text.format(**kw) if kw else text
