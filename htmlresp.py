#
# Generic HTML response generation support.
# Most of the variables on this object are public.
import Cookie
import time
import hashlib

redirHtml = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN"
   "http://www.w3.org/TR/html4/loose.dtd">
<html> <head> <title> 302 Found </title> </head>
<body> <h1> Found </h1>
<p> The document has moved <a href="%s">here</a>. </p>
<hr> <address> DWiki </address> </body> </html>
"""

class Response:
	def __init__(self):
		self.code = 200
		self.headers = {'Vary': 'Cookie,Host', }
		self.content = ""
		self.cookie = Cookie.SimpleCookie()
		self.charset = None
		self.time_reliable = False

	def setCharset(self, cs):
		self.charset = cs
	def typestring(self, what):
		if self.charset:
			return "%s; charset=%s" % (what, self.charset)
		else:
			return what

	def redirect(self, where):
		self.code = 301
		self.headers.clear()
		self.headers['Location'] = where
		self.content = redirHtml % where

	def setEtag(self):
		if self.content and self.code == 200:
			h = hashlib.sha1()
			h.update(self.content)
			ahash = h.hexdigest()
			self.headers['ETag'] = '"%s"' % ahash
		elif 'ETag' in self.headers:
			del self.headers['ETag']

	# You can supply a fully formatted thing here, or you can just
	# blort in some basic text and we'll do the rest.
	def error(self, msg, code = 404):
		self.code = code
		self.headers.clear()
		self.headers["Content-Type"] = self.typestring("text/html")
		if msg[0] == '<':
			self.content = msg
		else:
			self.content = "<html><head><title>%d - access denied</title></head>\n<body><p>%s</p></body></html>" % (code, msg)

	def html(self, content):
		self.code = 200
		self.headers['Content-Type'] = self.typestring('text/html')
		self.content = content
		self.setEtag()
	def text(self, content):
		self.code = 200
		self.headers['Content-Type'] = self.typestring('text/plain')
		self.content = content
		self.setEtag()
	def binary(self, content):
		self.code = 200
		self.headers['Content-Type'] = 'application/binary'
		self.content = content
		self.setEtag()
	def arbitrary(self, content, contentType):
		self.code = 200
		self.content = content
		self.headers['Content-Type'] = contentType
		self.setEtag()

	# We are technically potentially violating RFC 2616 here;
	# it requires us not to return timestamps in the future.
	# For our purposes, however, it is actually best to do so.
	def setLastModified(self, timestamp):
		ts = time.gmtime(timestamp)
		asct = time.strftime("%a, %d %b %Y %H:%M:%S GMT", ts)
		self.headers['Last-Modified'] = asct
		self.lastmodified = timestamp
	def setTimeReliable(self):
		self.time_reliable = True
	def setContentLength(self):
		self.headers['Content-Length'] = "%d" % len(self.content)
