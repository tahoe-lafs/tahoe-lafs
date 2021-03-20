#!/bin/sh
set -euo pipefail

cd "$(dirname $0)/../.."

printf "" > port-done
printf "" > port-todo

for filepath in $(find . -path ./.tox -prune -false -o -name \*.py)
do
  if test ! -s "$filepath" || $(grep -q 'Ported to Python 3' "$filepath")
	then
    bucket='done'
	else
    bucket='todo'
	fi
  echo $(wc -l "$filepath") >> "port-$bucket"
done

sort -nro port-done{,}
sort -nro port-todo{,}

echo "       loc    files" > port-summary
echo "     -------- -----" >> port-summary
function summarize {
  printf "$1  " | cut -d"-" -f2 | tr -d '\n'
  awk '{ loc += $1 } END { printf "%6d",loc }' "$1"
  printf "   "
  wc -l "$1" | awk '{ printf "%3d",$1 }'
  echo
} >> port-summary
summarize port-done
summarize port-todo
