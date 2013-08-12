#
# Each request or piece of a request has a context; here we capture
# and export that information. The context includes, eg, the page being
# requested, but it also includes global information such as the overall
# configuration.
#
# As a general rule, it is the *context* that knows what various
# configuration things mean.
#
# ... everybody dance.
import copy

import utils
import rendcache

class Context(object):
	def __init__(self, cfg, model):
		self.cfg = cfg
		self.modtime = 0
		self.time_reliable = True
		self._vars = {}
		self._vars.update(self.cfg)
		self._perpage = set()
		self.features = []
		self._cache = {}
		self.model = model
		self.errors = []
		self.authcache = {}

		# Generics:
		self.page = None
		self.login = None
		self.logout()

	# To clone we must insure that the vars context is not shared
	# and dump the cache. We also kill maxtime.
	def clone(self):
		nc = copy.copy(self)
		nc._vars = {}
		nc._vars.update(self._vars)
		nc._cache = {}
		nc.maxtime = 0
		# Errors propagate, so all of the lists stay the same
		# as the master list.
		#nc.error = None
		# Kill features.
		nc.features = []
		return nc
	# This is what we really mostly want to do.
	def clone_to_page(self, npage):
		nc = self.clone()
		nc.set_page(npage)
		return nc

	#
	# We look dict-like, based on self._vars.
	def __contains__(self, key):
		return key in self._vars
	def __getitem__(self, key):
		return self._vars[key]
	def get(self, key, default = None):
		return self._vars.get(key, default)

	def getfirst(self, *keys):
		for k in keys:
			if k in self._vars:
				return self._vars[k]
		return None

	# perpage is True if this is a page-specific variable that should
	# be cleared in set_page().
	def setvar(self, key, value, perpage=False):
		self._vars[key] = value
		if perpage:
			self._perpage.add(key)
		
	def delvar(self, key):
		if key in self._vars:
			del self._vars[key]
	def setviewvar(self, key, value, view = None):
		if not view:
			view = self['view-format']
		self._vars[':%s:%s' % (view, key)] = value
		if key == "page":
			self._vars[':post:page'] = value
	def getviewvar(self, key, view = None):
		if not view:
			view = self['view-format']
		return self._vars.get(":%s:%s" % (view, key), None)

	def set_error(self, err):
		self.errors.append(err)

	# Features:
	def addfeature(self, what):
		if what in self.features:
			return
		self.features.append(what)
	def hasfeature(self, what):
		return what in self.features
	# NOTE: this is a *copy* of the features list.
	def getfeatures(self):
		return self.features[:]

	# Passing in a time of -1 destroys the timekeeping ability
	# (and will eventually suppress any 'most recent update' output).
	def newtime(self, time):
		self.modtime = max(self.modtime, time)
	def unrel_time(self):
		self.time_reliable = False

	# Cache operations:
	def setcache(self, key, what):
		self._cache[key] = what
	def getcache(self, key):
		return self._cache.get(key, None)
	def dropcache(self):
		self._cache = {}

	#
	# Now, everything so far is nice and generic, but you know we're
	# not generic; we're about us. Us us. This means we need some
	# operations to manipulate ourselves at what could be called
	# a higher level.
	def logout(self):
		if 'defaultuser' in self.cfg and \
		   self.model.has_authentication():
			self.login = self.cfg['defaultuser']
		else:
			self.login = None
		self._logstate()
	def do_login(self, user):
		self.login = user
		self._logstate()
	def _logstate(self):
		if self.login and self.model.get_user(self.login):
			self._vars['login'] = self.login
		else:
			if 'login' in self._vars:
				del self._vars['login']
			self.login = None
		self.clearauthcache()
	def is_login_default(self):
		return 'defaultuser' in self.cfg and \
		       self.login == self.cfg['defaultuser']
	def default_user(self):
		return self.cfg.get("defaultuser", None)
	def current_user(self):
		return self.model.get_user(self.login)

	# Authentication cache support.
	def clearauthcache(self):
		self.authcache = {}
	def getauthent(self, type, what):
		if type in self.authcache and \
		   what in self.authcache[type]:
			return self.authcache[type][what]
		return None
	def setauthent(self, type, what, val):
		if not type in self.authcache:
			self.authcache[type] = {}
		self.authcache[type][what] = val

	def set_page(self, page):
		# Clear page-specific variables for the old page.
		for v in self._perpage:
			del self._vars[v]
		self._perpage = set()

		self.page = page
		# We don't setvar() these as per-page variables because
		# we reset them here anyways. Per-page variables are really
		# for outside code that sets per-page information, like the
		# wikitext rendering.
		self.setvar("page", page.path)
		self.setvar("pagename", page.name)
		self.setvar("pagetype", page.type)
		self.setvar("abspage", '/'+page.path)

	# Extraction of configuration information ho:
	def wiki_root(self):
		return self.getfirst('wikiroot', 'wikiname')


