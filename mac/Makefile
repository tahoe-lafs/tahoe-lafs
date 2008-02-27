
.PHONY: all dist build diskimage upload cleanup clean

all: build

dist: build diskimage upload cleanup

build:
	python setup.py py2app --no-chdir

diskimage:
	mv dist Allmydata-$(VERSION)
	hdiutil create -ov -srcfolder Allmydata-$(VERSION) allmydata-rw.dmg
	hdiutil convert -ov allmydata-rw.dmg -format UDRO -o allmydata-ro.dmg
	hdiutil convert -ov allmydata-ro.dmg -format UDZO -o Allmydata-$(VERSION).dmg
	hdiutil internet-enable -yes Allmydata-$(VERSION).dmg
	rm -r Allmydata-$(VERSION) allmydata-rw.dmg allmydata-ro.dmg

ifdef UPLOAD_DEST
upload:
	mkdir $(VERSION)
	cp Allmydata-$(VERSION).dmg $(VERSION)/
	chmod -R go+rx $(VERSION)/
	rsync -av $(VERSION) $(UPLOAD_DEST)
	rm -r $(VERSION)
else
upload:
	$(error UPLOAD_DEST must be set when using upload target. e.g. make upload UPLOAD_DEST=amduser@svn.allmydata.com:/home/amduser/public_html/dist/tahoe/mac-blah/)
endif

cleanup:
	rm Allmydata-$(VERSION).dmg

clean:
	rm -rf build dist
