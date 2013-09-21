#
# The core processing for HTTP requests.
# This is common across all methods of getting to HTTP processing,
# whether that is BaseHTTPServer or running as a CGI-BIN.
#
# We are paranoid enough to want to hand the actual DWiki view only
# things we are reasonably certain it can handle, so we pre-filter a
# bunch of things out.
#
# We also handle static files 'directly' (via staticserv's routines)
# instead of handing them off to DWiki views, which means we have to
# check to see whether we're serving out of that URL area or not.
#
# Our paranoia means that we sometimes need to generate our own error
# messages without being able to call on the normal DWiki error page
# services. As a result our error messages can look somewhat minimal.
#

import time
import os
import Cookie
import urllib
import re

import derrors, context
import htmlauth
import httputil, staticserv
import htmlresp

# A pre HTTP/1.0 query will not have a host. For now we just
# lose lose lose, because I don't think I care enough. (Hey,
# it's 2005. Get with the program.)
def getHost(environ):
	if 'HTTP_HOST' in environ:
		return environ['HTTP_HOST']
	elif 'SERVER_NAME' in environ:
		return environ['SERVER_NAME']
	else:
		# For now we have no fallback. In theory we could
		# try looking up our IP address and maybe finding
		# a hostname and etc etc etc. But, you know? No.
		return "YOU-LOSE"

# Get the host+URL schema for cache keying purposes.
# The host will already include the port, or should.
def getHostSchemaKey(environ):
	hst = getHost(environ)
	if environ.get('HTTPS') == "on":
		hst = "https:" + hst
	return hst

# Clean utm_* query parameters from a query dictionary. Returns
# True if there were any.
def clean_utm(qdict):
	utm_seen = False
	ks = qdict.keys()
	for k in ks:
		# Not clear what 'buffer_share' is but legit clients
		# seem to use it. Hate is strong.
		# NewsBlur uses a query parameter of '_=<NNNN>' (a random
		# number) as a (HTTP) cache buster; apparently this makes
		# it work better. So we reluctantly accept it too.
		if k.startswith("utm_") or k == "buffer_share" or k == "_":
			del qdict[k]
			utm_seen = True
	return utm_seen

# We must harvest several pieces of information from the request.
# 1: the host name, for later use in redirects.
# 2: the view (and 2a: any view parameters).
# 3: the path within our Wiki instance that we are requesting.
#    It is a fatal error to fall outside the root of our Wiki
#    instance.
# 4: the decoded raw full path, for use by non-Wiki handlers.
def gather_reqdata(environ):
	cfg = environ['dwiki.cfg']
	webserv = environ['dwiki.web']
	logger = environ['dwiki.logger']

	reqdata = {}
	reqdata['server-name'] = getHost(environ)
	reqdata['server-schemaname'] = getHostSchemaKey(environ)
	if environ.get('HTTPS') == "on":
		reqdata['server-url'] = "https://%s" % reqdata['server-name']
		reqdata['server-schemakey'] = ".https"
	else:
		reqdata['server-url'] = "http://%s" % reqdata['server-name']
		reqdata['server-schemakey'] = ""

	# Break the request URI apart, and decode anything in the
	# query string.
	base = httputil.urlFromEnv(environ)
	reqdata['request-fullpath'] = rawurl = base
	qstr = environ.get('QUERY_STRING', None)

	# Query string decode. Note that even after decoding the query
	# string, we may not have a view.
	v = None
	if qstr:
		v, qdict = httputil.parseQueryStringView(qstr)
		# We clean utm_* parameters here so that we do not
		# generate error messages about them. Sigh.
		# We do not log a message about them.
		if clean_utm(qdict):
			reqdata[':utm:redirect'] = True
		if qdict and not v:
			v = webserv.guess_view(qdict)
			if not v:
				logger.error("unknown parameters in GET: '%s'" % qstr)
				reqdata[':kill:request'] = True
	if v:
		reqdata['view'] = v
		reqdata['view:set'] = True
		# Set the parameter dictionary from allowed URL query
		# parameters.
		plist = webserv.view_param_list(v, environ['REQUEST_METHOD'])
		if qdict and plist:
			paramdict = {}
			#for k in plist:
			#	paramdict[k] = qdict[k]
			for k in qdict.keys():
				if k in plist:
					paramdict[k] = qdict[k]
				else:
					# WHOOP WHOOP.
					logger.error("Invalid GET '%s' parameter '%s=%s'" % (v, k, qdict[k]))
					# We should abort. But at least we
					# log now.
					reqdata[':kill:request'] = True
			if paramdict:
				reqdata['view-dict'] = qdict
	else:
		reqdata['view'] = "normal"

	# If the query does not seem to be at or below our
	# root URL, we do not set the query reqdata variable.
	# This will be detected later and cause us to bail
	# out.
	# (It is not a fatal error now because the URL path
	#  may be one that is handled by a non-HTML-view
	#  thing.)
	query = httputil.getRelativePath(cfg['rooturl'], rawurl)
	if query is not None:
		reqdata['query'] = query

	# Set us up the cookie(s).
	# The cookie comes in as a header 'Cookie:', with all of
	# the cookie values in it.
	if 'HTTP_COOKIE' in environ:
		c = Cookie.SimpleCookie()
		try:
			c.load(environ['HTTP_COOKIE'])
			reqdata['cookie'] = c
		except Cookie.CookieError, e:
			logger.error("Invalid Cookie header: %s: %s" % (repr(environ['HTTP_COOKIE']), str(e)))
	# HTTP command:
	reqdata['http-command'] = environ['REQUEST_METHOD']
	# And version. Anything we don't know about is assumed to
	# be at least 1.1 capable. If you are lying to us, you may
	# lose.
	reqdata['http-version'] = environ['SERVER_PROTOCOL']
	# Remote IP address. (So far no one cares about the remote port.)
	reqdata['remote-ip'] = environ['REMOTE_ADDR']

	return reqdata

