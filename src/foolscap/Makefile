
.PHONY: build test debian-sid debian-dapper debian-feisty debian-sarge
.PHONY: debian-edgy

build:
	python setup.py build

TEST=foolscap
test:
	trial $(TEST)

test-figleaf:
	rm -f .figleaf
	PYTHONPATH=misc/testutils trial --reporter=bwverbose-figleaf $(TEST)

figleaf-output:
	rm -rf coverage-html
	PYTHONPATH=misc/testutils python misc/testutils/figleaf2html -d coverage-html -r .
	@echo "now point your browser at coverage-html/index.html"

debian-sid:
	rm -f debian
	ln -s misc/sid/debian debian
	chmod a+x debian/rules
	debuild -uc -us

debian-dapper:
	rm -f debian
	ln -s misc/dapper/debian debian
	chmod a+x debian/rules
	debuild -uc -us

debian-edgy:
	rm -f debian
	ln -s misc/edgy/debian debian
	chmod a+x debian/rules
	debuild -uc -us

debian-feisty:
	rm -f debian
	ln -s misc/feisty/debian debian
	chmod a+x debian/rules
	debuild -uc -us

debian-sarge:
	rm -f debian
	ln -s misc/sarge/debian debian
	chmod a+x debian/rules
	debuild -uc -us

DOC_TEMPLATE=doc/template.tpl
docs:
	lore -p --config template=$(DOC_TEMPLATE) --config ext=.html \
	`find doc -name '*.xhtml'`

