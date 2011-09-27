# This way of running pychecker works better, although it can produce more
# explosions
pychecker:
	for i in *.py wsgi/[a-z]*.py; do pychecker $$i; done; true

clean:
	rm -f *~ *.pyc
