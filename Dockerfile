FROM python:2.7

ADD . /tahoe-lafs
RUN \
  cd /tahoe-lafs && \
  git pull --depth=100 && \
  pip install . && \
  rm -rf ~/.cache/

WORKDIR /root
