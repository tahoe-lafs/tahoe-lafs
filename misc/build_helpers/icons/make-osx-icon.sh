#! /bin/bash
# Based on <http://stackoverflow.com/a/23688878/393146> and
# <http://stackoverflow.com/a/11788723/393146>.
# converts the passed-in svgs to icns format

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 svg1 [svg2 [...]]"
    exit 0
fi

temp="$(pwd)/temp"
declare -a res=(16 32 64 128 256 512 1024)
for f in "$*"; do
    name="`basename -s .svg "$f"`"
    iconset="$temp/${name}.iconset"
    mkdir -p "$iconset"
    for r in "${res[@]}"; do
        inkscape -z -e "$iconset/icon_${r}x${r}.png" -w "$r" -h "$r" "$f"
    done
    ln "$iconset/icon_32x32.png" "$iconset/icon_16x16@2x.png"
    mv "$iconset/icon_64x64.png" "$iconset/icon_32x32@2x.png"
    ln "$iconset/icon_256x256.png" "$iconset/icon_128x128@2x.png"
    ln "$iconset/icon_512x512.png" "$iconset/icon_256x256@2x.png"
    mv "$iconset/icon_1024x1024.png" "$iconset/icon_512x512@2x.png"
    iconutil -c icns -o "${name}.icns" "$iconset"
done
rm -rf "$temp"
