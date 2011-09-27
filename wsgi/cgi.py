#
#
# This is taken more or less verbatim from
# http://www.python.org/doc/peps/pep-0333/
#
# It looks better with less indentation, too. Oh well.
import sys, os

def run_with_cgi(app):
	environ = dict(os.environ.items())
	environ['wsgi.input']        = sys.stdin
	environ['wsgi.errors']       = sys.stderr
	environ['wsgi.version']      = (1,0)
	environ['wsgi.multithread']  = False
	environ['wsgi.multiprocess'] = True
	environ['wsgi.run_once']     = True
	if environ.get('HTTPS','off') in ('on','1'):
		environ['wsgi.url_scheme'] = 'https'
	else:
		environ['wsgi.url_scheme'] = 'http'

	headers_set = []
       	headers_sent = []

	# Our one modification from the PEP is to catch write errors and
	# muzzle them, because people do do this.
	def write(data):
		if not headers_set:
			raise AssertionError("write() before start_response()")
		elif not headers_sent:
			status, response_headers = \
				headers_sent[:] = headers_set
			try:
				sys.stdout.write("Status: %s\r\n" % status)
				for header in response_headers:
					sys.stdout.write("%s: %s\r\n" % header)
				sys.stdout.write("\r\n")
			except EnvironmentError:
				pass
		try:
			sys.stdout.write(data)
			sys.stdout.flush()
		except EnvironmentError:
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

	result = app(environ, start_response)
	
	try:
		for data in result:
			if data:
				write(data)
		if not headers_sent:
			write('')
	finally:
		if hasattr(result, 'close'):
			result.close()
