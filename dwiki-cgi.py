#!/usr/bin/python
# This shoves off as fast as possible to the real file, because Python
# doesn't load .pyc / .pyo files for something it's running directly.
import sys
import dwikicgi

if __name__ == "__main__":
	# uncomment to log processing time to stderr
	#import time
	#startTime = time.time()
	dwikicgi.main(sys.argv[1:])
	#endTime = time.time()
	#sys.stderr.write("%s: processing took %.03f seconds\n" % (sys.argv[0], endTime-startTime))