# doPOSTMangling mangles reqdata to fix up for POST issues, and in the
# process checks for things we don't want to allow. If it finds
# problems it returns an error response; otherwise, it returns None.
def doPOSTMangling(reqdata, qdict, webserv):
	# Now, there is a complication, and that is that we
	# can't redirect from a POST to a GET without things
	# screaming at us. So we want our POST URLs to look
	# just like the regular URLs. So we have to encode
	# the view in the POST data, nnngh. Which means we
	# have to decode magic.
	# override reqdata view based on the POST data.
	if 'view' in qdict:
		reqdata['view'] = qdict['view']
	# A view in POST is never allowed to be 'normal'.
	# The value here is arbitrary but chosen to be
	# a) distinct and b) likely to fail spectacularly
	# later.
	if reqdata['view'] == "normal":
		# This can only happen if a hand-crafted POST is
		# submitted. In that case -- outta here.
		reqdata['view'] = ":post:claimed-normal"
		# We do not reject this immediately for obscure
		# reasons involving that other things might be
		# handling POST queries that are intercepted later.
		# (I am not convinced that such POSTs can be useful.
		# FIXME: examine later.)

	# We immediately refuse out of zone POSTs before we
	# try to do *anything* more with them.
	if 'query' not in reqdata:
		return httputil.genError("out-of-zone")

	# We do this for clarity (right now), since we currently
	# have no way of logging messages here. FIXME: fix this.
	if not webserv.view_exists(reqdata['view']):
		return httputil.genError("sec-error", 403)

	# Only a few views support POST. Reject invalid views
	# immediately.
	if not webserv.view_cmd_allowed(reqdata['view'], "POST"):
		return httputil.genError("out-of-zone", 405)

	# At this point we must load the request data with the
	# view magic. (Am I starting to rethink the whole mess
	# of how reqdata goes to the HTML view? Yes.)
	view = reqdata['view']
	paramdict = {}
	plist = webserv.view_param_list(view, "POST")
	for key in qdict.keys():
		if key in plist:
			paramdict[key] = qdict[key]
		elif key == "view":
			pass
		else:
			# Not an expected parameter? YOU LOSE.
			return httputil.genError('sec-error', 403)

	if paramdict:
		reqdata['view-dict'] = paramdict
	# We let the HTML view code fix up missing parameters.
	return None

# Remember: network IO does not necessarily deliver all of the bytes
# at once.
# FIXME: we should have a timeout, but that's much more complicated
# and involves select() to do it right and so on.
def readUpTo(fp, count):
	"""Read as much as possible from FP, up to COUNT bytes."""
	td = []
	while count > 0:
		l = fp.read(count)
		if not l:
			break
		td.append(l)
		count -= len(l)
	return ''.join(td)

# We are paranoid about the whole business of getting the POST body.
# (Although not as paranoid as we could be; we have no timeout.)
def getPostBody(environ):
	# First paranoia: is the content type right?
	# (Test case: XML-RPC calls come in as 'text/xml')
	# We can only go on to decide if it is the POST submission
	# type we expect.
	ctype = environ.get("CONTENT_TYPE", None)
	if not ctype:
		raise derrors.ReqErr, "missing content-type on POST"
	elif ctype not in ("application/x-www-form-urlencoded",
			   "application/x-www-form-urlencoded; charset=UTF-8"):
		raise derrors.ReqErr, "bad content-type on POST: '%s'" % ctype

	clength = environ.get("CONTENT_LENGTH", None)
	if clength:
		try:
			cl = int(clength)
		except ValueError:
			raise derrors.ReqErr, "bad value for POST content-length header: '%s'" % clength
		# Reject absurd values. I think >64K counts as absurd,
		# although I may change my mind. Also, 0 or less is bad.
		if cl > 64*1024 or cl <= 0:
			raise derrors.ReqErr, \
			      "Bad content-length in POST: '%s' " % clength
		# TODO: set a time limit on how long we will sit around
		# trying to read this. For now, punt to outside tools for
		# this.
		try:
			postin = readUpTo(environ['wsgi.input'], cl)
			if len(postin) != cl:
				raise derrors.ReqErr, \
				      "error reading POST body: got %d bytes, wanted %d" % (len(postin), cl)
		except EnvironmentError, e:
			raise derrors.ReqErr, \
			      "error reading POST body: "+str(e)

		qdict = httputil.parseQueryString(postin)
	else:
		raise derrors.ReqErr, \
		      "no value for content-length header in POST"
	return qdict