#
# We have one subspecies of context: the context for a HTTP request.
# The main job of this subclass is to set us up the bomb from all
# the harvested data about the request.
class HTMLContext(Context):
	def __init__(self, cfg, model, web, reqdata):
		super(HTMLContext, self).__init__(cfg, model)
		# Our hook for being able to get at web services,
		# as opposed to model services.
		self.web = web

		# Some data on HTTP settings.
		self.setvar("http-command", reqdata['http-command'])
		self.setvar("http-version", reqdata['http-version'])
		self.setvar("remote-ip", reqdata['remote-ip'])
		# The request data should also have the core info
		# necessary to generate a redirect, namely the full
		# host URI/etc.
		self.setvar('server-name', reqdata['server-name'])
		self.setvar('server-url', reqdata['server-url'])
		# Cache key suffix for http versus https. This is a hack.
		self.setvar('server-schemakey', reqdata['server-schemakey'])

		# Set us up the current page. This is url-decoded
		# and stripped of the rooturl plus a leading slash.
		rawname = reqdata['query']
		pagename = rawname
		if pagename and pagename[-1] == '/':
			pagename = pagename[:-1]

		# We store the raw query so that we can see if
		# people put the slash on the end of directories
		# like they should.
		self.setvar('page-rawname', rawname)

		# Set the current page.
		# The current page may be completely bogus, which
		# we have to check for later when we care and can
		# do something about it.
		self.set_page(self.model.get_page(pagename))

		self.setvar("view-format", reqdata["view"])
		if 'view:set' in reqdata:
			self.setvar("view-format-set-explicitly", True)
		# Handy sometimes.
		self.view = self['view-format']

		# Now we must set the view parameters.
		# As a convention, if the view parameters supply a
		# 'page' argument we also put it in ':post:page'.
		# 'page' is our generic 'we are actually working
		# on this one over here I hate POST' POST variable
		# for this, so we might as well make it easy.
		if 'view-dict' in reqdata:
			for k, v in reqdata['view-dict'].items():
				self.setviewvar(k, v)

	def setview(self, view):
		self.view = view
		self.setvar("view-format", view)

	# The active view is the view *iff* the view was set
	# explicitly, and otherwise None.
	def active_view(self):
		if 'view-format-set-explicitly' in self._vars:
			return self['view-format']
		else:
			return None

	# Which view shows comments?
	# Technically this might want to be a per-page property in the
	# long run, but my head hurts.
	def comment_view(self):
		if 'comments-in-normal' in self:
			return None
		else:
			return 'showcomments'

	# Obtaining the URL or the URI for a page is not a page operation
	# because a page may exist in multiple contexts with different
	# URLs.
	# .url() and .uri() default to the current view if the current
	# view has been set explicitly and the target can take the current
	# view.
	def _getview(self, page, view):
		if not view and self.active_view():
			view = self.active_view()
			pv = self.pref_view(page)
			if not self.web.page_view_ok(self, page, view):
				view = None
			elif pv == view or \
			     (pv is None and view == "normal"):
				# If the view is the page's default
				# view, return it as such.
				view = None
		return view
	def url(self, page, view = None, viewparams = None):
		view = self._getview(page, view)
		return self.web.url_from_path(page.path, view, viewparams)

	# The URI has http:// et al stuffed onto the URL.
	def uri(self, page, view = None, viewparams = None):
		view = self._getview(page, view)
		return self.web.uri_from_path(page.path, self, view,
					      viewparams)

	# These return URLs and URIs in the default view for the page,
	# not the current view.
	def nurl(self, page):
		return self.web.url_from_path(page.path)
	def nuri(self, page):
		return self.web.uri_from_path(page.path, self)

	# This too is a context service.
	# The complication is virtual pages, which I have not solved.
	# NNGH.
	def pref_view(self, page):
		if page.type == "dir":
			return self.web.prefDirView(page)
		else:
			return None
	def pref_view_and_dir(self, page):
		if page.type == "dir":
			return self.web.pref_view_and_dir(page)
		else:
			return (None, None)

	# I think that use of the cache is good enough, but we'll see
	# if we need this information in cloned contexts.
	def cache_page_children(self, page):
		rp = page.path
		res = self.getcache(("pagekids", rp))
		if res is not None:
			return res
		res = self._get_disk_cpc(page)
		if res is not None:
			# If the general disk cache hit, we must load
			# the in-memory cache.
			self.setcache(("pagekids", rp), res)
			return res
		# Full miss. Go to all the work.
		# 
		# descendants() may return an iterator, which is
		# absolutely no good to cache. So we must list-ize
		# it, no matter how annoying that is.
		res = list(page.descendants(self))
		# To be sure we sort it before we store it.
		utils.sort_timelist(res)
		self.setcache(("pagekids", rp), res)
		self._set_disk_cpc(page, res)
		return res

	# Get and store page descendent lists in the generator disk cache,
	# because they are time-consuming to compute. This is kind of a
	# hack; see comments in pageranges.py for a similar case. Maybe
	# I should merge them?
	def _get_disk_cpc(self, page):
		if not rendcache.cache_on(self.cfg) or page.virtual() or \
		   page.type != "dir":
			return None
		return rendcache.fetch_gen(self, page.path, "page-kids")
	# TODO: is this validator good enough? Probably.
	def _set_disk_cpc(self, page, plist):
		if not rendcache.cache_on(self.cfg) or page.virtual() or \
		   page.type != "dir":
			return
		v = rendcache.Validator()
		v.add_mtime(page)
		ds = {page.path: True}
		# note that Storage .children() (and thus .descendants()
		# et al) never returns directories. This is a bit
		# regrettable.
		for ts, ppath in plist:
			pdir = utils.parent_path(ppath)
			if pdir in ds:
				continue
			ds[pdir] = True
			v.add_mtime(self.model.get_page(pdir))
		rendcache.store_gen(self, "page-kids", page.path, plist, v)
