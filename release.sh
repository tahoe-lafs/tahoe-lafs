#!/bin/bash

function main() {
  parse_args $@
  
  if [ $h ]; then 
      help; exit 0
  fi
  if ! [ $d ]; then 
      check_dependencies
  fi
  if [ $t ]; then
      if [ "$RELEASE_TAG" = "" ]; then
          echo "Invalid release tag"; exit 1
      fi
  else 
      echo "Release tag must be passed via the -t flag"
  fi     
  if [ $i ]; then
      if ! [[ "$TICKET_NUMBER" =~ ^[0-9]+$ ]]; then
          echo "Invalid ticket number"; exit 1
      fi
  else 
      echo "Issue/ticker number must be passed via the -i flag"
  fi 
  # create release clone, and start release tasks    
  git clone . "../tahoe-release-$RELEASE_TAG"
  cd "../tahoe-release-$RELEASE_TAG"
  python -m venv venv
  ./venv/bin/pip install --editable .[test]
  git branch "$TICKET_NUMBER.release-$RELEASE_TAG" # looks like XXXX.release-1.16.0
  tox -e news
  touch "newsfragments/$TICKET_NUMBER.minor"
  git add . && git commit -s -m "tahoe-lafs-$RELEASE_TAG news"
}

function parse_args() {
    local param
    while getopts ":dhi:t:" param; do
        case $param in
            d) d=true;;
            h) h=true;;
            i) i=true; TICKET_NUMBER="$OPTARG";;
            t) t=true; RELEASE_TAG="$OPTARG";;
            \?) help; exit 1;;
        esac
    done
    shift $((OPTIND-1)) #clear argument list
}

function check_dependencies() {
    [[ -z $(git status -s) ]] || { echo >&2 "repo is not clean, commit everything first!"; exit 1; }
    python -c "import wheel" || { echo >&2 "wheel is not installed. Install via pip!"; exit 1; }
}

function help() {
    echo "Usage : ./releash.sh [-d][-t <string>]"
    echo "ARGURMENTS"
    echo "-d, Ignore dependency checks"
    echo "-h, Show help menu"
    echo "-i, Ticket number."
    echo "-t, Release tag name"
}

main $@