# Set up the environ from stuff.
def environSetup(environ, cfg, ms, webserv, staticstore, cachestore):
	environ['dwiki.starttime'] = time.time()
	environ['dwiki.cfg'] = cfg
	environ['dwiki.model'] = ms
	environ['dwiki.web'] = webserv
	environ['dwiki.staticstore'] = staticstore
	environ['dwiki.cache'] = cachestore

#
# Simple request handling.

# This is the actual DWiki request handler. It must be the bottom of
# the stack, and anything it doesn't handle is thus an error.
def doDwikiRequest(logger, reqdata, environ):
	# Recover things from the environment.
	cfg = environ['dwiki.cfg']
	modelserv = environ['dwiki.model']
	webserv = environ['dwiki.web']

	# If it is not a request for something we handle out of
	# line, and it is not rooted under our root URL, it dies
	# right now.
	if 'query' not in reqdata:
		return httputil.genError("out-of-zone")

	# If the view doesn't exist at all, fail now with a 404.
	# Logging this is somewhat questionable.
	if not webserv.view_exists(reqdata['view']):
		logger.warn("nonexistent view '%s' in HTTP command '%s'" % \
			    (reqdata['view'], reqdata['http-command']))
		return httputil.genError("out-of-zone")

	# Enforce only-accessible-by-POST views. Since we never
	# generate URLs to such zones, they must be being set
	# up by some overly-nasty outsider, so we are abrupt.
	if not webserv.view_cmd_allowed(reqdata['view'],
					reqdata['http-command']):
		logger.warn("view '%s' not allowed in HTTP command '%s'" % \
			    (reqdata['view'], reqdata['http-command']))
		return httputil.genError("sec-error", 403)

	# Picky compliance issue: rooturl is a directory, not
	# a URL prefix. If you gave us the non-directory version,
	# we force a redirect to the directory version ... and
	# will probably re-redirect to the actual front page.
	# AHAHA ahem haclick.
	if not reqdata['query'] and \
	   reqdata['request-fullpath'] and \
	   reqdata['request-fullpath'][-1] != '/':
		return httputil.redirToSlashedDir(reqdata['request-fullpath'],
						  reqdata)

	# Okay, it's something that we serve out of the HTML view.
	try:
		ctx = context.HTMLContext(cfg, modelserv, webserv, reqdata)
		cache = environ['dwiki.cache']
		if cache:
			ctx.setvar(':_cachestore', cache)
		# Try to load authentication if there's a cookie
		# present.
		if 'cookie' in reqdata:
			htmlauth.setLoginFromCookie(ctx, reqdata['cookie'])
		viewer = webserv.viewFactory(ctx)
		resp = viewer.respond()
		# Did the request encounter a reportable error?
		# If so, report it but do not abort response processing
		# to display an error.
		if ctx.errors:
			for e in ctx.errors:
				logger.warn(e)
	except derrors.WikiErr, e:
		logger.error(e)
		resp = httputil.genError("internal-error", 500)
	modelserv.finish()
	return resp

def genPostReqdata(logger, environ):
	# We get the POST body before flailing around establishing our
	# request parameters, because this is slightly faster if something
	# is broken in the POST.
	try:
		qdict = getPostBody(environ)
	except derrors.ReqErr, e:
		logger.warn("security: "+str(e))
		return (None, httputil.genError('sec-error', 403))

	# There are a number of reqdata fixups that we need to
	# do based on the state of the HTTP POST data and
	# similar things. They are all done out of line.
	reqdata = gather_reqdata(environ)
	resp = doPOSTMangling(reqdata, qdict, environ['dwiki.web'])
	return (reqdata, resp)

# This is an ugly way to get Cookie to play well with the
# rest of the world. Cookie *really* wants to splurt out
# complete headers, but everyone else is all 'no, give me
# header/value pairs'.
# So we fish around with manually generating the individual
# cookie values, and lie to them about what the header
# should be in a way that makes it work.
def genCookies(resp):
	cis = resp.cookie.items()
	if not cis:
		return []
	cis.sort()
	ol = []
	for k, v in cis:
		ol.append(('Set-Cookie', v.output(None, '').lstrip()))
	return ol

#
# Map codes to responses.
responseMap = {
	200: 'OK',
	301: 'Moved Permanently',
	302: 'Found',
	303: 'See Other',
	304: 'Not Modified',
	# We generate 403s on security errors that generate 'sec-error',
	# down in httpcore.py. Otherwise, that's it.
	403: 'Forbidden',
	404: 'Not Found',
	405: 'Method Not Allowed',
	500: 'Internal Server Error',
	501: 'Not Implemented',
	503: 'Service Unavailable',
	}

def cook_resp(resp):
	resp.headers['Connection'] = 'close'
	if 'Content-Length' not in resp.headers:
		resp.setContentLength()

def sendHeaders(code, headers, resp, start_response):
	hdrs = list(headers.items()) + genCookies(resp)
	if code in responseMap:
		status = "%d %s" % (code, responseMap[code])
	else:
		status = "%d Something" % code
	start_response(status, hdrs)

def sendHead(resp, start_response):
	cook_resp(resp)
	sendHeaders(resp.code, resp.headers, resp, start_response)
	return ''

	#resp.headers['Connection'] = 'close'
	#if 'Content-Length' not in resp.headers:
	#	resp.setContentLength()
	#hdrs = list(resp.headers.items()) + genCookies(resp)
	#code = resp.code
	#if code in responseMap:
	#	status = "%d %s" % (code, responseMap[code])
	#else:
	#	status = "%d Something" % code
	#start_response(status, hdrs)
	#return ''

