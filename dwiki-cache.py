#!/usr/bin/python
#
# Force things into the DWiki BFC cache if they aren't already there.
#	dwiki-cat.py [-P port] CFG HOSTNAME URL [URL ....]
#
#
import sys
import urlparse

import dwconfig, derrors
import httpcore, htmlrends, context

def start_response(code, headers):
	pass

# We don't reuse the environment so we don't bother making a copy before
# we run the app.
def runwsgi(env, app):
	app(env, start_response)

def setup_env(url, host, options):
	_, _, path, query, _ = urlparse.urlsplit(url)
	if options.port == "443":
		options.scheme = "https"
	if options.scheme == "https" and options.port == "80":
		options.port = "443"
	env = {
		'SERVER_PROTOCOL': 'HTTP/1.0',
		'SERVER_NAME': host,
		'SERVER_PORT': options.port,
		'REMOTE_ADDR': '127.0.0.1',
		'REMOTE_PORT': '9999',
		'REQUEST_METHOD': 'GET',
		'REQUEST_URI': url,
		'SCRIPT_NAME': '',
		'PATH_INFO': path,
		'QUERY_STRING': query,
		
		'wsgi.version': (1, 0),
		'wsgi.url_scheme': options.scheme,
		'wsgi.multithread': 0,
		'wsgi.multiprocess': 0,
		'wsgi.run_once': 1,
		'wsgi.input': None,
		'wsgi.errors': sys.stderr,

		'HTTP_HOST': host,
		'HTTP_USER_AGENT': 'DWiki Testbench.py',
		}
	if options.scheme == "https":
		env["HTTPS"] = "on"
	return env

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)
def usage():
	sys.stderr.write("usage: %s [options|--help] CONFIG HOSTNAME URL [URL ...]\n" % sys.argv[0])
	sys.exit(1)

def setup_options():
	parser = dwconfig.setup_options("%prog [--help] [options] CONFIG HOSTNAME URL [URL ...]",
					"dwiki-cache 0.1")
	parser.add_option('-P', '--port', dest='port', action="store",
			  type="string",
			  help="set the server port for the requests, default %default.")
	parser.add_option("-S", "--https", dest="scheme", action="store_const",
			  const="https",
			  help="pretend the request used HTTPS instead of HTTP.")
	parser.set_defaults(port="80", scheme="http")

	return parser

def main(args):
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) < 3:
		usage()

	options.extraconfig.append("internal_bfc-force-caching")
	try:
		app, r = dwconfig.materialize(args[0], options)
	except derrors.WikiErr, e:
		die("dwiki error: "+str(e))

	for url in args[2:]:
		env = setup_env(url, args[1], options)
		runwsgi(env, app)

if __name__ == "__main__":
	main(sys.argv[1:])
