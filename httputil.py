#
# Generic utilities for HTTP service.
# These are not part of the core DWiki HTML view code but are still
# HTTP-connector-independant, so we can reuse them between all of
# the connector methods.
#

import urllib, urlparse

import htmlresp

#
# Error processing for situations that are outside of the pages that
# DWiki serves. Because these are not supposed to happen in normal
# operation, we are very short and curt. (We cannot use htmlerr
# because that is for errors inside a DWiki context.)
#
genericErr = """<html><head><title>%d - Request Unsuccessful</title></head>
<body><h1> %d: Request Unsuccessful </h1>
<p> Your request cannot be satisfied. </p>
<p> %s </p>
</body></html>
"""

robotmsg = """You appear to be a web spider or web robot making a request
for something that web robots are not supposed to visit. In particular,
please stop crawling through links marked with <tt>rel="nofollow"</tt>. </p>
<p> If you are not a web robot, we apologize for the problem; please
make sure that your browser environment sends a valid User-Agent header
and try again."""

errorMsgs = {
	"out-of-zone": "Your request is for a URL we do not serve.",
	"file-not-available": "The page you requested is not available.",
	"internal-error": "The server encountered an internal error while processing your request.",
	"sec-error": "You appear to be trying to break this web server. Goodbye.",
	"not-supported": "This server does not support that operation.",
	"web-robot": robotmsg,
	"disallowed": "Access not allowed",
	}

def genError(what, ecode = 404):
	resp = htmlresp.Response()
	resp.error(genericErr % (ecode, ecode, errorMsgs[what]))
	resp.code = ecode
	return resp

# What is the raw path that is incomplete (lacks a final slash).
# We add the slash and return the redirector.
def redirToSlashedDir(what, reqdata):
	resp = htmlresp.Response()
	what = what + '/'
	resp.redirect("%s%s" % (reqdata['server-url'], what))
	return resp

# If path is a child of directory root, return the relative portion of
# the path; '' means the root. Otherwise, return None.
def getRelativePath(root, path):
	root = root.rstrip('/')
	rroot = root + '/'
	# Request for the root:
	if path == root or path == rroot:
		return ''
	# okay, something under the root?
	if path.startswith(rroot):
		return path[len(rroot):]
	# Does not.
	return None

# Because I don't care to be too paranoid, this just picks the last
# one out of a bunch of the same things.
# All hail CJ Silverio, who wrote the code that I stole this from.
def parseQueryStringView(qstr):
	res = {}
	view = None
	for p in qstr.split("&"):
		kv = p.split("=", 1)
		if len(kv) != 2:
			view = p
		else:
			key, value = kv
			value = urllib.unquote_plus(value)
			res[key] = value.replace("\r\n", "\n")
	return (view, res)
# We actively don't want a view for this one.
def parseQueryString(qstr):
	_, res = parseQueryStringView(qstr)
	return res

#
# Apart from the bit where we need to know the original headers of the
# request (or at least the two we really care about), the conditional
# logic of whether we can serve a 304 is standard.
ifModSince = 'HTTP_IF_MODIFIED_SINCE'
ifNoneMatch = 'HTTP_IF_NONE_MATCH'

# Can we send a 304?
# This depends on either or both of last-modified and etags being
# present. The logic is convoluted: if both are present, both must
# match. Otherwise, the present one must match; if neither are
# there, we can't match.
# This logic is generic ... except the bit where we have to be
# dealing with the request headers.
def ifNotModified(environ, resp):
	if resp.code != 200:
		return False
	rLM = environ.get(ifModSince, None)
	rET = environ.get(ifNoneMatch, None)
	aLM = resp.headers.get('Last-Modified', None)
	aET = resp.headers.get('ETag', None)

	# Anything present must match; otherwise, reject.
	if (rLM and rLM != aLM) or (rET and rET != aET):
		return False
	# If we have matchers they match and we're good, assuming
	# that we can match time at all.
	if (rLM and aLM and resp.time_reliable) or (rET and aET):
		return True
	# Otherwise, reject.
	return False

