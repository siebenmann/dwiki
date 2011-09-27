#
# Handle exceptions by printing a traceback to standard error.
#
# We deliberately don't try to do anything else, such as dump
# HTML to standard error. (At least right now.)
# This keeps the number of moving parts in a damaged system
# to a minimum.

import sys, traceback

def stderr(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))

def print_except(ty, inf, trc):
	stderr("Python internal error:")
	for e in traceback.format_exception(ty, inf, trc):
		lines = e.split("\n")
		for l in lines:
			if not l:
				continue
			stderr("T: %s" % l)
	stderr("-- traceback finished")

def enable():
	sys.excepthook = print_except

