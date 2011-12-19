#!/usr/bin/python
#
# Serve dwiki up via a simple basic HTTP server.
# (We may complicate this later, but we'll see.)
# This is basically a test harness, as DWiki is intended to be run
# as a CGI-BIN in the end.
# However, it is an attempt to be at least a *convincing* demo.
#
# Considering that this supports ETag and Last-Modified headers,
# Cookies, POST as well as GET, serving static files out of a
# portion of the wikispace, etc ... well, this is getting pretty
# convincing. I suppose I'm sold. (It makes a good testbed.)
#
# Our default port is 8010.
#
import sys

# Most of the actual functionality lives in here.
import dwconfig

# We are actually a semi-thin shim over the BaseHTTP WSGI server.
import wsgi.server

import profile, pstats

def runLimited(httpd, howmany):
	for _ in xrange(0, howmany):
		httpd.handle_request()

def doProfile(httpd, howmany):
	#h = hotshot.Profile("dwikin.prof")
	#h.runcall(runLimited, (httpd, howmany))
	#h.close()
	#stats = hotshot.stats.load("dwikin.prof")
	p = profile.Profile()
	p.runcall(runLimited, httpd, howmany)
	stats = pstats.Stats(p)

	stats.strip_dirs()
	stats.sort_stats("time", "calls")
	stats.print_stats(40)

def usage():
	sys.stderr.write("usage: dwiki-serv.py [options] CONF-FILE\nUse -h for options help.\n")
	sys.exit(1)
def setup_options():
	parser = dwconfig.setup_options("%prog [--help] [options] CONF-FILE",
					"dwiki-serv 0.1")
	parser.add_option('-f', '--fork', dest="servtype",
			  action="store_const", const="fork",
			  help="server forks to handle each connection")
	parser.add_option('-t', '--thread', dest="servtype",
			  action="store_const", const="thread",
			  help="server spawns a thread to handle connections")
	parser.add_option('-s', '--serial', dest="servtype",
			  action="store_const", const="plain",
			  help="server handles connections serially")
	parser.add_option('-c', '--count', dest="howmany",
			  action="store", type="int", metavar="COUNT",
			  help="run the server for only COUNT requests")
	parser.add_option('-P', '--profile', dest="profile",
			  action="store_true", help="profile the execution.")
	parser.add_option('-p', '--port', type="int", metavar="PORT",
			  dest="port", help="listen on port PORT (default %default)")
	parser.add_option('-a', '--address', type="string", metavar="ADDR",
			  dest="addr", help="listen only at IP address ADDR")
	parser.set_defaults(servtype = "default", howmany= -1, profile=False,
			    port=8010, addr='')
	return parser

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)

def main(args):
	# Parse and validate command line options.
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) != 1:
		usage()
	if options.profile:
		if options.howmany <= 0:
			die("-P requires a -c value")
		if options.servtype not in ('default', 'plain'):
			die("-P conflicts with -f and -t")
		stype = "plain"
	else:
		stype = options.servtype
		if stype == "default":
			stype = "thread"

	# Load up the configuration from the single argument, and then
	# create the dependant services and stuff.
	procfunc, _ = dwconfig.materialize(args[0], options)

	# Generate an appropriate WSGI server.
	httpd = wsgi.server.gen_server((options.addr, options.port),
				       procfunc, stype)

	# Depending on the options, serve the server in various ways.
	if options.profile:
		doProfile(httpd, options.howmany)
	elif options.howmany > 0:
		runLimited(httpd, options.howmany)
	else:
		try:
			httpd.serve_forever()
		except KeyboardInterrupt:
			pass

if __name__ == "__main__":
	main(sys.argv[1:])