# In the WSGI way, sending a response is nothing more than sending
# headers and returning the DWiki content.
def sendResponse(resp, start_response):
	sendHead(resp, start_response)
	return resp.content

	#sendHead(resp, start_response)
	#return resp.content

# A not-modified response is a 304 code, the header, and no body.
# Note that because of in-memory caching, we cannot modify the real
# request to change the code or the headers.
def sendNotModified(resp, start_response):
	cook_resp(resp)
	headers = resp.headers.copy()
	headers['Content-Length'] = '0'
	sendHeaders(304, headers, resp, start_response)
	return ''

	#resp.code = 304
	#resp.headers['Content-Length'] = '0'
	#return sendHead(resp, start_response)

#
#
def get_cfg(env):
	return env['dwiki.cfg']
def get_static(env):
	return env['dwiki.staticstore']

# Is this a request for a static URL?
# We fail POSTs to static URLs early, so we can generate a specific error
# for them.
def StaticServ(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	if staticserv.getStaticPath(cfg, reqdata) is None:
		return next(logger, reqdata, environ)
	# We are serving static stuff, one way or another.
	if environ['REQUEST_METHOD'] == 'POST':
		logger.warn("POST request to a static URL")
		return httputil.genError("sec-error", 403)
	else:
		return staticserv.doStatic(cfg, reqdata, get_static(environ))

#
# In some cases we want to redirect to a canonical hostname for the
# web server.
def CanonRedir(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	chl = cfg['canon-hosts']

	# Ah, the joys of people who send the port as part of
	# the server name. Since they got here, we assume that
	# the port is right.
	host = reqdata['server-name']
	if ':' in host:
		host, _ = host.split(':', 1)

	if host.lower() in chl:
		return next(logger, reqdata, environ)

	# Since we are redirecting to a different host name,
	# this is one of the rare cases when we actually have
	# to put the server port into the redirection URL.
	# (We assume that the target name uses the same port
	# and the same choice of http or https.)
	host = chl[0]
	qs = environ.get('QUERY_STRING', None)
	loc = reqdata['request-fullpath']
	# if we have a canon-host-url setting, all redirections use it
	# instead of the first canon-host.
	if 'canon-host-url' in cfg:
		url = cfg['canon-host-url']
	else:
		us = environ['wsgi.url_scheme']
		port = environ['SERVER_PORT']
		url = '%s://%s' % (us, host)
		if (us == 'http' and port != '80') or \
		   (us == 'https' and port != '443'):
			url += ':'+port
	url += loc
	if qs:
		url += '?'+qs
	resp = htmlresp.Response()
	resp.redirect(url)
	return resp

# Change reqdata['server-url'] so that various sorts of things will
# generate full URLs using our canonical host url. We must also change
# server-name so that caching works right. Note that this means that
# we must be processed *after* CanonRedir but before all caches.
#
# TODO: that we have so many variants of this same basic information
# is a code smell. It should be better.
def CanonHost(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	n = cfg['canon-host-url'].split("//")
	reqdata['server-name'] = n[1]
	reqdata['server-url'] = cfg['canon-host-url']
	if reqdata['server-url'].lower().startswith("https:"):
		reqdata['server-schemakey'] = '.https'
		reqdata['server-schemaname'] = 'https:'+reqdata['server-name']
	else:
		reqdata['server-schemakey'] = ''
		reqdata['server-schemaname'] = reqdata['server-name']
	return next(logger, reqdata, environ)

#
# This is about as simple a brute force cache as we can get. It just
# caches things for bfc-cache-ttl seconds, not worrying about cache
# invalidation or anything. It only cuts in when we're loaded, and
# only for GET/HEAD requests, and only if they don't have a cookie
# header -- basically, for the 'anonymous Slashdot hordes descend'
# case.
#
def get_load():
	try:
		r = os.getloadavg()
		return float(r[0])
	except (EnvironmentError, ValueError):
		return float(0.0)

def is_cacheable_request(cfg, reqdata, environ):
	"""Cacheable requests must be GET or HEAD requests with
	a path and without cookies or view parameters. Also, it
	must not be an Atom feed request that runs into
	feed-max-size-ips"""
	if environ['REQUEST_METHOD'] not in ('GET', 'HEAD') or \
	   environ.get('HTTP_COOKIE', '') or \
	   'view-dict' in reqdata or 'query' not in reqdata:
		return False

	# We cannot serve Atom requests out of a cache at all if
	# this request would hit the feed-max-size-ips feature.
	if 'feed-max-size-ips' in cfg and \
	   reqdata['view'] in ('atom', 'atomcomments', 'rss2') and \
	   httputil.matchIP(reqdata['remote-ip'],
			    cfg['feed-max-size-ips']):
		return False

	# Is cacheable.
	return True

def is_cacheable_resp(resp, cfg, environ):
	"""A cacheable response must have a 200 code and not be from
	a robot listed in bfc-skip-robots."""
	if resp.code != 200:
		return False

	# Do not bother doing BFC caching for robots in bfc-skip-robots
	ua = environ.get('HTTP_USER_AGENT', '')
	if ua and 'bfc-skip-robots' in cfg:
		for rua in cfg['bfc-skip-robots']:
			if rua in ua:
				return False

	return True

BFCNAME = "bfc"
def BFCache(next, logger, reqdata, environ):
	#if environ['REQUEST_METHOD'] not in ('GET', 'HEAD') or \
	#   environ.get('HTTP_COOKIE', '') or \
	#   'view-dict' in reqdata or 'query' not in reqdata:
	cfg = get_cfg(environ)
	if not is_cacheable_request(cfg, reqdata, environ):
		return next(logger, reqdata, environ)

	# In cache? Key is the view, path is the query.
	# (We do not include the request method because GET and HEAD
	# are treated identically at this level, so we actively want
	# to cache them the same.)
	ky = reqdata['view']
	# The query may have a (trailing) /.
	pth = reqdata['query'].rstrip('/')
	hst = reqdata['server-schemaname']
	cache = environ['dwiki.cache']
	#cfg = get_cfg(environ)
	doCache = False

	# Because they are some of our most expensive and at the same
	# time most consistently requested pages, Atom syndication
	# feeds can have an optional (much) larger TTL.
	TTL = cfg['bfc-cache-ttl']
	if reqdata['view'] in ('atom', 'atomcomments', 'rss2'):
		# We cannot serve Atom requests out of the BFC at all
		# if this request would hit the feed-max-size-ips
		# feature; we can neither use the cached BFC results
		# nor cache the results of this query. So get outta
		# here if that's the case.
		#if 'feed-max-size-ips' in cfg and \
		#   httputil.matchIP(reqdata['remote-ip'],
		#		    cfg['feed-max-size-ips']):
		#	return next(logger, reqdata, environ)

		# If you cannot be bothered to do conditional GET, I
		# can't be bothered to serve you fresh content. We
		# force caching and set a likely very large TTL.
		# Note that this has downstream consequences on
		# people who are doing conditional GET.
		if 'bfc-atom-nocond-ttl' in cfg and \
		   httputil.ifModSince not in environ and \
		   httputil.ifNoneMatch not in environ:
			doCache = True
			TTL = cfg['bfc-atom-nocond-ttl']
		elif 'bfc-atom-ttl' in cfg:
			TTL = cfg['bfc-atom-ttl']
		# otherwise the TTL will be left as bfc-cache-ttl.

	# Because we are handling outside paths, they can contain bad
	# crap that will be rejected by the storage layer, so we have
	# to catch that and not abort. CacheKeyErr is a specific internal
	# error for cache key problems.
	try:
		res = cache.fetch(BFCNAME, hst, pth, ky, TTL)
	except derrors.CacheKeyErr:
		return next(logger, reqdata, environ)

	if res:
		return res

	# Miss: fill cache on a 200 response, but only if either the
	# request took 'too long' or the load average is high; this
	# is how we check for 'under load'.
	t0 = time.time()
	res = next(logger, reqdata, environ)

	# We only fill if the request is one that we both can cache
	# and want to cache.
	#if res.code != 200:
	if not is_cacheable_resp(res, cfg, environ):
		return res

	# Do not bother doing BFC caching for robots in bfc-skip-robots
	#ua = environ.get('HTTP_USER_AGENT', '')
	#if ua and 'bfc-skip-robots' in cfg:
	#	for rua in cfg['bfc-skip-robots']:
	#		if rua in ua:
	#			return res

	td = time.time() - t0
	# It is worth micro-optimizing this, since get_load is not
	# necessarily a blazingly fast operation. If the time is
	# real small, we skip the entire load average check.
	tmin = cfg['bfc-time-min']
	ttriv = cfg['bfc-time-triv']
	if td >= tmin:
		doCache = True
	elif td <= ttriv:
		pass
	elif not doCache and 'bfc-load-min' in cfg:
		lmin = cfg['bfc-load-min']
		la = get_load()
		doCache = (la >= lmin)
	if doCache:
		cres = cache.store(res, BFCNAME, hst, pth, ky)
		if cres and 'cache-warn-errors' in cfg:
			environ['dwiki.logger'].warn("BFC problem: %s" % cres)
	return res

# Our very simple in memory cache, like the BFC but simply in our
# memory.  This is only safe in a single process, which means that
# it's only considered useful in a preforking SCGI environment.
# Because it is in memory, it comes before staticurl processing
# (unlike the BFC, which comes afterwards because it is actually
# slower than the staticurl handler).
#
# cache keys are (host, path, view), cache values are (timestamp, resp)
# NOTE: this implies that resp cannot be modified by downstream processing,
# eg to implement not-modified responses. This bit me once.
inMemCache = {}
def InMemFetch(hst, pth, ky, TTL):
	key = (hst, pth, ky)
	r = inMemCache.get(key, None)
	if r is None:
		return r
	now = time.time()
	if r[0]+TTL >= now:
		return r[1]
	else:
		# It's expired, so delete it immediately.
		del inMemCache[key]
		return None
def InMemStore(hst, pth, ky, res):
	inMemCache[(hst, pth, ky)] = (time.time(), res)

def InMemCache(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	if (cfg['wsgi-server-type'] != 'scgi-prefork' and \
	    'imc-force-on' not in cfg):
		return next(logger, reqdata, environ)
	if not is_cacheable_request(cfg, reqdata, environ):
		r = next(logger, reqdata, environ)
		# This can only be a heuristic, since we cannot
		# reach into the IMCs of other processes, but one
		# does what one can.
		if r.cookie.items():
			# It would be slightly better to have an
			# explicit signal to flush the cache, but it's
			# difficult for the low-level code to touch
			# reqdata right now. So we go with 'flush cache
			# if this request tries to set cookies'.
			inMemCache.clear()
		return r

	# Generate the components of the cache key.
	ky = reqdata['view']
	# The query may have a (trailing) /.
	pth = reqdata['query'].rstrip('/')
	hst = reqdata['server-schemaname']

	# If our simple in-memory cache has the key and it's not
	# expired, serve it.
	res = InMemFetch(hst, pth, ky, cfg['imc-cache-ttl'])
	if res:
		return res

	# Generate it.
	res = next(logger, reqdata, environ)
	if not is_cacheable_resp(res, cfg, environ) or \
	   len(res.content) > cfg['imc-resp-max-size']:
		return res

	# Purge entries as necessary. This should normally only purge
	# a single entry. We test >= imc-cache-entries because we are
	# about to add one.
	if len(inMemCache) >= cfg['imc-cache-entries']:
		r = [(inMemCache[x][0], x) for x in inMemCache.keys()]
		r.sort(reverse=True)
		while len(inMemCache) >= cfg['imc-cache-entries']:
			_, key = r.pop()
			del inMemCache[key]

	# Store unconditionally (when cacheable). TODO: really?
	InMemStore(hst, pth, ky, res)
	
	return res


# This is the simple way to get our caches cleared
def CacheCleaner(next, logger, reqdata, environ):
	res = next(logger, reqdata, environ)
	environ['dwiki.cache'].flush()
	return res
# Convenient for testing.
def SlowReq(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	time.sleep(cfg['slow-requests-by'])
	return next(logger, reqdata, environ)

#
# Refuse requests from 'bad robots', robots that shouldn't be traipsing
# through links marked nofollow.
# Note that at this stage in processing, these are explicitly set views,
# not default views for directories; this is why 'blogdir' is safe to
# include. Note that 'normal' is not safe to include, because it is the
# default view that is set by now.
bad_robot_views = ('atom', 'atomcomments', 'writecomment', 'source',
		   'blogdir', 'blog', 'rss2', )
def is_bad_robot_request(environ, reqdata):
	# TODO: it is questionable that this doesn't apply to POST
	# requests. On the other hand, in practice I'm going to ban
	# outright any robot that appears to make POST requests.
	return environ['REQUEST_METHOD'] in ('GET', 'HEAD') and \
	       reqdata['view'] in bad_robot_views

def RobotKiller(next, logger, reqdata, environ):
	if not is_bad_robot_request(environ, reqdata):
		return next(logger, reqdata, environ)

	cfg = get_cfg(environ)
	# Under some configurations, no user agent is considered a
	# bad robot.
	ua = environ.get('HTTP_USER_AGENT', '')
	if not ua and not cfg['no-ua-is-bad-robot']:
		return next(logger, reqdata, environ)
	elif not ua or (ua == 'Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.0; MyIE2; Maxthon)'):
		return httputil.genError("web-robot", 403)

	# Look for bad robots.
	#brl = [x.strip() for x in cfg['bad-robots'].split(' | ')]
	brl = cfg['bad-robots']
	for rua in brl:
		if rua in ua:
			# This error is somewhat arbitrary, yet generally
			# truthful. The file *isn't* available, at least
			# not in that form, to you, and it's because your
			# access is forbidden.
			# was 'file-not-available'.
			return httputil.genError("web-robot", 403)
	# Okay, we're good
	return next(logger, reqdata, environ)

# Kill some robots outright. This is much simpler.
def RobotKiller2(next, logger, reqdata, environ):
	ua = environ.get('HTTP_USER_AGENT', '')
	if not ua:
		return next(logger, reqdata, environ)
	cfg = get_cfg(environ)
	brl = cfg['banned-robots']
	for rua in brl:
		if rua in ua:
			return httputil.genError("web-robot", 403)
	return next(logger, reqdata, environ)

# Kill requests for banned robot views from specific IP addresses.
def IpBadKiller(next, logger, reqdata, environ):
	if not is_bad_robot_request(environ, reqdata):
		return next(logger, reqdata, environ)

	cfg = get_cfg(environ)
	ipl = cfg['bad-robot-ips']
	sip = environ.get('REMOTE_ADDR', '')
	# no remote IP means immediate kill
	if not sip or httputil.matchIP(sip, ipl):
		logger.warn("banned robot IP denied access")
		return httputil.genError("disallowed", 403)
	else:
		return next(logger, reqdata, environ)

# Kill bad sources outright, in cases where we cannot use Apache
# access controls or whatever.
def IpKiller(next, logger, reqdata, environ):
	cfg = get_cfg(environ)
	ipl = cfg['banned-ips']
	sip = environ.get('REMOTE_ADDR', '')
	# no remote IP means immediate kill
	if not sip or httputil.matchIP(sip, ipl):
		logger.warn("banned IP denied access")
		return httputil.genError("disallowed", 403)
	else:
		return next(logger, reqdata, environ)

# Kill bad comment sources outright, like IpKiller/banned-ips, but only
# for attempts to leave comments. This is less severe than banned-ips and
# so can be applied more broadly. Note that this applies to both GET and
# POST requests.
def IpCommentKiller(next, logger, reqdata, environ):
	if reqdata['view'] != 'writecomment':
		return next(logger, reqdata, environ)
	cfg = get_cfg(environ)
	ipl = cfg['banned-comment-ips']
	sip = environ.get('REMOTE_ADDR', '')
	if not sip or httputil.matchIP(sip, ipl):
		logger.warn("banned comment IP denied access")
		return httputil.genError("disallowed", 403)
	else:
		return next(logger, reqdata, environ)

# Kill insane requests, so that lower layers don't have to deal with
# various sorts of irritating explosions.
# We cannot completely validate the path because of synthetic pages
# like /.login; they would come up as invalid if we tried the full
# ruleset.
def InsaneKiller(next, logger, reqdata, environ):
	ri = environ.get("REQUEST_URI", '')
	# 'http://..../..' URLs are legitimate, at least for
	# HTTP/1.1 requests, and so we generously accept them
	# all the time.
	if ri and '#' in ri:
		return httputil.genError('out-of-zone')
	# The advantage of using this is that it is post-decode.
	bu = reqdata['request-fullpath']
	if bu and ('//' in bu or '/../' in bu):
		return httputil.genError('out-of-zone')
	# HTTP Referer values including ', ' (or just space) are a)
	# illegal and b) the sign of certain spambots. We fail them
	# on POSTs.
	rt = environ.get('HTTP_REFERER', None)
	if rt and environ['REQUEST_METHOD'] == 'POST' and ', ' in rt:
		# this is sort of accurate. sort of.
		environ['dwiki.logger'].warn("rejected POST with bogus HTTP Referer")
		return httputil.genError("web-robot", 403)
	return next(logger, reqdata, environ)

# Fix HTTP_HOST in the presence of http://.../ GETs.
# The HTTP/1.1 RFC behavior is that the http://.../ hostname takes
# priority over the Host: header; we implement this by overwriting
# the HTTP_HOST header itself (and the copy in the reqdata, whoops)
# with the new value.
def HostFixer(next, logger, reqdata, environ):
	uh = httputil.hostFromEnv(environ)
	if uh:
		# TODO: updating environ is the sign of a hack.
		environ['HTTP_HOST'] = uh
		reqdata['server-name'] = uh
		if environ.get('HTTPS') == "on":
			reqdata['server-url'] = "https://%s" % uh
		else:
			reqdata['server-url'] = "http://%s" % uh
	return next(logger, reqdata, environ)

# Check for people playing stupid games with Host: header values.
# See http://www.skeletonscribe.net/2013/05/practical-http-host-header-attacks.html
host_re = re.compile("^[-_a-z0-9A-Z.]+(:[0-9]+)?$")
def valid_host(hn):
	return bool(host_re.match(hn))
def HostKiller(next, logger, reqdata, environ):
	uh = httputil.hostFromEnv(environ)
	if uh and not valid_host(uh):
		environ['dwiki.logger'].warn("rejected invalid Host: value from request URI: %s" % repr(uh))
		return httputil.genError("sec-error", 403)
	gh = getHost(environ)
	if not valid_host(gh):
		environ['dwiki.logger'].warn("rejected invalid Host: value from Host: header: %s" % repr(gh))
		return httputil.genError("sec-error", 403)
	return next(logger, reqdata, environ)

# This is about as lame as you can get, but it's very nicely
# encapsulated.
def ReqKiller(next, logger, reqdata, environ):
	if ':kill:request' in reqdata:
		return httputil.genError("sec-error", 403)
	else:
		return next(logger, reqdata, environ)

# If we have stripped utm_* query parameters from the query dictionary,
# do not serve the actual page under the utm-izer URL; instead, generate
# a redirection to the real (de-utm_*-ized) URL of the page.
# (How this works is modeled on ReqKiller, for the same reason.)
#
# We deliberately place this after a number of processing steps that
# can fail the request; if we've banned the IP or the request has other
# bad query parameters or so on, we want the request to fail immediately
# instead of being redirected and then failing.
def UtmRedirecter(next, logger, reqdata, environ):
	if ':utm:redirect' not in reqdata:
		return next(logger, reqdata, environ)

	# We are neurotically complete about generating the real URL
	# of the page, including corner cases like having other
	# legitimate query parameters.	
	us = environ['wsgi.url_scheme']
	host = reqdata['server-name']
	loc = reqdata['request-fullpath']
	qs = []
	if 'view:set' in reqdata:
		qs.append(reqdata['view'])
	if 'view-dict' in reqdata:
		qs.extend(["%s=%s" % (x[0], urllib.quote_plus(x[1])) \
			   for x in reqdata['view-dict'].items()])
	url = '%s://%s%s' % (us, host, loc)
	if qs:
		url += '?' + '&'.join(qs)
	resp = htmlresp.Response()
	resp.redirect(url)
	return resp

#
# Possibly this should be on the logger. Call it history.
# FIXME: definitely on the logger.
def logMsg(msg, env):
	cfg = get_cfg(env)
	el = env['wsgi.errors']
	if 'stamp-messages' not in cfg:
		el.write(msg)
	else:
		ts = time.strftime("%a %b %d %H:%M:%S %Y")
		el.write("[%s] [note] [client %s] %s" %
			 (ts, env.get("REMOTE_ADDR", "na?"), msg))

# This is a cheap hack for simple profiling.
def DumpTime(next, logger, reqdata, environ):
	res = next(logger, reqdata, environ)
	req = environ['REQUEST_URI']
	td = time.time() - environ['dwiki.starttime']
	if td >= 0.0001:
		logMsg("dwiki timing: %.3g second for %s\n" % (td, req),
		       environ)
	return res

# Dump information about Atom requests.
atomMap = (('REQUEST_URI', 'Req'), ('HTTP_HOST', 'H'),
	   ('HTTP_IF_MODIFIED_SINCE', 'IMS'),
	   ('HTTP_IF_NONE_MATCH', 'INM'),
	   )
def DumpAtom(next, logger, reqdata, environ):
	resp = next(logger, reqdata, environ)
	# We are only interested in successful GET requests for Atom
	# feeds that have a User-Agent header. Do we have one?
	qs = environ.get('QUERY_STRING', '')
	ua = environ.get('HTTP_USER_AGENT', '')
	if qs not in ('atom', 'atomcomments', 'rss2') or \
	   environ['REQUEST_METHOD'] != 'GET' or \
	   not ua or resp.code != 200:
		return resp

	# This had better not mangle the request!
	hit = httputil.ifNotModified(environ, resp)

	# Dump copious information.
	l = ["UA: '%s'" % ua]
	present = []
	for evar, astr in atomMap:
		er = environ.get(evar)
		if er:
			l.append("%s: '%s'" % (astr, er))
			present.append(astr)
	l.append(hit and "matched" or "no-match")
	# If we missed, record what *our* values are, so we can see
	# why we missed.
	if not hit:
		if 'INM' in present:
			l.append("ETag: '%s'" % resp.headers['ETag'])
		if 'IMS' in present:
			l.append("LM: '%s'" % resp.headers['Last-Modified'])

	# Dump out. Note that we don't use the logger interface for
	# this.
	logMsg("dwiki debug atomgood: %s\n" % ", ".join(l), environ)
	return resp

#
# Translate from an incoming WSGI request into a DWiki request.
# Everything below this is DWiki handling.
#
# This is not *quite* a WSGI top level. A true WSGI top level
# has to introduce a number of dwiki.* variables into the
# environment. We could do that here, but one of them is the
# logger, so that's actually above us right now.
#
class WSGIDwikiTrans:
	def __init__(self, next):
		self.next = next

	def callNext(self, logger, environ):
		return self.next(logger, gather_reqdata(environ), environ)

	def do_HEAD(self, logger, environ, start_response):
		return sendHead(self.callNext(logger, environ), start_response)

	def do_GET(self, logger, environ, start_response):
		resp = self.callNext(logger, environ)
		if httputil.ifNotModified(environ, resp):
			return sendNotModified(resp, start_response)
		else:
			return sendResponse(resp, start_response)

	def do_POST(self, logger, environ, start_response):
		reqdata, resp = genPostReqdata(logger, environ)
		# If we did not abort getting POST data,
		# actually process the POST.
		if not resp:
			resp = self.next(logger, reqdata, environ)
		return sendResponse(resp, start_response)

	# Note a technicality: WSGI apps return an iterable object
	# as their result, not a single object, so we must wrap up
	# our data return in a list.
	def __call__(self, environ, start_response):
		cmd = environ['REQUEST_METHOD']
		logger = environ['dwiki.logger']
		if cmd not in ('GET', 'HEAD', 'POST'):
			# 5xx series errors are theoretically things that
			# can be retried later; that makes 501 the wrong
			# code here. 405 implies 'permanent failure'.
			resp = httputil.genError('not-supported', 405)
			return [sendResponse(resp, start_response)]
		else:
			cfunc = getattr(self, "do_"+cmd)
			return [cfunc(logger, environ, start_response)]

#
# List the optional DWiki modules, with the configuration file
# variable that controls if they are active.
# This is in inverted order; entries at the end are at the 'top' of the
# stack of request handlers.
dwikiStack = (
	(SlowReq, 'slow-requests-by'),
	(BFCache, 'bfc-cache-ttl'),
	(StaticServ, 'staticurl'),
	(InMemCache, 'imc-cache-entries'),
	(CanonHost, 'canon-host-url'),
	(CanonRedir, 'canon-hosts'),
	(UtmRedirecter, ''),
	(CacheCleaner, 'cachedir'),
	(IpBadKiller, 'bad-robot-ips'),
	(RobotKiller, 'bad-robots'),
	(RobotKiller2, 'banned-robots'),
	(IpCommentKiller, 'banned-comment-ips'),
	(IpKiller, 'banned-ips'),
	(InsaneKiller, ''),
	(ReqKiller, ''),
	(HostFixer, ''),
	(HostKiller, ''),
	(DumpAtom, 'dump-atom-reqs'),
	(DumpTime, 'dump-req-times'),
	)
def cocoon(func, next):
	return lambda lg, rd, env: func(next, lg, rd, env)
def genDwikiStack(cfg):
	cur = doDwikiRequest
	for func, cfgvar in dwikiStack:
		if not cfgvar or (cfgvar in cfg):
			cur = cocoon(func, cur)
	return WSGIDwikiTrans(cur)
