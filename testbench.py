#!/usr/bin/python
#
# A DWiki test bench. Usage:
#	testbench.py [-P] CFG COUNT URL [RENDERER]
#
import sys, time
import profile, pstats
import urlparse
import itertools

import dwconfig, derrors
import httpcore, htmlrends, context

def start_response(code, headers):
	pass

# Applications can mutate the environment, so we always make a copy
# of it before running the app.
def runwsgi(env, app):
	e = dict(env.items())
	app(e, start_response)

# We take a context and clone it so that we have as little overhead
# in the core loop as possible.
def runrend(rend, ctx):
	nc = ctx.clone()
	rend(nc)

# The itertools.repeat(None, cnt) technique is used by timeit.py,
# so I assume it is the fastest and least overhead. (The overhead
# of pushing the it object creation out of runLimited is miniscule
# on the scale I am operating.)
def runLimited(cnt, what, *args):
	it = itertools.repeat(None, cnt)
	for _ in it:
		what(*args)

def doProfile(cnt, what, *args):
	#h = hotshot.Profile("dwikin.prof")
	#h.runcall(runLimited, (httpd, howmany))
	#h.close()
	#stats = hotshot.stats.load("dwikin.prof")
	p = profile.Profile()
	alst = (runLimited, cnt, what,) + args
	p.runcall(*alst)
	stats = pstats.Stats(p)

	stats.strip_dirs()
	stats.sort_stats("time", "calls")
	stats.print_stats(30)

# This requires the itimer statprof based stuff from
# http://vallista.idyll.org/wiki/StatProf
def doStatProf(*args):
	import bench.statprof
	sys.setcheckinterval(0)
	bench.statprof.start()
	runLimited(*args)
	bench.statprof.stop()
	bench.statprof.display(percent_threshold=1.0)

def doTime(cnt, *args):
	args = (cnt,) + args
	t0 = time.time()
	runLimited(*args)
	t1 = time.time()
	td = (t1-t0)/cnt
	print "testbench.py: %.4g msec per operation" % (td*1000.0)

def setup_env(url):
	_, _, path, query, _ = urlparse.urlsplit(url)
	env = {
		'SERVER_PROTOCOL': 'HTTP/1.0',
		'SERVER_NAME': 'localhost',
		'SERVER_PORT': '80',
		'REMOTE_ADDR': 'localhost',
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

# No -P, no renderer right now.
def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)
def usage():
	sys.stderr.write("usage: %s [options|--help] CONFIG URL COUNT [RENDERER]\n" % sys.argv[0])
	sys.exit(1)

def setup_options():
	parser = dwconfig.setup_options("%prog [--help] [options] CONFIG URL COUNT [RENDERER]",
					"testbench 0.1")
	parser.add_option('-P', '--profile', dest='operation',
			  action="store_const", const=doProfile,
			  help="profile, using the Python profiler")
	parser.add_option('-S', '--statprof', dest="operation",
			  action="store_const", const=doStatProf,
			  help="profile, using the statistical profiler")
	parser.set_defaults(operation = doTime)
	return parser

def main(args):
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	op = options.operation
	if len(args) not in (3, 4):
		usage()

	cnt = int(args[2])
	url = args[1]
	rend = None
	if len(args) == 4:
		try:
			rend = htmlrends.get_renderer(args[3])
		except derrors.RendErr, e:
			die(str(e))

	try:
		app, r = dwconfig.materialize(args[0], options)
		# We're one of the people who actually needs this.
		cfg, ms, webserv, staticstore, cachestore = r

	except derrors.WikiErr, e:
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
		op(cnt, runrend, rend, ctx)
	else:
		op(cnt, runwsgi, env, app)

if __name__ == "__main__":
	main(sys.argv[1:])
