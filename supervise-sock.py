#!/usr/bin/python
#
# Supervise a daemon that supports systemd socket activation.
#
# In socket supervision we (the supervisor process) own the actual
# socket that the daemon will use. We create it, wait for the first
# connection request to arrive, start the actual daemon and pass the
# socket to it, wait for it to exit, and then return to waiting for a
# new connection request. We pass the socket to the actual daemon using
# systemd's protocol.
# All of our socket-related options (-s/-P, -p/-a, and --systemd-socket)
# control what socket *we* open, not how we pass the socket to the actual
# daemon.
#
# After the daemon exits, we may wait at least a little bit of time
# before starting a new one. The default is not to wait at all if the
# daemon exited successfully (if there is a new connection we'll
# immediately restart a new instance) but to wait a bit if the daemon
# exited with a failure. The goal of waiting on failure is to avoid a
# flood of unsuccessful daemon restarts. See -R and -E.
# There is no other restart throttling; if the daemon keeps failing
# we'll keep restarting it every -R seconds for, well, ever.
#
# -v currently only causes us to print warnings if the actual daemon
# exits with a failure status. You probably want to use this.
#
# Redirecting or logging the output of this and the daemon we are
# supervising is up to you. You may want to make the command we run
# a script that sets up the enviroment for your real daemon. If you
# do, make sure that you 'exec' the real daemon; the systemd socket
# activation protocol normal has a PID safeguard (so that if the daemon
# is not the right PID, ie the direct child PID, it will ignore things).
#
# A typical usage would be:
#	supervise-sock.py -s /some/sockname -v -- dwiki-scgi.py --systemd-sock wiki.conf
#
# The details of the systemd socket activation protocol are covered in
# http://0pointer.de/public/systemd-man/sd_listen_fds.html
# (in the 'Notes' section).

import sys, os, stat, select, time

# My hate is burning department of hate.
# FIXME: there should be a better way than this.
__pychecker__ = "no-shadowbuiltin no-local"
from optparse import OptionParser
__pychecker__ = ""

import wsgi.gensock
import sockact

# Our standard error had better be put somewhere useful.
import stderrtb; stderrtb.enable()

def usage():
	sys.stderr.write("usage: supervise-sock.py [options] CMD [ARG ...]\nUse -h for options help.\n")
	sys.exit(1)
def setup_options():
	parser = OptionParser(usage="%prog [--help] [options] CMD [ARG ...]",
			      version="supervise-sock 0.1")
	parser.add_option('-R', '--restart-delay', dest="rdelay", type="float",
			  metavar="DELAY",
			  help="wait DELAY seconds (may be fractional) after the CMD exits successfully before potentially restarting. Default %default.")
	parser.add_option('-E', '--error-delay', dest='edelay', type="float",
			  metavar="DELAY",
			  help="wait DELAY seconds (may be fractional) after CMD exits badly before potentially restarting it. Default %default.")
	parser.add_option('', '--start-now', dest='startimmed',
			  action="store_true",
			  help="Run the daemon immediately on startup without waiting for the first socket connection (we will continue to wait on the socket for subsequent startups).")
	parser.add_option('', '--immediate-restart', dest='restartimmed',
			  action="store_true",
			  help="Immediately restart the daemon after it exits without waiting for a new socket connection.")
	parser.add_option('-s', '--socket', dest="sockfile", type='string',
			  metavar='SOCK',
			  help="use SOCK as the (Unix) socket path.")
	parser.add_option('-P', '--perms', dest='perms', type='string',
			  metavar="PERM",
			  help="with -s, set the socket permissions to this value.")
	parser.add_option('-p', '--port', type="int", metavar="PORT",
			  dest="port", help="listen on port PORT")
	parser.add_option('-a', '--address', type="string", metavar="ADDR",
			  dest="addr", help="listen at IP address ADDR")
	parser.add_option('', '--systemd-socket', dest="systemd",
			  action="store_true",
			  help="use the systemd socket activation protocol to get our socket (which is assumed to be a Unix socket)")
	parser.add_option('-v', '--verbose', dest="verbose",
			  action="store_true",
			  help="be more verbose")
	parser.set_defaults(sockfile = None, port = None, addr = '',
			    systemd = False,
			    startimmed = False, restartimmed = False,
			    rdelay = 0, edelay = 30, verbose = False,)
	return parser

def die(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))
	sys.exit(1)
def warn(msg):
	sys.stderr.write("%s: %s\n" % (sys.argv[0], msg))

ERRSTATUS = 100
def start_server(s, args):
	sys.stderr.flush()
	sys.stdout.flush()
	pid = os.fork()
	if pid:
		return os.waitpid(pid, 0)[1]

	sockact.sd_set_listen_sockets([s])
	try:
		os.execvp(args[0], args)
	except EnvironmentError, e:
		os._exit(ERRSTATUS)

def wait_str(res):
	if os.WIFEXITED(res):
		if os.WEXITSTATUS(res) == ERRSTATUS:
			return "exit status %d (daemon program may not exist or be executable)" % os.WEXITSTATUS(res)
		else:
			return "exit status %d" % os.WEXITSTATUS(res)
	elif os.WIFSIGNALED(res):
		return "killed by signal %d" % os.WTERMSIG(res)
	else:
		return "unknown wait() result %d" % res
	
def process(s, options, args):
	first = True
	while True:
		if not ((first and options.startimmed) or \
			(not first and options.restartimmed)):
			r = select.select([s], [], [])
			if not r:
				continue
		first = False
		res = start_server(s, args)
		if os.WIFEXITED(res) and os.WEXITSTATUS(res) == 0:
			if options.rdelay:
				time.sleep(options.rdelay)
		elif options.edelay:
			if options.verbose:
				warn("daemon exited badly: %s" % wait_str(res))
			time.sleep(options.edelay)

def main(args):
	# Parse and validate command line options.
	parser = setup_options()
	(options, args) = parser.parse_args(args)
	if len(args) < 1:
		usage()

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

	s = wsgi.gensock.gen_sock(saddr, options)
	try:
		process(s, options, args)
	except KeyboardInterrupt:
		pass

if __name__ == "__main__":
	main(sys.argv[1:])
