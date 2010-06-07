
.PHONY: all dist build diskimage upload cleanup clean

all: build

dist: build diskimage upload cleanup

build:
	rsync -av ` sw_vers -productVersion | sed -e 's/^\(10\.[0-9]*\)\..*$$/\1/' `/ ./
	ln -sf ../contrib/fuse/impl_c/blackmatch.py tahoefuse.py
	python setup.py py2app --no-chdir

diskimage:
	mv dist Allmydata-$(VERSION)
	hdiutil create -ov -srcfolder Allmydata-$(VERSION) allmydata-rw.dmg
	hdiutil convert -ov allmydata-rw.dmg -format UDRO -o allmydata-ro.dmg
	hdiutil convert -ov allmydata-ro.dmg -format UDZO -o Allmydata-$(VERSION).dmg
	hdiutil internet-enable -yes Allmydata-$(VERSION).dmg
	rm -r Allmydata-$(VERSION) allmydata-rw.dmg allmydata-ro.dmg

ifdef UPLOAD_DEST_FURLFILE
# N.B.: xfer-client.py requires foolscap, so we hack up PYTHONPATH to improve
# the chances of finding it (using the one that we just built)
# broken. To fix this just use flappclient. --Zooko 2010-06-06
upload:
	chmod a+r Allmydata-$(VERSION).dmg
	PYTHONPATH=$(PYTHONPATH):../support/lib/python2.5/site-packages \
	 python ../misc/xfer-client.py --furlfile $(UPLOAD_DEST_FURLFILE) \
	 Allmydata-$(VERSION).dmg
else
upload:
	$(error UPLOAD_DEST_FURLFILE must be set when using upload target. e.g. make upload UPLOAD_DEST_FURLFILE=~/.upload.furl)
endif

cleanup:
	rm Allmydata-$(VERSION).dmg

clean:
	rm -rf build dist
