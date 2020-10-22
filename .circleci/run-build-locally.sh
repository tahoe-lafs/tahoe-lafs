#!/usr/bin/env bash

CIRCLE_TOKEN=efb53124be82dd4b3153bc0e3f60de71da629d59

curl --user ${CIRCLE_TOKEN}: \
    --request POST \
    --form revision=$(git rev-parse HEAD) \
    --form config=@config.yml \
    --form notify=false \
    https://circleci.com/api/v1.1/project/github/exarkun/tahoe-lafs/tree/2929.circleci
