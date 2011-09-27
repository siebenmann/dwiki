#
# A SCGI WSGI server. This is made much, much easier than FastCGI
# because SCGI is actually a very simple protocol (thank god).
# (See http://www.mems-exchange.org/software/scgi/)
#
# This is a forking server; at each request we fork(), and handle
# the request in children. We are somewhat cavalier about reaping
# children; zombies may hang around until the next request.
#

import sys, os, socket
import time

# Netstring utility functions.
# Note that because Python annoyingly has different interfaces between
# sockets and regular files, we must explicitly use .recv() instead of
# .read(). Sigh.
# (Except that I just changed this to use real files, for the buffering.)
class NSError(Exception):
	pass

def ns_read_size(sock):
	"""Read the size of a netstring from SOCK."""
	size = ""
	while 1:
		c = sock.read(1)
		if c == ':':
			break
		elif not c:
			raise NSError, 'short netstring read on length'
		size += c
	return long(size)

def ns_reads(sock):
	"""Read a netstring from SOCK."""
	size = ns_read_size(sock)
	data = ""
	while size:
		s = sock.read(size)
		if not s:
			raise NSError, 'short netstring read, not enough data'
		data += s
		size -= len(s)
	if sock.read(1) != ',':
		raise NSError, 'missing netstring terminator'
	return data

#
# Read the SCGI environment from a new connection.
class SCGIProtoErr(Exception):
	pass

def _read_env(sock):
	hdrs = ns_reads(sock)
	if not hdrs:
		raise SCGIProtoErr, "header netstring empty"
	elif hdrs[-1] != '\0':
		raise SCGIProtoErr, "headers not terminated with a null byte"
	items = hdrs.split("\0")
	del items[-1]
	if (len(items) % 2) != 0:
		raise SCGIProtoErr, "uneven number of header items: "+repr(items)

	# There *has* to be a better idiom for this than this.
	d = {}
	while items:
		k = items.pop(0); v = items.pop(0)
		d[k] = v
	return d

# Handle a single SCGI connection.
# Errors do not produce stack pukes, they produce reports on standard error.
def repError(msg):
	ts = time.strftime("%a %b %d %H:%M:%S %Y")
	sys.stderr.write("[%s] [error] scgi-to-wsgi: %s\n" % (ts, msg))
	
def do_scgi(sock, app):
	# WSGI requires that we actually have file like objects.
	# Plus, they do buffering, which reduces the number of
	# syscalls we need to make.
	fd = os.dup(sock.fileno())
	infp = os.fdopen(fd)

	try:
		env = _read_env(infp)
	except NSError, e:
		repError("netstring error: %s" % str(e))
		return
	except SCGIProtoErr, e:
		repError("SCGI protocol error: %s" % str(e))
		return
	except (EnvironmentError, socket.error), e:
		repError("cannot read environment: %s" % str(e))
		return

	# Set up WSGI variables.
	d = {
		'wsgi.version': (1, 0),
		'wsgi.multithread': 0,
		'wsgi.multiprocess': 1,
		'wsgi.run_once': 1,
		'wsgi.input': infp,
		'wsgi.errors': sys.stderr,
		}
	env.update(d)
	if env.get('HTTPS', 'off') in ('on', '1'):
		env['wsgi.url_scheme'] = 'https'
	else:
		env['wsgi.url_scheme'] = 'http'


	# Somehow I wish I had a generic handler for this.
	# FIXME: code smell duplication with cgi.py (and implicitly
	# with server.py).
	headers_set = []
	headers_sent = []
	def write(data):
		if not headers_set:
			raise AssertionError("write() before start_response()")
		elif not headers_sent:
			outh = []
			status, response_headers = \
				headers_sent[:] = headers_set
			try:
				outh.append("Status: %s\r\n" % status)
				for header in response_headers:
					outh.append("%s: %s\r\n" % header)
				outh.append("\r\n")
				sock.sendall(''.join(outh))
			except (socket.error, EnvironmentError):
				pass
		try:
			sock.sendall(data)
		except (socket.error, EnvironmentError):
			pass

	def start_response(status, response_headers, exc_info = None):
		if exc_info:
			try:
				if headers_sent:
					raise exc_info[0], exc_info[1], \
					      exc_info[2]
			finally:
				exc_info = None
		elif headers_set:
			raise AssertionError("Headers already set!")

		headers_set[:] = [status, response_headers]
		return write

	result = app(env, start_response)

	try:
		for data in result:
			if data:
				write(data)
		if not headers_sent:
			write('')
	finally:
		if hasattr(result, 'close'):
			result.close()
	infp.close()
	sock.close()

class SCGIWSGIServer(object):
	def __init__(self, addr, app, stopfunc, options):
		self.wsgi_app = app
		self.ccount = 0
		self.stopfunc = stopfunc
		self.stopfile = options.stopfile
		self.maxconn = options.maxconn
		if len(addr) == 2:
			s = socket.socket()
			perms = None
		else:
			s = socket.socket(socket.AF_UNIX,
					  socket.SOCK_STREAM)
			perms = options.perms
		s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		s.bind(addr)
		s.listen(40)
		self.sock = s
		if perms:
			os.chmod(addr, perms)

	def serve_forever(self):
		while 1:
			conn, addr = self.sock.accept()
			self.delegate_request(conn)
			conn = None
			if self.stopfunc and self.stopfunc():
				break

	def reap_kids(self):
		while 1:
			try:
				(pid, status) = os.waitpid(-1, os.WNOHANG)
			except EnvironmentError:
				return
			if pid <= 0:
				break
			assert(self.ccount > 0)
			self.ccount -= 1

	def delegate_request(self, conn):
		self.reap_kids()
		# Over the maximum? Drop the request.
		if self.ccount >= self.maxconn:
			repError("Dropping connection, over maxconn")
			return

		self.ccount += 1
		pid = os.fork()
		if pid != 0:
			# In parent? We're done; bail.
			return

		do_scgi(conn, self.wsgi_app)
		os._exit(0)

def gen_server(server_address, wsgi_app, stopfunc, options):
	return SCGIWSGIServer(server_address, wsgi_app, stopfunc, options)
