FROM python:2.7

ADD . /tahoe-lafs
RUN \
  cd /tahoe-lafs && \
  git pull --depth=100 && \
  make && \
  ln -vs /tahoe-lafs/bin/tahoe /usr/local/bin/tahoe

WORKDIR /root
