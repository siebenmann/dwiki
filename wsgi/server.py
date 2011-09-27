#
# This is taken from various places.
#
# As such I can't guarantee it's a fully conformant WSGI server.
# As usual: seems to work for me!
import sys, os, socket

import urlparse

__pychecker__ = "no-shadowbuiltin no-argsused"
import BaseHTTPServer
import SocketServer
__pychecker__ = ""

hdrMapping = (
	('Content-Type', 'CONTENT_TYPE'),
	('Content-Length', 'CONTENT_LENGTH'),
	)
def env_name(hn):
	return 'HTTP_'+hn.upper().replace('-', '_')

class WSGIRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):

	def get_environ(self):
		serv_addr, serv_port = self.server.server_address
		server = self.server
		# ARGH. This is what you told the server to bind to,
		# possibly '' for INADDR_ANY. Sigh.
		if not serv_addr:
			serv_addr = "YOU-LOSE"
		clnt_addr, clnt_port = self.client_address
		_, _, path, query, _ = urlparse.urlsplit(self.path)
		environ = {
			'SERVER_PROTOCOL': self.request_version,
			'SERVER_NAME': serv_addr,
			'SERVER_PORT': str(serv_port),
			'REMOTE_ADDR': clnt_addr,
			'REMOTE_PORT': str(clnt_port),
		
			'REQUEST_METHOD': self.command,
			'REQUEST_URI': self.path,
			'SCRIPT_NAME': '',
			'PATH_INFO': path,
			'QUERY_STRING': query,

			'wsgi.version': (1, 0),
			'wsgi.url_scheme': 'http',
			'wsgi.multithread': server.wsgi_multithread,
			'wsgi.multiprocess': server.wsgi_multiprocess,
			'wsgi.run_once': server.wsgi_runonce,
			'wsgi.input': self.rfile,
			'wsgi.errors': sys.stderr,
			}
		# Add headers from the request.
		for hn, evnm in hdrMapping:
			if hn in self.headers:
				environ[evnm] = self.headers[hn]
		for k, v in self.headers.items():
			environ[env_name(k)] = v

		return environ

	def wsgi_write_headers(self, status, response_headers):
		code, message = status.split(" ", 1)
		try:
			self.send_response(int(code), message)
			for k, v in response_headers:
				self.send_header(k, v)
			self.end_headers()
		except (EnvironmentError, socket.error):
			pass

	def wsgi_write(self, data):
		if not self.wsgi_headers:
			raise AssertionError, "write() before start_response()"
		elif not self.wsgi_headers_sent:
			status, response_headers = self.wsgi_headers
			self.wsgi_headers_sent = True
			self.wsgi_write_headers(status, response_headers)
		try:
			self.wfile.write(data)
			self.wfile.flush()
		except (EnvironmentError, socket.error):
			pass

	def wsgi_start_response(self, status, response_headers,
				exc_info = None):
		if exc_info:
			try:
				if self.wsgi_headers_sent:
					raise exc_info[0], exc_info[1], \
					      exc_info[2]
			finally:
				exc_info = None
		elif self.wsgi_headers:
			raise AssertionError, "Headers already set!"
		self.wsgi_headers = [status, response_headers]
		return self.wsgi_write
		
	def wsgi_execute(self):
		environ = self.get_environ()
		app = self.server.wsgi_app
		self.wsgi_headers = None
		self.wsgi_headers_sent = False
		result = app(environ, self.wsgi_start_response)
		try:
			for data in result:
				if data:
					self.wsgi_write(data)
			if not self.wsgi_headers_sent:
				self.wsgi_write('')
		finally:
			if hasattr(result, 'close'):
				result.close()

	# We don't handle everything.
	do_POST = do_GET = do_HEAD = do_DELETE = do_PUT = do_TRACE = \
		  wsgi_execute

class WSGIServer(BaseHTTPServer.HTTPServer):
	# We cannot claim HTTP/1.1 compliance.
	protocol_version = "HTTP/1.0"
	server_version = "WSGIHTTP/0.5"
	wsgi_multithread = 0
	wsgi_multiprocess = 0
	wsgi_runonce = 0
	def __init__(self, server_address, wsgi_app):
		self.wsgi_app = wsgi_app
		BaseHTTPServer.HTTPServer.__init__(self, server_address,
						   WSGIRequestHandler)

class WSGIThreadServer(SocketServer.ThreadingMixIn, WSGIServer):
	wsgi_multithread = 1

class WSGIForkServer(SocketServer.ForkingMixIn, WSGIServer):
	wsgi_multiprocess = 1
	wsgi_runonce = 1

def gen_server(server_address, wsgi_app, stype = "thread"):
	__pychecker__ = "no-returnvalues"
	if stype == "thread":
		return WSGIThreadServer(server_address, wsgi_app)
	elif stype == "fork":
		return WSGIForkServer(server_address, wsgi_app)
	else:
		return WSGIServer(server_address, wsgi_app)
