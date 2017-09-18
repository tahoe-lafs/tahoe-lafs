FROM debian:9
LABEL maintainer "gordon@leastauthority.com"
RUN apt-get update
RUN DEBIAN_FRONTEND=noninteractive apt-get -yq upgrade
RUN DEBIAN_FRONTEND=noninteractive apt-get -yq install build-essential python-dev libffi-dev libssl-dev python-virtualenv git
RUN \
  git clone https://github.com/tahoe-lafs/tahoe-lafs.git /root/tahoe-lafs; \
  cd /root/tahoe-lafs; \
  virtualenv --python=python2.7 venv; \
  ./venv/bin/pip install --upgrade setuptools; \
  ./venv/bin/pip install --editable .; \
  ./venv/bin/tahoe --version;
RUN \
  cd /root; \
  mkdir /root/.tahoe-client; \
  mkdir /root/.tahoe-introducer; \
  mkdir /root/.tahoe-server;
RUN /root/tahoe-lafs/venv/bin/tahoe create-introducer --location=tcp:introducer:3458 --port=tcp:3458 /root/.tahoe-introducer
RUN /root/tahoe-lafs/venv/bin/tahoe start /root/.tahoe-introducer
RUN /root/tahoe-lafs/venv/bin/tahoe create-node --location=tcp:server:3457 --port=tcp:3457 --introducer=$(cat /root/.tahoe-introducer/private/introducer.furl) /root/.tahoe-server
RUN /root/tahoe-lafs/venv/bin/tahoe create-client --webport=3456 --introducer=$(cat /root/.tahoe-introducer/private/introducer.furl) --basedir=/root/.tahoe-client --shares-needed=1 --shares-happy=1 --shares-total=1
VOLUME ["/root/.tahoe-client", "/root/.tahoe-server", "/root/.tahoe-introducer"]
EXPOSE 3456 3457 3458
ENTRYPOINT ["/root/tahoe-lafs/venv/bin/tahoe"]
CMD []
