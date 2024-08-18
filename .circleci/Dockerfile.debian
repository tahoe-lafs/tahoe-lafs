ARG TAG
FROM debian:${TAG}
ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND noninteractive
ENV WHEELHOUSE_PATH /tmp/wheelhouse
ENV VIRTUALENV_PATH /tmp/venv
# This will get updated by the CircleCI checkout step.
ENV BUILD_SRC_ROOT /tmp/project

RUN apt-get --quiet update && \
    apt-get --quiet --yes install \
        git \
        lsb-release \
        sudo \
        build-essential \
        python${PYTHON_VERSION} \
        python${PYTHON_VERSION}-dev \
        libffi-dev \
        libssl-dev \
        libyaml-dev \
        virtualenv \
        tor

# Get the project source.  This is better than it seems.  CircleCI will
# *update* this checkout on each job run, saving us more time per-job.
COPY . ${BUILD_SRC_ROOT}

RUN "${BUILD_SRC_ROOT}"/.circleci/prepare-image.sh "${WHEELHOUSE_PATH}" "${VIRTUALENV_PATH}" "${BUILD_SRC_ROOT}" "python${PYTHON_VERSION}"
