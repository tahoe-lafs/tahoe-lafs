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


  if ! [ $f ]; then
      start_release
  else
      if ! [ $k ]; then
          echo "Signing key not provided"; exit 1
      fi
      if ! [[ "$SIGNING_KEY" =~ ^[a-zA-Z0-9]+$ ]]; then
          echo "Invalid format for signing key"; exit 1
      fi
      complete_release
  fi

}

function parse_args() {
    local param
    while getopts ":cdhi:t:fk:" param; do
        case $param in
            c) c=true;;
            d) d=true;;
            f) f=true;;
            h) h=true;;
            i) i=true; TICKET_NUMBER="$OPTARG";;
            k) k=true; SIGNING_KEY="$OPTARG";;
            t) t=true; RELEASE_TAG="$OPTARG";;
            \?) help; exit 1;;
        esac
    done
    shift $((OPTIND-1)) #clear argument list
}


function clean() {
  rm -rf tahoe-release-*
}

function check_dependencies() {
    [[ -z $(git status -s) ]] || { echo >&2 "repo is not clean, commit everything first!"; exit 1; }
    python -c "import wheel" || { echo >&2 "wheel is not installed. Install via pip!"; exit 1; }
}

function start_release() {
  # create release clone, and start release tasks    
  git clone https://github.com/tahoe-lafs/tahoe-lafs.git "../tahoe-release-$RELEASE_TAG"
  cd "../tahoe-release-$RELEASE_TAG"
  python -m venv venv
  ./venv/bin/pip install --editable .[test]
  git branch "$TICKET_NUMBER.release-$RELEASE_TAG" # looks like XXXX.release-1.16.0
  git checkout "$TICKET_NUMBER.release-$RELEASE_TAG"
  ./venv/bin/tox -e news
  touch "newsfragments/$TICKET_NUMBER.minor"
  RELEASE_TITLE="Release $RELEASE_TAG $(date +('%Y-%m-%d'))"
  git add . && git commit -s -m "tahoe-lafs-$RELEASE_TAG news"
  sed -i -r 's/(\.){2}[[:space:]]towncrier start line//g' NEWS.rst 
  sed -i -r "s/(Release\s([[:digit:]]+\.[[:digit:]]+\.[[:digit:]])(\.)post[[:digit:]]+[[:space:]]\([[:digit:]]{4}-[[:digit:]]{02}-[[:digit:]]{02}\))+/$RELEASE_TITLE/g" NEWS.rst
  echo "First release step complete."
  echo "Please review News.rst"
  echo 'Update "docs/known_issues.rst" (if neccesary)'
  echo "Run : ./release.sh -d -t $RELEASE_TAG -i $TICKET_NUMBER -k {YOUR SIGNING KEY} -f"
}


function complete_release() {
  git push origin "$TICKET_NUMBER.release-$RELEASE_TAG"
  RELEASE_TITLE="Release $RELEASE_TAG $(date +('%Y-%m-%d'))"
  git tag -s -u $SIGNING_KEY -m "${RELEASE_TITLE,,}" $RELEASE_TAG
  ./venv/bin/tox -e py37,codechecks,docs,integration
  ./venv/bin/tox -e deprecations,upcoming-deprecations
}

function help() {
    echo "Usage : ./releash.sh [-d][-t <string>]"
    echo "ARGURMENTS"
    echo "-c, Clean"
    echo "-d, Ignore dependency checks"
    echo "-f, Finish release process"
    echo "-h, Show help menu"
    echo "-i, Ticket number."
    echo "-t, Release tag name"
}

main $@