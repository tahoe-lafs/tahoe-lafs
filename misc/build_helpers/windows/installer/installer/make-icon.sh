#! /bin/bash
# Based on <http://stackoverflow.com/a/23688878/393146>
# converts the passed-in svgs to ico

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 svg1 [svg2 [...]]"
    exit 0
fi

temp=$(mktemp -d)
declare -a res=(16 24 32 48 64 256)
for f in $*; do
    mkdir -p $temp/$(dirname $f)
    for r in "${res[@]}"; do
        inkscape -z -e $temp/${f}${r}.png -w $r -h $r $f
    done
    resm=( "${res[@]/#/$temp/$f}" )
    resm=( "${resm[@]/%/.png}" )
    for filetype in ico; do
        convert "${resm[@]}" ${f%%.*}.$filetype
    done
done
rm -rf $temp