#
# Split a URL to get the query components. We can't use
# urlparse.urlparse() because it will mis-handle double
# slashes. Plus, *no dequoting?!*
def urlsplit(url):
	# We are of the opinion that people who quote the '?' get to
	# lose.
	if '?' not in url:
		return (urllib.unquote(url), '')
	else:
		r = url.split('?', 1)
		return (urllib.unquote(r[0]), r[1])

# You might think that this was simple. You would be ... how shall
# we say ... slightly off. We assume that REQUEST_URI is present.
# The core problem is that SCRIPT_NAME is actually literally the
# name of the script, but it is not necessarily the front of the
# *URL*, after which you can find PATH_INFO.
# (In a WSGI environment, we may be being too picky. Tough.)
def urlFromEnv(env):
	rq = env.get('REQUEST_URI', '')
	#qi = env.get('QUERY_STRING', '')
	if not rq:
		# Well, I guess we fake it.
		base = env.get('SCRIPT_NAME', '') + env.get('PATH_INFO', '')
	else:
		# We must do a full unparse, because some stupid joker may
		# have fed us a URL with a fragment identifier.
		# I'm not sure what we should do in this case; generate an
		# internal URL that is going to be invalid?
		# (PS: I don't know what I was talking about re urlparse
		# and double slashes up above, back when I wrote it.)
		schema, host, base, param, query, frag = urlparse.urlparse(rq)

	return urllib.unquote(base)

# Extract the host from the REQUEST_URI, if it is present.
def hostFromEnv(env):
	rq = env.get('REQUEST_URI', '')
	if not rq:
		return None
	schema, host, base, param, query, frag = urlparse.urlparse(rq)
	return host

# This is lame, partly because I have no real idea what format of IPv6
# addresses web servers in the real world write into $REMOTE_ADDR.
# Checking for '[' at the start is probably conservative.
def is_ipv6_addr(sip):
	return ':' in sip or sip.startswith('[')

#
# Match an IP address -- usually the request source -- against a (string)
# list of IPs or tcpwrappers style IP prefixes. Returns True or False,
# depending on whether or not things matched.
# CHANGED: ipLst much actually be a netblock.IPRanges object; we match
# with 'in'.
def matchIP(sip, ipLst):
	# Check for IPv6 first, because we don't work on IPv6 addresses.
	# Maybe someday.
	if is_ipv6_addr(sip):
		return False
	if not ipLst:
		return False
	return sip in ipLst

# We should do better.
# See http://www.peterbe.com/plog/html-entity-fixer and
# the snippy equivalent in trunk/libraries/snippy/utilities.py
# Unfortunately doing it right requires charset knowledge.
#
# Quoting & < > and " hits all of the printable characters that
# have special encodings. So we'll call it a day (for now).
# http://www.tbray.org/ongoing/When/200x/2004/01/11/PostelPilgrim
# lists "'" as a fifth character I need to quote. Goddamnit, as
# they say.

#import re
#mustquote = re.compile(r'[&<>"]')
#quoteEntities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': "&quot;",
#		  "'": "&apos;", }
#def quotehtml(hstr):
#	def _quote(what):
#		return quoteEntities[what.group(0)]
#	return mustquote.sub(_quote, hstr)

# This is the faster way, used by among others the standard library
# XML code.

# ('I' in this is Daniel Martin, who helped Chris Siebenmann improve this.)
# I use &#39; instead of &apos; because I've found support for &apos;
# in the past to be a bit spotty.  Specifically, &apos; is part of the
# XML 1.0 spec, but IS NOT part of the HTML4 spec.  (see the file 
# HTMLspecial.ent linked from http://www.w3.org/TR/html4/sgml/intro.html)
# Fortunately, both targets respect &#39; as meaning "'".

# NOTE: & MUST COME FIRST.
quoteEntities = (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'),
		 ('"', '&quot;'), ("'", '&#39;'))

def quotehtml(hstr):
	for qe, qs in quoteEntities:
		hstr = hstr.replace(qe, qs)
	return hstr

# This is not quite equal to what wikirend does. Different contexts.
uquoteEntities = (('&', '&amp;'), ('"', '%22'), (' ', '%20'), ('>', '%3E'))
def quoteurl(ustr):
	for qe, qs in uquoteEntities:
		ustr = ustr.replace(qe, qs)
	return ustr
