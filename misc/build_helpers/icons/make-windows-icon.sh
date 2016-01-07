#! /bin/bash
# Based on <http://stackoverflow.com/a/23688878/393146>
# converts the passed-in svgs to ico format

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 svg1 [svg2 [...]]"
    exit 0
fi

temp="$(mktemp -d)"
declare -a res=(16 24 32 48 64 256)
for f in "$*"; do
    name="`basename -s .svg "$f"`"
    iconset="$temp/${name}.iconset"
    mkdir -p "$iconset"
    for r in "${res[@]}"; do
        inkscape -z -e "$iconset/${name}${r}.png" -w "$r" -h "$r" "$f"
    done
    resm=( "${res[@]/#/$iconset/${name}}" )
    resm=( "${resm[@]/%/.png}" )
    convert "${resm[@]}" "${f%%.*}.ico"
done
rm -rf "$temp"
