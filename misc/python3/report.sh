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

echo "         wc -l  files" > port-summary
echo "       -------- -----" >> port-summary
function summarize {
  printf "  "
  printf "$1" | cut -d"-" -f2 | tr -d '\n'
  printf "  "
  awk '{ loc += $1 } END { printf "%6d",loc }' "$1"
  printf "   "
  wc -l "$1" | awk '{ printf "%3d",$1 }'
  echo
} >> port-summary
summarize port-done
summarize port-todo

printf " total " >> port-summary
function sum {
  ndone=$(head -n3 port-summary | tail -n1 | awk "{ print \$$1 }")
  ntodo=$(head -n4 port-summary | tail -n1 | awk "{ print \$$1 }")
  echo "$ndone + $ntodo" | bc -s
}
printf " $(sum 2)" >> port-summary
printf "   $(sum 3)" >> port-summary
echo >> port-summary

echo >> port-summary
printf "%% todo " >> port-summary
function perc {
  ndone=$(head -n3 port-summary | tail -n1 | awk "{ print \$$1 }")
  ntodo=$(head -n4 port-summary | tail -n1 | awk "{ print \$$1 }")
  echo "r = ($ntodo / ($ndone + $ntodo)) * 100; scale=0; r / 1" | bc -sl
}
printf "     $(perc 2)" >> port-summary
printf "    $(perc 3)" >> port-summary
echo >> port-summary
