#
# DWiki render cache manager
#
import htmlrends

# The validators.
# A new validator verifies nothing and always passes.
class Validator(object):
	def __init__(self):
		self.mtimes = {}
		self.ctimes = {}

	def add_ctime(self, page):
		assert(page.exists())
		self.ctimes[page.path] = page.modstamp
	def add_mtime(self, page):
		assert(page.exists())
		self.mtimes[page.path] = page.timestamp

	def verify(self, ctx):
		for ppath, ts in self.mtimes.items():
			np = ctx.model.get_page(ppath)
			if np.timestamp != ts:
				return False
		for ppath, ts in self.ctimes.items():
			np = ctx.model.get_page(ppath)
			if np.modstamp != ts:
				return False
		return True

# Generate a general or a per-user key name from the general key name.
def keyname(ctx, kn, perUser):
	if perUser and ctx.model.has_authentication() and ctx.login:
		return kn + '-' + ctx.login
	else:
		return kn

def get_cstore(ctx):
	return ctx[':_cachestore']

def cache_on(cfg):
	return 'render-cache' in cfg

CACHENAME = 'renderers'

# Fetch and validate something from cname / sn / path / { key | key2 },
# with the given TTL if any. We try under key first then key2 if necessary
# and useful.
# key is usually general; key2 is per-user.
def _fetch(ctx, cname, sn, path, key, key2, TTL = None):
	cstore = get_cstore(ctx)
	res = cstore.fetch(cname, sn, path, key, TTL)
	if not res and key2 != key:
		res = cstore.fetch(cname, sn, path, key2, TTL)
	if not res:
		return None

	(v, res) = res
	if not v.verify(ctx):
		return None
	else:
		return res

# Errors are reported through the context hook for doing so, if cache
# error warnings are enabled.
# Store data plus validator in cn / sn / path / key. Yes, The order
# of arguments is flipped. TODO: fix?
def _store(ctx, cn, sn, key, path, data, v):
	cstore = get_cstore(ctx)
	s = cstore.store((v, data), cn, sn, path, key)
	if s and 'cache-warn-errors' in ctx:
		ctx.set_error("renderer cache problem: %s" % s)

# Fetch the key NAME for ctx.page (on the current server hostname).
# fetch() has no TTL; the result is valid as long as the validator passes.
def fetch(ctx, name):
	key = keyname(ctx, name, True)
	key2 = keyname(ctx, name, False)
	return _fetch(ctx, CACHENAME, ctx['server-name'], ctx.page.path,
		      key, key2)	

# Fetch a heuristic cache item from 'generators' / 'all' / path / name.
# heuristic generators must be the same for all hosts and have a TTL
# after which they expire.
def fetch_gen(ctx, path, name, TTL = None):
	if TTL is None:
		# Global default heuristic TTL is one hour
		TTL = ctx.cfg.get('render-heuristic-ttl', 60*60)
	return _fetch(ctx, "generators", "all", path, name, name, TTL = TTL)
	
def store(ctx, name, data, v, perUser = False):
	# If this is set, we only cache rendering done for the anonymous
	# default user. Among other things, this avoids a proliferation
	# of identical copies of the file for various different users.
	# (Since most of the time, renderers are the same for people.)
	if perUser and 'render-anonymous-only' in ctx.cfg and \
	   ctx.model.has_authentication() and \
	   (not ctx.is_login_default() or ctx.login is None):
		return

	key = keyname(ctx, name, perUser)
	_store(ctx, CACHENAME, ctx['server-name'], key, ctx.page.path, data, v)

# Store data + v for a heuristic generator: 'generators' / 'all' / path / name
def store_gen(ctx, name, path, data, v):
	_store(ctx, "generators", "all", name, path, data, v)

#
# Flagged heuristic generators are a special type of general heuristic
# generators. In addition to whatever validator is stored with the data,
# they are also invalidated by the presence of a flag quad in the cache
# store that is more recent than they are.
# The value stored in the flag quad is never used; the quad itself is
# stored only in order to update its file time. Flag quads always exist
# in the root of the cache store, so they globally invalidate everything
# when touched.

# Invalidate the given flagname. Flagnames are always stored as
# 'generators' / 'all' / '' / flagname
def invalidate_flagged(ctx, flagname):
	cstore = get_cstore(ctx)
	s = cstore.store("PUSH", "generators", "all", '', flagname)
	if s and 'cache-warn-errors' in ctx:
		ctx.set_error("renderer flag-store cache problem: %s" % s)

# Get a flagged heuristic generator. To be valid, it cannot be older
# than render-heuristic-flagged-ttl (default 48 hours) and it must
# be at least render-heuristic-flagged-delta (default 30 seconds)
# more recent than the flag quad if the latter exists.
# The delta exists to lessen races between cache generation and flag
# quad updates in two separate processes.
def get_flagged(ctx, name, path, flagname):
	TTL = ctx.cfg.get('render-heuristic-flagged-ttl', 60*60*48)
	delta = ctx.cfg.get('render-heuristic-flagged-delta', 30)
	r = _fetch(ctx, "generators", "all", path, name, name, TTL = TTL)
	if not r:
		return r
	# Validate timestamps.
	cstore = get_cstore(ctx)
	t1 = cstore.timeof('generators', 'all', path, name)
	t2 = cstore.timeof('generators', 'all', '', flagname)
	if not t2 or (t2+delta) < t1:
		return r
	else:
		return None

#
# Generic infrastructure for simple caching of something that is
# just dependant on the page.
def simpleCacheWrap(func, name, perUser = False):
	def _cachewrapped(ctx):
		if not cache_on(ctx.cfg):
			return func(ctx)
		# The result is stored as a single-element tuple.
		# This insures that if we have a result that is
		# empty, we can tell it apart from a cache miss.
		res = fetch(ctx, name)
		if res:
			return res[0]
		# Miss; compute, store, return.
		res = func(ctx)
		v = Validator()
		v.add_ctime(ctx.page)
		store(ctx, name, (res,), v, perUser)
		return res
	_cachewrapped.__doc__ = func.__doc__
	return _cachewrapped
def registerSimpleCached(name, func, perUser = False):
	htmlrends.register(name, simpleCacheWrap(func, name, perUser))
