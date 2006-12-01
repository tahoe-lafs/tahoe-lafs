
.PHONY: run-queen run-client test

run-queen:
	cd queen-basedir && PYTHONPATH=.. twistd -noy ../queen.tac

run-client:
	cd client-basedir && PYTHONPATH=.. twistd -noy ../client.tac

test:
	trial allmydata

create_dirs:
	mkdir queen-basedir
	mkdir client-basedir
	mkdir client-basedir/storage
