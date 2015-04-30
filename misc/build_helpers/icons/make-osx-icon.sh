#! /bin/bash
# Based on <http://stackoverflow.com/a/23688878/393146> and
# <http://stackoverflow.com/a/11788723/393146>.
# converts the passed-in svgs to icns format

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 svg1 [svg2 [...]]"
    exit 0
fi

temp="$(mktemp -d)"
declare -a res=(16 32 64 128 256 512 1024)
for f in "$*"; do
    name="`basename -s .svg "$f"`"
    iconset="$temp/${name}.iconset"
    mkdir -p "$iconset"
    for r in "${res[@]}"; do
        inkscape -z -e "$iconset/${name}${r}x${r}.png" -w "$r" -h "$r" "$f"
    done
    ln "$iconset/${name}32x32.png" "$iconset/${name}16x16@2x.png"
    mv "$iconset/${name}64x64.png" "$iconset/${name}32x32@2x.png"
    ln "$iconset/${name}256x256.png" "$iconset/${name}128x128@2x.png"
    ln "$iconset/${name}512x512.png" "$iconset/${name}256x256@2x.png"
    mv "$iconset/${name}1024x1024.png" "$iconset/${name}512x512@2x.png"
    iconutil -c icns -o "${name}.icns" "$iconset"
done
rm -rf "$temp"
