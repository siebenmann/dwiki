#!/usr/bin/python
#
# A DWiki version of cat, sort of. Usage:
#	dwiki-cat.py CFG URL [RENDERER]
#
import sys
import urlparse

import dwconfig, derrors
import httpcore, htmlrends, context

def start_response(code, headers):
	print "Status: %s" % code
	for header in headers:
		print "%s: %s" % header
	print

# Applications can mutate the environment, so we always make a copy
# of it before running the app.
def runwsgi(env, app):
	e = dict(env.items())
	for e in app(e, start_response):
		print e

# We take a context and clone it so that we have as little overhead
# in the core loop as possible.
def runrend(rend, ctx):
	nc = ctx.clone()
	print rend(nc)

def setup_env(url):
	_, _, path, query, _ = urlparse.urlsplit(url)
	env = {
		'SERVER_PROTOCOL': 'HTTP/1.0',
		'SERVER_NAME': 'localhost',
		'SERVER_PORT': '80',
		'REMOTE_ADDR': '127.0.0.1',
		'REMOTE_PORT': '9999',
		'REQUEST_METHOD': 'GET',
		'REQUEST_URI': url,
		'SCRIPT_NAME': '',
		'PATH_INFO': path,
		'QUERY_STRING': query,
		
		'wsgi.version': (1, 0),
		'wsgi.url_scheme': 'http',
		'wsgi.multithread': 0,
		'wsgi.multiprocess': 0,
		'wsgi.run_once': 1,
		'wsgi.input': None,
		'wsgi.errors': sys.stderr,

		'HTTP_HOST': 'localhost',
		'HTTP_USER_AGENT': 'DWiki Testbench.py',
		}
	return env

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)
def usage():
	sys.stderr.write("usage: %s [options|--help] CONFIG URL [RENDERER]\n" % sys.argv[0])
	sys.exit(1)

def setup_options():
	parser = dwconfig.setup_options("%prog [--help] [options] CONFIG URL [RENDERER]",
					"dwiki-cat 0.1")
	return parser

def main(args):
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) not in (2, 3):
		usage()

	url = args[1]
	rend = None
	if len(args) == 3:
		try:
			rend = htmlrends.get_renderer(args[2])
		except derrors.RendErr as e:
			die(str(e))

	try:
		app, r = dwconfig.materialize(args[0], options)
		# We're one of the people who actually needs this.
		cfg, ms, webserv, staticstore, cachestore = r
	except derrors.WikiErr as e:
		die("dwiki error: "+str(e))

	env = setup_env(url)
	if rend:
		httpcore.environSetup(env, cfg, ms, webserv, staticstore,
				      cachestore)
		env['dwiki.logger'] = None
		rdata = httpcore.gather_reqdata(env)
		if 'query' not in rdata:
			die("URL %s is not accepted by the core" % url)
		ctx = context.HTMLContext(cfg, ms, webserv, rdata)
		if cachestore:
			ctx.setvar(':_cachestore', cachestore)
		runrend(rend, ctx)
	else:
		runwsgi(env, app)

if __name__ == "__main__":
	main(sys.argv[1:])
