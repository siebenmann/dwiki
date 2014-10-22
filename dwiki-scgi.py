#!/usr/bin/python
#
# Serve dwiki up via SCGI.
#
# This normally uses a relatively efficient preforking server implementation.
# The server listens on some socket for SCGI requests and handles them.
# The socket can be a Unix domain socket (ie a file), a TCP port, or
# just be inherited from a supervisor through the systemd protocol
# (see supervise-sock.py for one such supervisor).
#
# There is always one master process and then up to -m worker processes.
# Worker processes exit after processing --restart-after requests (and
# then may be restarted if they are needed due to --min-workers and/or
# --min-idle).
#
# The server normally runs forever unless you specify at least one of
# --idle-timeout, -l, or -S (and then touch the stopfile after the
# server has started). There is no automatic re-exec feature; you need
# to stop the server and then have whatever is supervising it restart
# it. Note that -S has a delayed effect; dwiki-scgi only notices the
# stop file the next time it wakes up, ie the next time it handles a
# request.
#
# --min-workers sets the minimum number of worker processes, whether
# idle or busy. --min-idle applies on top of however many busy workers
# you have. Setting a large --min-idle may make dwiki-scgi fork() a
# burst of new worker processes when traffic comes in.
#
# ----
# It is worth explaining the need for -L.
# The problem is that we can't tell the difference between a dead
# Unix domain socket and a live one; there is no 'this is already
# bound' error the way there is for IP.
# This means that if a lot of us start very close to each other, we
# can all wind up removing someone else's socket file, making our own,
# listening on it, having our socket file removed by the next person,
# etc etc.
#
# The only way out is to lock something, in this case the -L file,
# *immediately* when we start up. The choice of file to lock is
# arbitrary (and we could just as well use the config file).
# The file must already exist and be openable for read.
#

import sys, os, stat, time
import fcntl

# My hate is burning department of hate.
# FIXME: there should be a better way than this.
__pychecker__ = "no-shadowbuiltin no-local"
from optparse import OptionGroup
__pychecker__ = ""

# Most of the actual functionality lives in here.
import dwconfig

# We are actually a semi-thin shim over the BaseHTTP WSGI server.
import wsgi.scgiserv
import wsgi.scgipf

import sockact

# Our standard error had better be put somewhere useful.
import stderrtb; stderrtb.enable()

def usage():
	sys.stderr.write("usage: dwiki-scgi.py [options] CONF-FILE\nUse -h for options help.\n")
	sys.exit(1)
def setup_options():
	defMaxconn = 10
	defMinconn = 4
	defPerconn = 100
	defMinidle = 1
	parser = dwconfig.setup_options("%prog [--help] [options] CONF-FILE",
					"dwiki-scgi 0.1")

	parser.add_option('-v', '--verbose', dest="verbose",
			  action="store_true",
			  help="be more verbose")
	parser.add_option('-L', '--lockfile', type="string",
			  metavar="FILE", dest="lockfile",
			  help="lock this file (must exist) for mutual exclusion (primarily on startup).")

	group2 = OptionGroup(parser, "What to listen on",
			     "You must give one of -s, -p, or --systemd-socket to specify where to listen for SCGI requests.")
	parser.add_option_group(group2)
	group2.add_option('-s', '--socket-file', dest="sockfile", type='string',
			  metavar='SOCK',
			  help="use SOCK as the (Unix) socket path.")
	group2.add_option('-P', '--perms', dest='perms', type='string',
			  metavar="PERM",
			  help="with -s, set the socket permissions to this value (a number). Use a leading 0 to force octal, eg '0666'.")
	group2.add_option('', '--systemd-socket', dest="systemd",
			  action="store_true",
			  help="use the systemd socket activation protocol to get our socket.")
	group2.add_option('-p', '--port', type="int", metavar="PORT",
			  dest="port", help="listen on port PORT.")
	group2.add_option('-a', '--address', type="string", metavar="ADDR",
			  dest="addr", help="listen at IP address ADDR (the default is to listen on all).")

	group0 = OptionGroup(parser, "Options to control when we exit")
	parser.add_option_group(group0)
	group0.add_option('-S', '--stopfile', dest="stopfile", type="string",
			  metavar='FILE',
			  help="Use FILE as the stop file (no default). The server will exit after the next request if FILE exists and has a modification time that is after it started.")
	group0.add_option('', '--idle-timeout', dest="idletimeout",
			  type="float", metavar="MIN",
			  help="Server exits after MIN minutes (may be fractional) if there has been no activity. This may preempt --min-lifetime.")
	group0.add_option('-l', '--low-loadavg', dest="minloadavg",
			  type="float", metavar="LAVG",
			  help="Server dies if all three load average numbers are under LAVG.")
	group0.add_option('', '--min-lifetime', dest="minlifetime",
			  type="float", metavar="MIN",
			  help="Server runs for at least MIN minutes (may be fractional) regardless of the load average (no default).")

	group1 = OptionGroup(parser, "Load control options",
			     "Note that a number of these options do not do anything if you specify --fork.")
	parser.add_option_group(group1)
	group1.add_option('-m', '--maxconn', dest="maxconn", type="int",
			  metavar='NUM', action='store',
			  help=("limit us to NUM worker processes and thus NUM simultaneous connections (default %d)." % defMaxconn))
	group1.add_option('', '--drop-on-overload', dest='dropoverload',
			  action="store_true",
			  help="drop new requests when all worker processes are busy instead of waiting for the next one to become free. (Not recommended.)")
	group1.add_option('', '--min-workers', dest="minconn", type="int",
			  metavar="NUM", action='store',
			  help=("always have NUM worker processes (default %d)." % defMinconn))
	group1.add_option('', '--restart-after', dest='perconn', type='int',
			  metavar='NUM', action='store',
			  help=("restart each worker process after it handles NUM connections (default %d)." % defPerconn))
	group1.add_option('', '--min-idle', dest='minidle', type='int',
			  metavar='NUM', action="store",
			  help=("try to always have NUM idle worker processes (default %d)" % defMinidle))
	group1.add_option('', '--worker-timeout', dest='workertimeout',
			  type="float", metavar="MIN",
			  help="Spare worker processes exit after MIN minutes of inactivity (as usual, MIN may be fractional).")

	group3 = OptionGroup(parser, "Obscure options")
	parser.add_option_group(group3)
	group3.add_option('-N', '--null-app', dest='nullapp',
			  action="store_true", help="run a null application for timing purposes, instead of DWiki.")
	group3.add_option('', '--fork', dest="servtype",
			  action="store_const", const="fork",
			  help="server forks to handle each connection, instead of using a pool of preforked worker processes.")
	parser.set_defaults(maxconn = defMaxconn,
			    minconn = defMinconn, perconn = defPerconn,
			    minidle = defMinidle,
			    servtype="prefork",
			    dropoverload = False,
			    sockfile = None, port=None, addr='',
			    systemd = False,
			    stopfile = None, perms = None, minloadavg = None,
			    minlifetime = None,
			    idletimeout = None, workertimeout = None,
			    lockfile = None, verbose = False, nullapp = False)
	return parser

