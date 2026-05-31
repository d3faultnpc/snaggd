#!/bin/bash
# PostToolUse hook: syntax-check any .py file after Edit/Write/MultiEdit

FILE=$(echo "$CLAUDE_TOOL_INPUT" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('path',''))" 2>/dev/null)

if [[ "$FILE" == *.py ]]; then
  ERROR=$(python3 -m py_compile "$FILE" 2>&1)
  if [ $? -ne 0 ]; then
    echo "SYNTAX ERROR in $FILE:"
    echo "$ERROR"
    echo "Fix syntax before proceeding."
    exit 1
  fi
fi
exit 0
