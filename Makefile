
.PHONY: run-queen run-client test

run-queen:
	cd queen-basedir && PYTHONPATH=.. twistd -noy ../queen.tac

run-client:
	cd client-basedir && PYTHONPATH=.. twistd -noy ../client.tac

run-client2:
	cd client-basedir2 && PYTHONPATH=.. twistd -noy ../client.tac
run-client3:
	cd client-basedir3 && PYTHONPATH=.. twistd -noy ../client.tac

test:
	trial allmydata

test-figleaf:
	trial --reporter=bwverbose-figleaf allmydata
	figleaf2html -d coverage-html -x allmydata/test/figleaf.excludes
# after doing test-figleaf, point your browser at coverage-html/index.html

create_dirs:
	mkdir -p queen-basedir
	mkdir -p client-basedir
	mkdir -p client-basedir2
	mkdir -p client-basedir/storage
	mkdir -p client-basedir2/storage

deb-dapper:
	rm -f debian
	ln -s dapper/debian debian
	chmod a+x debian/rules
	fakeroot debian/rules binary

deb-sid:
	rm -f debian
	ln -s sid/debian debian
	chmod a+x debian/rules
	fakeroot debian/rules binary