# This is used for timing the entire through-stack of SCGI et al.
def NullApp(environ, start_response):
	__pychecker__ = "no-argsused"
	start_response("200 OK",
		       [("Content-Type", "text/plain; charset=UTF-8")])
	return ["This is a null return.\n"]

def get_load():
	try:
		return os.getloadavg()
	except EnvironmentError:
		return None

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)

def timestamp():
	return time.strftime("%a %b %d %H:%M:%S %Y")

def main(args):
	# Parse and validate command line options.
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) != 1:
		usage()
	if options.lockfile:
		try:
			fd = os.open(options.lockfile, os.O_RDONLY)
		except EnvironmentError, e:
			die("Could not open lockfile: "+str(e))
		try:
			fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except EnvironmentError:
			# All failures are assumed to be 'cannot lock,
			# someone else is already there', and we die.
			sys.exit(0)
		# Note that is important that we never close 'fd';
		# locks die on close.

	# ... I wish there was a better way.
	x = [x for x in [options.sockfile, options.port, options.systemd] if x]
	if len(x) > 1:
		die("can only use one of -s, -p, or --systemd-socket")
	elif len(x) == 0:
		die("must supply one of -s, -p, or --systemd-socket")
	elif options.perms and not options.sockfile:
		die("-P requires -s")

	if options.systemd:
		lsocks = sockact.sd_listen_sockets()
		if not lsocks:
			die("--systemd-socket given but no socket(s) found")
		elif len(lsocks) > 1:
			die("sadly I cannot listen on multiple sockets yet, passed %d sockets" % len(lsocks))
		saddr = lsocks[0]
	elif options.sockfile:
		saddr = options.sockfile
		# Apparently required by Unix? Sigh.
		try:
			st = os.stat(options.sockfile)
			if stat.S_ISSOCK(st.st_mode):
				os.unlink(options.sockfile)
		except EnvironmentError:
			pass
	elif options.port:
		saddr = (options.addr, options.port)
	if options.perms:
		try:
			options.perms = int(options.perms, 0)
		except ValueError:
			die("cannot convert permissions '%s' to integer" % options.perms)

	if options.idletimeout:
		options.idletimeout *= 60
	if options.workertimeout:
		options.workertimeout *= 60

	# Load up the configuration from the single argument, and then
	# create the dependant services and stuff.
	procfunc, _ = dwconfig.materialize(args[0], options,
					   "scgi-%s" % options.servtype)

	# If we have been asked to do timings, overwrite the WSGI process
	# function with the null WSGI app.
	if options.nullapp:
		procfunc = NullApp

	# Create the function that will tell the server when it should
	# stop.
	s_time = time.time()
	def stopNow():
		try:
			if options.stopfile and \
			   s_time < os.path.getmtime(options.stopfile):
				return True
		except EnvironmentError:
			pass
		if options.minlifetime:
			r_time = time.time() - s_time
			if (options.minlifetime * 60) > r_time:
				return False
		if options.minloadavg:
			cla = get_load()
			if cla and options.minloadavg > max(cla):
				return True		
		return False
	# To make our shutdown more orderly, remove the socket file
	# when we are shutting down so that new connections can't be
	# made.
	def stopNow2():
		r = stopNow()
		if r and options.sockfile:
			try:
				os.unlink(options.sockfile)
			except EnvironmentError:
				pass
		return r

	sfunc = None
	if options.stopfile or options.minloadavg:
		sfunc = stopNow2

	# Generate the SCGI server, and then serve it out.
	if options.servtype == "prefork":
		builder = wsgi.scgipf.gen_server
	else:
		builder = wsgi.scgiserv.gen_server
	scgi = builder(saddr, procfunc, sfunc, options)

	try:
		if options.verbose:
			sys.stderr.write("[%s] [note] dwiki-scgi starting\n" %
					 timestamp())
		scgi.serve_forever()
	except KeyboardInterrupt:
		pass
	if options.verbose:
		sys.stderr.write("[%s] [note] dwiki-scgi stopping\n" %
				 timestamp())

if __name__ == "__main__":
	main(sys.argv[1:])
