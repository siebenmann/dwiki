#
# Serve dwiki up though a CGI-BIN.
# This doesn't run directly, because it's big enough that we want it in
# .pyc form, which doesn't happen if it's directly executed.
#
# Apache output puts the status code and indeed the entire first
# line in as the 'Status:' header.
#
import sys

# NOT APPROPRIATE FOR PRODUCTION
#import cgitb; cgitb.enable()
import stderrtb; stderrtb.enable()

import derrors

# Most of the actual functionality lives in here.
import dwconfig

# Our actual WSGI implementation. This one comes straight from
# PEP 333, apart from some additional error handling.
# What we actually do is configure our app stack and then throw
# off to it.
import wsgi.cgi

# This is not too useful unless used with some external timing agent,
# to see how long the whole thing took.
def NullApp(environ, start_response):
	__pychecker__ = "no-argsused"
	start_response("200 OK",
		       [("Content-Type", "text/plain; charset=UTF-8")])
	return ["This is a null return.\n"]

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)
def usage():
	sys.stderr.write("usage: dwiki-cgi.py [options] CONF-FILE\nUse -h for options help.\n")
	sys.exit(1)

def setup_options():
	parser = dwconfig.setup_options("%prog [--help] [options] CONF-FILE",
					"dwiki-cgi 0.1")
	parser.add_option('-N', '--null-app', dest='nullapp',
			  action="store_true", help="run a null application")
	parser.set_defaults(nullapp=False)
	return parser

#
# CGI main entry point.
# We capture (dwiki) errors in an attempt to do something sensible.
# Later we will capture all errors and puke a stack backtrace to
# standard error.
def main(args):
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) != 1:
		usage()
	try:
		app, _ = dwconfig.materialize(args[0], options)
		if options.nullapp:
			app = NullApp
		wsgi.cgi.run_with_cgi(app)
	except derrors.WikiErr, e:
		die("dwiki error: "+str(e))
