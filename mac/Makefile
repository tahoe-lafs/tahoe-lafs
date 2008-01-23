
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

upload:
	mkdir $(VERSION)
	mv Allmydata-$(VERSION).dmg $(VERSION)/
	chmod -R go+rx $(VERSION)/
	rsync -av $(VERSION) amduser@svn.allmydata.com:/home/amduser/public_html/dist/tahoe/mac/

cleanup:
	rm -r $(VERSION)

clean:
	rm -rf build dist
