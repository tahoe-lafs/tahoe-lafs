#!/usr/bin/env bash

set -euo pipefail

# Get your API token here:
# https://app.circleci.com/settings/user/tokens
API_TOKEN=$1
shift

# Name the branch you want to trigger the build for
BRANCH=$1
shift

curl \
    --verbose \
    --request POST \
    --url https://circleci.com/api/v2/project/gh/tahoe-lafs/tahoe-lafs/pipeline \
    --header "Circle-Token: $API_TOKEN" \
    --header "content-type: application/json" \
    --data '{"branch":"'"$BRANCH"'","parameters":{"push-images":true,"run-tests":false}}'
