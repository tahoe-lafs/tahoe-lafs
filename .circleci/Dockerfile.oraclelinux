ARG TAG
FROM oraclelinux:${TAG}
ARG PYTHON_VERSION

ENV WHEELHOUSE_PATH /tmp/wheelhouse
ENV VIRTUALENV_PATH /tmp/venv
# This will get updated by the CircleCI checkout step.
ENV BUILD_SRC_ROOT /tmp/project

# XXX net-tools is actually a Tahoe-LAFS runtime dependency!
RUN yum install --assumeyes \
    git \
    sudo \
    make automake gcc gcc-c++ \
    python${PYTHON_VERSION} \
    libffi-devel \
    openssl-devel \
    libyaml \
    /usr/bin/virtualenv \
    net-tools

# Get the project source.  This is better than it seems.  CircleCI will
# *update* this checkout on each job run, saving us more time per-job.
COPY . ${BUILD_SRC_ROOT}

RUN "${BUILD_SRC_ROOT}"/.circleci/prepare-image.sh "${WHEELHOUSE_PATH}" "${VIRTUALENV_PATH}" "${BUILD_SRC_ROOT}" "python${PYTHON_VERSION}"
