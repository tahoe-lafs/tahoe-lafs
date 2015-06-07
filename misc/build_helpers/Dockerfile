FROM python:2.7

ADD ../.. /tahoe-lafs
RUN \
  cd /tahoe-lafs && \
  make && \
  ln -vs /tahoe-lafs/bin/tahoe /usr/local/bin/tahoe

WORKDIR /root
