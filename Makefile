# Timestamps are the common case so now we support them in the Makefile.
# The default is to restore timestamps from the timestamp file.
postget:
	./dostamps set test test.timestamps

precommit:
	./dostamps get test test.timestamps

# This way of running pychecker works better, although it can produce more
# explosions
pychecker:
	for i in *.py wsgi/[a-z]*.py; do pychecker $$i; done; true

clean:
	rm -f *~ *.pyc
