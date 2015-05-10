FROM python:2.7

ADD . /tahoe-lafs
RUN \
  sed -i "s/\# alias/alias/" /root/.bashrc && \
  cd /tahoe-lafs && \
  make && \
  ln -vs /tahoe-lafs/bin/tahoe /usr/local/bin/tahoe

WORKDIR /root
