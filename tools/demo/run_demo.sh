#!/bin/sh
# Produce the real command outputs for the demo GIF, against a fake vault with fake data only.
# Usage: run_demo.sh <lang en|zh> <work root>   (normally called by make.sh)
set -eu
LANG_CODE="$1"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
W="$2/work-$LANG_CODE"
rm -rf "$W"
mkdir -p "$W/fakehome" "$W/out"
export HOME="$W/fakehome"
export PRIVFILL_VAULT="$W/fakevault/vault.json"
export PRIVFILL_PASSPHRASE_TEST="demo passphrase only"
export PRIVFILL_LANG="$LANG_CODE"
export QT_QPA_PLATFORM=offscreen
P="$REPO/privfill.py"
cd "$W"

if [ "$LANG_CODE" = zh ]; then
  NAME="姓名"; PHONE="手机号"
  SPEC='{"protocol":1,"title":"报名信息","note":"报名表需要这两项。","fields":[{"name":"姓名","label":"姓名","hint":"例如 张三","type":"text","required":true},{"name":"手机号","label":"手机号码","hint":"例如 138 0013 8000","type":"phone","required":true}]}'
  ANSWERS='{"姓名":"王小明","手机号":"139 0013 9000"}'
  printf '# 报名表\n\n姓名：{{姓名}}\n手机：{{手机号}}\n紧急联系时只显示后四位：{{手机号|last4}}\n' > registration.md
  OUTNAME='out/{{姓名}}_报名表.md'
else
  NAME="full name"; PHONE="phone"
  SPEC='{"protocol":1,"title":"Registration","note":"Needed for the registration form.","fields":[{"name":"full name","label":"Full name","hint":"e.g. Jane Roe","type":"text","required":true},{"name":"phone","label":"Mobile phone number","hint":"e.g. 138 0013 8000","type":"phone","required":true}]}'
  ANSWERS='{"full name":"Alex Example","phone":"139 0013 9000"}'
  printf '# Registration\n\nName:  {{full name}}\nPhone: {{phone}}\nShown on the badge: ***{{phone|last4}}\n' > registration.md
  OUTNAME='out/{{full name}}_registration.md'
fi
printf '%s' "$SPEC" > spec.json
printf '%s' "$ANSWERS" > answers.json

PRIVFILL_FORM_TEST="$ANSWERS" python3 "$P" form "$SPEC" > form.txt 2>&1
python3 "$P" keys > keys.txt 2>&1
python3 "$P" render registration.md "$OUTNAME" > render.txt 2>&1
ls out > outname.txt
echo "done $LANG_CODE"
